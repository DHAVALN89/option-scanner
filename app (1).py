import os, time, gzip, json
from datetime import date, datetime, timedelta
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor

import requests
import pandas as pd
import streamlit as st

st.set_page_config(page_title="F&O Scanner", layout="wide")
pd.set_option("styler.render.max_elements", 20_000_000)
BASE = "https://api.upstox.com/v2"
INDEXES = {  # name: (instrument_key, exchange)
    "NIFTY": ("NSE_INDEX|Nifty 50", "NSE"),
    "BANKNIFTY": ("NSE_INDEX|Nifty Bank", "NSE"),
    "FINNIFTY": ("NSE_INDEX|Nifty Fin Service", "NSE"),
    "MIDCPNIFTY": ("NSE_INDEX|NIFTY MID SELECT", "NSE"),
    "SENSEX": ("BSE_INDEX|SENSEX", "BSE"),
}

# ---------------- data loading ----------------
@st.cache_data(ttl=6 * 3600, show_spinner="Loading contract list...")
def load_fo(exch):
    url = f"https://assets.upstox.com/market-quote/instruments/exchange/{exch}.json.gz"
    raw = gzip.decompress(requests.get(url, timeout=120).content)
    df = pd.DataFrame(json.loads(raw))
    df = df[(df["segment"] == f"{exch}_FO") & df["instrument_type"].isin(["CE", "PE"])]
    df = df[["instrument_key", "trading_symbol", "underlying_symbol", "underlying_key",
             "underlying_type", "expiry", "lot_size"]].copy()
    df["expiry"] = pd.to_datetime(df["expiry"], unit="ms").dt.strftime("%Y-%m-%d")
    return df[df["expiry"] >= str(date.today())]

# ---------------- sidebar ----------------
st.sidebar.header("Settings")
token = st.sidebar.text_input("Upstox access token", type="password",
                              value=os.environ.get("UPSTOX_TOKEN", ""))
if not token:
    st.title("📈 F&O Scanner")
    st.info("Open the sidebar (>) and paste today's Upstox access token.")
    st.stop()
hdr = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

mode = st.sidebar.radio("Choose", ["Index", "Stock", "Both"], horizontal=True)
nse = load_fo("NSE")
stock_list = sorted(nse[nse["underlying_type"] == "EQUITY"]["underlying_symbol"].unique())

targets = {}  # name -> (key, exch, kind)
if mode in ("Index", "Both"):
    for n in st.sidebar.multiselect("Indexes", list(INDEXES), default=["NIFTY", "SENSEX"]):
        targets[n] = (INDEXES[n][0], INDEXES[n][1], "Index")
if mode in ("Stock", "Both"):
    all_st = st.sidebar.checkbox(f"All {len(stock_list)} F&O stocks", False)
    chosen = stock_list if all_st else st.sidebar.multiselect(
        "Stocks", stock_list, default=["RELIANCE", "HDFCBANK"])
    for s in chosen:
        k = nse[nse["underlying_symbol"] == s]["underlying_key"].iloc[0]
        targets[s] = (k, "NSE", "Stock")
if not targets:
    st.warning("Select at least one index or stock.")
    st.stop()

meta_parts = []
for n, (k, ex, kind) in targets.items():
    d = load_fo(ex)
    meta_parts.append(d[d["underlying_symbol"] == n].assign(und=n, kind=kind, exch=ex))
meta = pd.concat(meta_parts)
all_exp = sorted(meta["expiry"].unique())
exp_mode = st.sidebar.radio("Expiry", ["Nearest", "Pick dates"], horizontal=True)
if exp_mode == "Pick dates":
    pick = st.sidebar.multiselect("Expiry dates", all_exp, default=all_exp[:1])
else:
    pick = None

st.sidebar.subheader("Ranking")
rank = st.sidebar.selectbox("Slicer / rank by", [
    "Top Value", "Highest OI", "Volume spike", "Volume", "OI change", "OI change %",
    "% Gainers", "% Losers"])
top_n = st.sidebar.number_input("How many contracts", 10, 20000, 500, 100)
opt_type = st.sidebar.radio("Type", ["Both", "CE", "PE"], horizontal=True)
min_vol = st.sidebar.number_input("Min volume", 0, value=0, step=1000)
spike_days = st.sidebar.selectbox("Spike vs avg of past days", [2, 5, 10])
scan_k = st.sidebar.number_input("Spike scan: top-by-volume contracts", 50, 2000, 300, 50)
auto = st.sidebar.checkbox("Auto refresh", True)
secs = st.sidebar.slider("Refresh seconds", 5, 120, 30)
st.sidebar.caption("Many stocks = many API calls. Use 60s+ refresh with 'All stocks'.")
if st.sidebar.button("Reset IV baseline"):
    st.session_state.pop("iv0", None)

# ---------------- API ----------------
def get(path, params=None):
    for _ in range(3):
        r = requests.get(BASE + path, headers=hdr, params=params, timeout=20)
        if r.status_code == 429:
            time.sleep(1.5); continue
        r.raise_for_status()
        return r.json().get("data") or []
    return []

def tv_symbol(und, exch, expiry, strike, side):
    d = datetime.strptime(expiry, "%Y-%m-%d").strftime("%y%m%d")
    return f"{exch}:{und}{d}{'C' if side == 'CE' else 'P'}{strike:g}"

def buildup(chg, oichg):
    if oichg > 0 and chg > 0: return "Long Buildup"
    if oichg > 0 and chg < 0: return "Short Buildup"
    if oichg < 0 and chg > 0: return "Short Covering"
    if oichg < 0 and chg < 0: return "Long Unwinding"
    return "-"

def snapshot(und):
    key, exch, kind = targets[und]
    m = meta[meta["und"] == und]
    exps = sorted(m["expiry"].unique())
    use = [e for e in exps if e in pick] if pick is not None else exps[:1]
    lookup = m.set_index("instrument_key")[["trading_symbol", "lot_size"]].to_dict("index")
    rows = []
    for ex in use:
        try:
            chain = get("/option/chain", {"instrument_key": key, "expiry_date": ex})
        except Exception:
            continue
        for s in chain:
            for side, k in (("CE", "call_options"), ("PE", "put_options")):
                o = s.get(k)
                if not o: continue
                md, gr = o.get("market_data", {}), o.get("option_greeks", {})
                ik = o["instrument_key"]
                info = lookup.get(ik, {})
                lot = info.get("lot_size") or 1
                ltp, close = md.get("ltp") or 0, md.get("close_price") or 0
                vol, oi, poi = md.get("volume") or 0, md.get("oi") or 0, md.get("prev_oi") or 0
                chg = round(ltp - close, 2)
                oichg = oi - poi
                rows.append({
                    "Symbol": info.get("trading_symbol", ik),
                    "Underlying": und, "Type": kind, "Expiry": ex,
                    "Strike": s["strike_price"], "CE/PE": side,
                    "LTP": ltp, "Chg": chg,
                    "%Chg": round(chg / close * 100, 2) if close else 0.0,
                    "Buildup": buildup(chg, oichg),
                    "Bid": md.get("bid_price") or 0, "Ask": md.get("ask_price") or 0,
                    "Volume": vol, "Value(Cr)": round(ltp * vol * lot / 1e7, 2),
                    "OI": oi, "OI Chg": oichg,
                    "OI Chg%": round(oichg / poi * 100, 2) if poi else 0.0,
                    "IV": round(gr.get("iv") or 0, 2),
                    "Chart": "https://www.tradingview.com/chart/?symbol=" +
                             quote(tv_symbol(und, "BSE" if exch == "BSE" else "NSE",
                                             ex, s["strike_price"], side), safe=""),
                    "Key": ik,
                })
    return rows

@st.cache_data(ttl=3600, show_spinner=False)
def past_avg_vol(key, days):
    to, frm = date.today(), date.today() - timedelta(days=days * 2 + 6)
    try:
        c = get(f"/historical-candle/{quote(key, safe='')}/day/{to}/{frm}")
        c = c["candles"] if isinstance(c, dict) else c
    except Exception:
        return None
    c = [x for x in c if x[0][:10] < str(date.today())][:days]
    return sum(x[5] for x in c) / len(c) if c else None

# ---------------- build table ----------------
st.title("📈 Live F&O Scanner")
try:
    with ThreadPoolExecutor(8) as pool:
        res = list(pool.map(snapshot, list(targets)))
except requests.HTTPError as e:
    st.error(f"Upstox error: {e}. Token expired or invalid?")
    st.stop()

df = pd.DataFrame([r for x in res for r in x])
if df.empty:
    st.warning("No data (market closed or no contracts for chosen expiry).")
    st.stop()

iv0 = st.session_state.setdefault("iv0", dict(zip(df["Key"], df["IV"])))
df["IV Chg"] = (df["IV"] - df["Key"].map(iv0).fillna(df["IV"])).round(2)
pv = st.session_state.get("prev_vol", {})
df["Vol Δ"] = df["Volume"] - df["Key"].map(pv).fillna(df["Volume"])
st.session_state["prev_vol"] = dict(zip(df["Key"], df["Volume"]))

if opt_type != "Both":
    df = df[df["CE/PE"] == opt_type]
df = df[df["Volume"] >= min_vol]

spike_col = f"Spike x({spike_days}d)"
if rank == "Volume spike":
    df = df.sort_values("Volume", ascending=False).head(int(scan_k)).copy()
    with ThreadPoolExecutor(8) as pool:
        avg = list(pool.map(lambda k: past_avg_vol(k, spike_days), df["Key"]))
    df[spike_col] = [round(v / a, 2) if a else None for v, a in zip(df["Volume"], avg)]

sorters = {"Top Value": ("Value(Cr)", False), "Highest OI": ("OI", False),
           "Volume spike": (spike_col, False), "Volume": ("Volume", False),
           "OI change": ("OI Chg", False), "OI change %": ("OI Chg%", False),
           "% Gainers": ("%Chg", False), "% Losers": ("%Chg", True)}
col, asc = sorters[rank]
df = df.sort_values(col, ascending=asc, na_position="last").head(int(top_n)).reset_index(drop=True)
df.index += 1

st.caption(f"{len(df)} contracts | {time.strftime('%H:%M:%S')} | tap 📈 to open TradingView | "
           "tap headers to sort | IV Chg vs first load | Vol Δ since last refresh")

# ---------------- styling ----------------
def color_num(v):
    if isinstance(v, (int, float)):
        if v > 0: return "background-color:#14532d;color:#bbf7d0"
        if v < 0: return "background-color:#7f1d1d;color:#fecaca"
    return ""

BU = {"Long Buildup": "#14532d", "Short Covering": "#166534",
      "Short Buildup": "#7f1d1d", "Long Unwinding": "#991b1b"}
def color_bu(v):
    return f"background-color:{BU[v]};color:white" if v in BU else ""

show = df.drop(columns=["Key"])
sty = show.style
mapper = getattr(sty, "map", None) or sty.applymap
sty = mapper(color_num, subset=["Chg", "%Chg", "OI Chg", "OI Chg%", "IV Chg"])
sty = (getattr(sty, "map", None) or sty.applymap)(color_bu, subset=["Buildup"])

st.dataframe(
    sty, use_container_width=True, height=700,
    column_config={
        "Chart": st.column_config.LinkColumn("Chart", display_text="📈"),
        "LTP": st.column_config.NumberColumn(format="%.2f"),
        "%Chg": st.column_config.NumberColumn(format="%.2f%%"),
        "OI Chg%": st.column_config.NumberColumn(format="%.2f%%"),
    })

if auto:
    time.sleep(secs)
    st.rerun()

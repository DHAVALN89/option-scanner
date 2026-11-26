import os, time, gzip, json, threading
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import requests
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="F&O Scanner", layout="wide")
st.markdown("""<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
html, body, .stApp {font-family:'Inter',sans-serif;background:#ffffff;color:#111827;}
.block-container{padding-top:1rem;padding-bottom:1rem;max-width:100%;}
section[data-testid="stSidebar"]{background:#f8fafc;border-right:1px solid #e2e8f0;}
section[data-testid="stSidebar"] label, section[data-testid="stSidebar"] p,
section[data-testid="stSidebar"] h1, section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3 {color:#111827 !important;}
.hero{background:linear-gradient(135deg,#0ea5e9 0%,#6366f1 60%,#a855f7 100%);
 border-radius:16px;padding:14px 18px 6px;margin-bottom:8px;color:#fff;}
.hero h1{margin:0;font-size:1.5rem;font-weight:700;letter-spacing:-.3px;color:#fff;}
.hero p{margin:2px 0 6px;font-size:.8rem;opacity:.9;color:#fff;}
.kpis{display:flex;flex-wrap:wrap;gap:8px;margin:6px 0 10px;}
.kpi{flex:1 1 calc(50% - 8px);min-width:140px;background:#fff;border:1px solid #e2e8f0;
 border-radius:12px;padding:8px 12px;box-shadow:0 1px 2px rgba(0,0,0,.04);}
.kpi small{color:#64748b;font-size:.7rem;text-transform:uppercase;letter-spacing:.6px;}
.kpi b{display:block;font-size:1.15rem;font-weight:700;color:#111827;}
.kpi b.g{color:#16a34a}.kpi b.r{color:#dc2626}
.movers{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:8px;margin-bottom:10px;}
.mv{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:8px 10px;}
.mv h4{margin:0 0 4px;font-size:.78rem;color:#334155;font-weight:700;}
.mv table{width:100%;border-collapse:collapse;font-size:.82rem;}
.mv td{padding:2px 0;color:#111827;} .mv td:last-child{text-align:right;font-weight:600;}
.mv td.g{color:#16a34a}.mv td.r{color:#dc2626}
[data-testid="stCaptionContainer"]{color:#475569;}
div[data-testid="stExpander"]{border:1px solid #e2e8f0;border-radius:12px;}
</style>""", unsafe_allow_html=True)
pd.set_option("styler.render.max_elements", 20_000_000)
BASE = "https://api.upstox.com"
TOKEN_FILE = "/tmp/upstox_token.json"
IST = timezone(timedelta(hours=5, minutes=30))
INDEXES = {
    "NIFTY": ("NSE_INDEX|Nifty 50", "NSE"),
    "BANKNIFTY": ("NSE_INDEX|Nifty Bank", "NSE"),
    "FINNIFTY": ("NSE_INDEX|Nifty Fin Service", "NSE"),
    "MIDCPNIFTY": ("NSE_INDEX|NIFTY MID SELECT", "NSE"),
    "SENSEX": ("BSE_INDEX|SENSEX", "BSE"),
}

# Sector map (edit freely; unknown symbols show "Other")
_SECT = {
 "Banking": "HDFCBANK ICICIBANK SBIN KOTAKBANK AXISBANK INDUSINDBK BANKBARODA PNB FEDERALBNK IDFCFIRSTB AUBANK BANDHANBNK CANBK RBLBANK BANKINDIA UNIONBANK YESBANK INDIANB",
 "Financials": "BAJFINANCE BAJAJFINSV HDFCLIFE SBILIFE ICICIPRULI ICICIGI LICHSGFIN CHOLAFIN MUTHOOTFIN MANAPPURAM SHRIRAMFIN PFC RECLTD SBICARD HDFCAMC LICI JIOFIN ANGELONE CDSL BSE MCX IIFL LTF M&MFIN POONAWALLA IRFC NUVAMA PNBHOUSING KFINTECH PAYTM POLICYBZR SAMMAANCAP",
 "IT": "TCS INFY WIPRO HCLTECH TECHM LTIM MPHASIS COFORGE PERSISTENT OFSS KPITTECH TATAELXSI TATATECH CYIENT",
 "Auto": "MARUTI M&M TATAMOTORS BAJAJ-AUTO HEROMOTOCO EICHERMOT TVSMOTOR ASHOKLEY BHARATFORG BOSCHLTD MOTHERSON BALKRISIND APOLLOTYRE MRF EXIDEIND SONACOMS TIINDIA UNOMINDA",
 "Pharma/Health": "SUNPHARMA DRREDDY CIPLA DIVISLAB LUPIN AUROPHARMA TORNTPHARM ZYDUSLIFE ALKEM BIOCON GLENMARK LAURUS GRANULES IPCALAB MANKIND APOLLOHOSP MAXHEALTH FORTIS SYNGENE LALPATHLAB PPLPHARMA",
 "FMCG": "HINDUNILVR ITC NESTLEIND BRITANNIA DABUR GODREJCP MARICO COLPAL TATACONSUM UBL VBL PATANJALI MCDOWELL-N RADICO",
 "Metals/Mining": "TATASTEEL JSWSTEEL HINDALCO VEDL JINDALSTEL SAIL NMDC NATIONALUM HINDZINC APLAPOLLO JSL COALINDIA",
 "Oil & Gas": "RELIANCE ONGC BPCL IOC GAIL HINDPETRO PETRONET OIL IGL MGL GUJGASLTD",
 "Power": "NTPC POWERGRID TATAPOWER ADANIGREEN ADANIENSOL ADANIPOWER NHPC JSWENERGY TORNTPOWER SJVN SUZLON INOXWIND WAAREEENER PREMIERENE",
 "Capital Goods/Infra": "LT SIEMENS ABB BHEL HAL BEL CGPOWER POLYCAB HAVELLS CUMMINSIND KEI DIXON BLUESTARCO VOLTAS KAYNES RVNL IRCON NBCC IRCTC INOXWIND ASTRAL SUPREMEIND",
 "Cement": "ULTRACEMCO GRASIM AMBUJACEM SHREECEM DALBHARAT ACC",
 "Telecom": "BHARTIARTL IDEA INDUSTOWER TATACOMM",
 "Consumer/Retail": "TITAN ASIANPAINT BERGEPAINT PIDILITIND TRENT DMART JUBLFOOD PAGEIND KALYANKJIL NYKAA ETERNAL ZOMATO SWIGGY INDHOTEL NAUKRI CROMPTON",
 "Realty": "DLF GODREJPROP OBEROIRLTY PRESTIGE PHOENIXLTD LODHA",
 "Chemicals": "UPL PIIND SRF DEEPAKNTR NAVINFLUOR TATACHEM COROMANDEL CHAMBLFERT SOLARINDS",
 "Logistics/Aviation": "INDIGO ADANIPORTS CONCOR DELHIVERY",
 "Conglomerate": "ADANIENT",
}
SECTOR = {s: k for k, v in _SECT.items() for s in v.split()}

# ---------- token ----------
def saved_token():
    try:
        t = st.secrets.get("UPSTOX_TOKEN", "")
        if t: return t
    except Exception:
        pass
    if os.environ.get("UPSTOX_TOKEN"): return os.environ["UPSTOX_TOKEN"]
    try:
        d = json.load(open(TOKEN_FILE))
        if d.get("date") == str(date.today()): return d["token"]
    except Exception:
        pass
    return ""

def save_token(t):
    try: json.dump({"date": str(date.today()), "token": t}, open(TOKEN_FILE, "w"))
    except Exception: pass

# ---------- contract list ----------
@st.cache_data(ttl=6 * 3600, show_spinner="Loading contract list (first time only)...")
def load_fo(exch):
    url = f"https://assets.upstox.com/market-quote/instruments/exchange/{exch}.json.gz"
    raw = gzip.decompress(requests.get(url, timeout=120).content)
    df = pd.DataFrame(json.loads(raw))
    df = df[(df["segment"] == f"{exch}_FO") & df["instrument_type"].isin(["CE", "PE"])]
    df = df[["instrument_key", "trading_symbol", "underlying_symbol", "underlying_key",
             "underlying_type", "expiry", "lot_size"]].copy()
    df["expiry"] = pd.to_datetime(df["expiry"], unit="ms").dt.strftime("%Y-%m-%d")
    return df[df["expiry"] >= str(date.today())]

# ---------- sidebar ----------
st.sidebar.header("Settings")
token = st.sidebar.text_input("Upstox token (Analytics token = no daily login)",
                              type="password", value=saved_token())
if not token:
    st.title("📈 F&O Scanner")
    st.info("Paste your Upstox **Analytics Token** in the sidebar (valid 1 year). "
            "Better: save it once in Streamlit → Settings → Secrets as UPSTOX_TOKEN.")
    st.stop()
if token != saved_token():
    save_token(token)
hdr = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

mode = st.sidebar.radio("Choose", ["Index", "Stock", "Both"], horizontal=True, index=2)
nse = load_fo("NSE")
stock_list = sorted(nse[nse["underlying_type"] == "EQUITY"]["underlying_symbol"].unique())
stock_keys = nse.drop_duplicates("underlying_symbol").set_index("underlying_symbol")["underlying_key"].to_dict()

targets = {}
if mode in ("Index", "Both"):
    for n in st.sidebar.multiselect("Indexes", list(INDEXES), default=["NIFTY", "SENSEX"]):
        targets[n] = (INDEXES[n][0], INDEXES[n][1], "Index")
if mode in ("Stock", "Both"):
    all_st = st.sidebar.checkbox(f"All {len(stock_list)} F&O stocks", False)
    chosen = stock_list if all_st else st.sidebar.multiselect(
        "Stocks", stock_list, default=["RELIANCE", "HDFCBANK"])
    for s in chosen:
        targets[s] = (stock_keys[s], "NSE", "Stock")
if not targets:
    st.warning("Select at least one index or stock."); st.stop()

parts = []
for ex in {v[1] for v in targets.values()}:
    d = load_fo(ex)
    parts.append(d[d["underlying_symbol"].isin([n for n, v in targets.items() if v[1] == ex])])
meta = pd.concat(parts)
meta_by_und = {k: g for k, g in meta.groupby("underlying_symbol")}
all_exp = sorted(meta["expiry"].unique())

exp_mode = st.sidebar.radio("Expiry", ["Nearest", "Pick dates"], horizontal=True)
pick = tuple(st.sidebar.multiselect("Expiry dates", all_exp, default=all_exp[:1])) \
    if exp_mode == "Pick dates" else None
sectors = st.sidebar.multiselect("Sector filter (stocks)", sorted(set(SECTOR.values())) + ["Other"])

st.sidebar.subheader("Ranking")
rank = st.sidebar.selectbox("Slicer / rank by", [
    "Top Value", "Highest OI", "Volume spike", "Volume", "OI change", "OI change %",
    "% Gainers", "% Losers"])
top_n = st.sidebar.number_input("How many contracts", 10, 20000, 500, 100)
opt_type = st.sidebar.radio("Type", ["Both", "CE", "PE"], horizontal=True)
min_vol = st.sidebar.number_input("Min volume", 0, value=0, step=1000)

st.sidebar.subheader("Optional columns (⏱ = extra API calls)")
use_spike = st.sidebar.checkbox("⏱⏱ Volume spike (slowest)", False) or rank == "Volume spike"
spike_days = st.sidebar.selectbox("Spike vs avg of past days", [2, 5, 10])
scan_k = st.sidebar.number_input("Spike: top-by-volume contracts", 50, 2000, 150, 50)
use_or = st.sidebar.checkbox("⏱ Opening candle high/low + break", False)
or_min = st.sidebar.selectbox("Opening candle minutes (from 9:15)", [5, 15, 30], index=1)

auto = st.sidebar.checkbox("Auto refresh", True)
secs = st.sidebar.slider("Refresh seconds", 5, 120, 15)
if st.sidebar.button("Reset IV baseline"):
    st.session_state.pop("iv0", None)

# ---------- API ----------
_lock, _last = threading.Lock(), [0.0]
def throttle(gap=0.03):
    with _lock:
        t = max(time.time(), _last[0] + gap); _last[0] = t
    w = t - time.time()
    if w > 0: time.sleep(w)

SESSION = requests.Session()
SESSION.mount("https://", requests.adapters.HTTPAdapter(pool_connections=32, pool_maxsize=32))

def get(path, params=None):
    for _ in range(3):
        throttle()
        r = SESSION.get(BASE + path, headers=hdr, params=params, timeout=15)
        if r.status_code == 429:
            time.sleep(1.0); continue
        r.raise_for_status()
        return r.json().get("data") or []
    raise RuntimeError("Rate limited by Upstox (429). Increase refresh seconds.")

def tv_symbol(und, exch, expiry, strike, side):
    d = datetime.strptime(expiry, "%Y-%m-%d").strftime("%y%m%d")
    return f"{exch}:{und}{d}{'C' if side == 'CE' else 'P'}{strike:g}"

def buildup(chg, oichg):
    if oichg > 0 and chg > 0: return "Long Buildup"
    if oichg > 0 and chg < 0: return "Short Buildup"
    if oichg < 0 and chg > 0: return "Short Covering"
    if oichg < 0 and chg < 0: return "Long Unwinding"
    return "-"

def snapshot(und, pick_):
    key, exch, kind = targets[und]
    m = meta_by_und.get(und)
    if m is None: return [], []
    exps = sorted(m["expiry"].unique())
    use = [e for e in exps if e in pick_] if pick_ is not None else exps[:1]
    lookup = m.set_index("instrument_key")[["trading_symbol", "lot_size"]].to_dict("index")
    rows, errs = [], []
    for ex in use:
        try:
            chain = get("/v2/option/chain", {"instrument_key": key, "expiry_date": ex})
        except Exception as e:
            errs.append(f"{und} {ex}: {e}"); continue
        for s in chain:
            spot = s.get("underlying_spot_price") or 0
            for side, k in (("CE", "call_options"), ("PE", "put_options")):
                o = s.get(k)
                if not o: continue
                md, gr = o.get("market_data", {}), o.get("option_greeks", {})
                ik = o["instrument_key"]
                info = lookup.get(ik, {})
                lot = info.get("lot_size") or 1
                ltp, close = md.get("ltp") or 0, md.get("close_price") or 0
                vol, oi, poi = md.get("volume") or 0, md.get("oi") or 0, md.get("prev_oi") or 0
                chg, oichg = round(ltp - close, 2), oi - poi
                gex = (1 if side == "CE" else -1) * (gr.get("gamma") or 0) * oi * lot * spot * spot * 0.01 / 1e7
                rows.append({
                    "Symbol": info.get("trading_symbol", ik), "Underlying": und, "Type": kind,
                    "Expiry": ex, "Strike": s["strike_price"], "CE/PE": side,
                    "LTP": ltp, "Chg": chg,
                    "%Chg": round(chg / close * 100, 2) if close else 0.0,
                    "Buildup": buildup(chg, oichg),
                    "Bid": md.get("bid_price") or 0, "Ask": md.get("ask_price") or 0,
                    "Volume": vol, "Value(Cr)": round(ltp * vol * lot / 1e7, 2),
                    "OI": oi, "OI Chg": oichg,
                    "OI Chg%": round(oichg / poi * 100, 2) if poi else 0.0,
                    "IV": round(gr.get("iv") or 0, 2), "GEX(Cr)": round(gex, 2), "Spot": spot,
                    "Chart": "https://www.tradingview.com/chart/?symbol=" + quote(
                        tv_symbol(und, exch, ex, s["strike_price"], side), safe=""),
                    "Key": ik,
                })
    return rows, errs

@st.cache_data(ttl=8, max_entries=3, show_spinner=False)
def fetch_all(token_, unds, pick_):
    t0 = time.time()
    with ThreadPoolExecutor(16) as pool:
        res = list(pool.map(lambda u: snapshot(u, pick_), unds))
    return (pd.DataFrame([r for x, _ in res for r in x]),
            [e for _, x in res for e in x], round(time.time() - t0, 1))

@st.cache_data(ttl=8, max_entries=3, show_spinner=False)
def sym_quotes(token_, keys):
    out = {}
    for i in range(0, len(keys), 400):
        for v in get("/v2/market-quote/quotes", {"instrument_key": ",".join(keys[i:i + 400])}).values():
            lp, nc = v.get("last_price") or 0, v.get("net_change") or 0
            prev = lp - nc
            out[v.get("instrument_token")] = (lp, round(nc / prev * 100, 2) if prev else 0.0)
    return out

@st.cache_data(ttl=8 * 3600, show_spinner=False, max_entries=5000)
def open_range(key, day, n, bucket):
    d = get(f"/v3/historical-candle/intraday/{quote(key, safe='')}/minutes/1")
    c = d["candles"] if isinstance(d, dict) else d
    hi = lo = None
    for x in c:
        ts = x[0]
        if ts[:10] != day: continue
        m = int(ts[11:13]) * 60 + int(ts[14:16]) - 555
        if 0 <= m < n:
            hi = x[2] if hi is None else max(hi, x[2])
            lo = x[3] if lo is None else min(lo, x[3])
    return hi, lo

@st.cache_data(ttl=3600, show_spinner=False, max_entries=20000)
def past_avg_vol(key, days):
    to, frm = date.today(), date.today() - timedelta(days=days * 2 + 6)
    d = get(f"/v3/historical-candle/{quote(key, safe='')}/days/1/{to}/{frm}")
    c = d["candles"] if isinstance(d, dict) else d
    c = [x for x in c if x[0][:10] < str(date.today())][:days]
    return sum(x[5] for x in c) / len(c) if c else None

def safe(fn, *a):
    try: return fn(*a)
    except Exception: return None

# ---------- header with live IST clock ----------
st.markdown('<div class="hero"><h1>📈 Live F&O Scanner</h1><p>Options chain · OI · Volume · PCR · Walls · GEX · Opening range</p></div>', unsafe_allow_html=True)
components.html("""
<div id="c" style="font:700 20px sans-serif;color:#2563eb"></div>
<script>
function t(){document.getElementById('c').textContent='🕐 '+new Date().toLocaleTimeString('en-IN',
{timeZone:'Asia/Kolkata',hour12:false})+' IST  |  '+new Date().toLocaleDateString('en-IN',
{timeZone:'Asia/Kolkata',weekday:'short',day:'2-digit',month:'short'})}
t();setInterval(t,1000);
</script>""", height=36)

# ---------- styling ----------
def color_num(v):
    if isinstance(v, (int, float)) and v == v:
        if v > 0: return "background-color:#dcfce7;color:#166534"
        if v < 0: return "background-color:#fee2e2;color:#991b1b"
    return ""
BU = {"Long Buildup": ("#bbf7d0", "#14532d"), "Short Covering": ("#dcfce7", "#166534"),
      "High Break": ("#bbf7d0", "#14532d"), "Short Buildup": ("#fecaca", "#7f1d1d"),
      "Long Unwinding": ("#fee2e2", "#991b1b"), "Low Break": ("#fecaca", "#7f1d1d")}
def color_bu(v):
    return f"background-color:{BU[v][0]};color:{BU[v][1]}" if v in BU else ""

F2 = ["Sym LTP", "Sym %Chg", "LTP", "Chg", "%Chg", "Bid", "Ask", "Value(Cr)", "OI Chg%", "IV", "IV Chg",
      "PCR", "GEX(Cr)", "Net GEX(Cr)", "OR High", "OR Low"]
F0 = ["Volume", "Vol Δ", "OI", "OI Chg"]
FG = ["Strike", "Call Wall", "Put Wall"]
ORDER = ["Symbol", "Underlying", "Sector", "Sym LTP", "Sym %Chg", "Type", "Expiry", "Strike", "CE/PE",
         "LTP", "Chg", "%Chg", "Buildup", "Bid", "Ask", "Volume", "Vol Δ", "Spike", "Value(Cr)",
         "OI", "OI Chg", "OI Chg%", "IV", "IV Chg", "PCR", "Call Wall", "Put Wall",
         "GEX(Cr)", "Net GEX(Cr)", "OR High", "OR Low", "OR Break", "Chart"]
cols_show = st.sidebar.multiselect("Columns to show", ORDER, default=ORDER)
SORT = {"Top Value": ("Value(Cr)", False), "Highest OI": ("OI", False), "Volume spike": ("Spike", False),
        "Volume": ("Volume", False), "OI change": ("OI Chg", False), "OI change %": ("OI Chg%", False),
        "% Gainers": ("%Chg", False), "% Losers": ("%Chg", True)}

@st.fragment(run_every=secs if auto else None)
def board():
    T = {}
    df, errs, T["options"] = fetch_all(token, tuple(targets), pick)
    if df.empty:
        msg = errs[0] if errs else "No contracts for chosen expiry / market closed."
        if any("401" in e for e in errs):
            msg = "Token invalid/expired. Paste a fresh token (or the 1-year Analytics token)."
        st.warning(f"No data. {msg}"); return

    # underlying-level metrics (before filters)
    idx = pd.MultiIndex.from_frame(df[["Underlying", "Expiry"]])
    tot = df.pivot_table(index=["Underlying", "Expiry"], columns="CE/PE", values="OI", aggfunc="sum").fillna(0)
    for c_ in ("CE", "PE"):
        if c_ not in tot: tot[c_] = 0
    pcr = (tot["PE"] / tot["CE"].replace(0, np.nan)).round(2)
    df["PCR"] = pcr.reindex(idx).values
    for side, name in (("CE", "Call Wall"), ("PE", "Put Wall")):
        sub = df[df["CE/PE"] == side]
        w = sub.loc[sub.groupby(["Underlying", "Expiry"])["OI"].idxmax()].set_index(["Underlying", "Expiry"])["Strike"]
        df[name] = w.reindex(idx).values
    df["Net GEX(Cr)"] = df.groupby(["Underlying", "Expiry"])["GEX(Cr)"].transform("sum").round(2)

    # symbol quotes
    t0 = time.time()
    q = safe(sym_quotes, token, tuple(v[0] for v in targets.values())) or {}
    kmap = {u: v[0] for u, v in targets.items()}
    df["Sym LTP"] = [round(q[kmap[u]][0] if kmap[u] in q else sp, 2) for u, sp in zip(df["Underlying"], df["Spot"])]
    df["Sym %Chg"] = [q[kmap[u]][1] if kmap[u] in q else 0.0 for u in df["Underlying"]]
    df["Sector"] = [("Index" if targets[u][2] == "Index" else SECTOR.get(u, "Other")) for u in df["Underlying"]]
    T["symbol quotes"] = round(time.time() - t0, 1)
    uni = df.drop_duplicates("Underlying")[["Underlying", "Sector", "Sym %Chg"]]

    # opening candle
    now = datetime.now(IST); nowm = now.hour * 60 + now.minute
    complete = nowm >= 555 + or_min
    df["OR High"] = np.nan; df["OR Low"] = np.nan; df["OR Break"] = "-"
    if use_or:
        t0 = time.time()
        day = now.strftime("%Y-%m-%d"); bucket = 0 if complete else nowm
        with ThreadPoolExecutor(16) as pool:
            r = list(pool.map(lambda u: safe(open_range, kmap[u], day, or_min, bucket), list(targets)))
        orr = {u: (x if x else (None, None)) for u, x in zip(targets, r)}
        df["OR High"] = df["Underlying"].map(lambda u: orr[u][0])
        df["OR Low"] = df["Underlying"].map(lambda u: orr[u][1])
        df["OR High"] = pd.to_numeric(df["OR High"], errors="coerce")
        df["OR Low"] = pd.to_numeric(df["OR Low"], errors="coerce")
        def brk(l, h, lo):
            if h is None or h != h: return "-"
            if not complete: return "Forming"
            return "High Break" if l > h else "Low Break" if l < lo else "Inside"
        df["OR Break"] = [brk(l, h, lo) for l, h, lo in zip(df["Sym LTP"], df["OR High"], df["OR Low"])]
        T["opening candle"] = round(time.time() - t0, 1)

    iv0 = st.session_state.setdefault("iv0", dict(zip(df["Key"], df["IV"])))
    df["IV Chg"] = (df["IV"] - df["Key"].map(iv0).fillna(df["IV"])).round(2)
    pv = st.session_state.get("prev_vol", {})
    df["Vol Δ"] = df["Volume"] - df["Key"].map(pv).fillna(df["Volume"])
    st.session_state["prev_vol"] = dict(zip(df["Key"], df["Volume"]))

    if opt_type != "Both": df = df[df["CE/PE"] == opt_type]
    if sectors: df = df[df["Sector"].isin(sectors)]
    df = df[df["Volume"] >= min_vol]

    df["Spike"] = np.nan
    if use_spike:
        t0 = time.time()
        top = df.sort_values("Volume", ascending=False).head(int(scan_k))
        with ThreadPoolExecutor(16) as pool:
            avg = list(pool.map(lambda k: safe(past_avg_vol, k, spike_days), top["Key"]))
        df.loc[top.index, "Spike"] = [round(v / a, 2) if a else np.nan for v, a in zip(top["Volume"], avg)]
        T["volume spike"] = round(time.time() - t0, 1)

    col, asc = SORT[rank]
    df = df.sort_values(col, ascending=asc, na_position="last").head(int(top_n)).reset_index(drop=True)
    df.index += 1
    df = df.rename(columns={"Spike": f"Spike x({spike_days}d)"})
    off = set()
    if not use_spike: off.add("Spike")
    if not use_or: off |= {"OR High", "OR Low", "OR Break"}
    show_cols = [c if c != "Spike" else f"Spike x({spike_days}d)" for c in ORDER
                 if c in cols_show and c not in off]
    show = df[show_cols]
    f2 = F2 + [f"Spike x({spike_days}d)"]

    def big(x):
        return f"{x/1e7:.2f} Cr" if x >= 1e7 else f"{x/1e5:.2f} L" if x >= 1e5 else f"{x:,.0f}"
    c_oi, p_oi = tot["CE"].sum(), tot["PE"].sum()
    opcr = p_oi / c_oi if c_oi else 0
    up = int((df["%Chg"] > 0).sum()); dn = int((df["%Chg"] < 0).sum())
    st.markdown(
        '<div class="kpis">'
        f'<div class="kpi"><small>Contracts</small><b>{len(df):,}</b></div>'
        f'<div class="kpi"><small>Total PCR</small><b class="{"g" if opcr >= 1 else "r"}">{opcr:.2f}</b></div>'
        f'<div class="kpi"><small>Call OI</small><b class="r">{big(c_oi)}</b></div>'
        f'<div class="kpi"><small>Put OI</small><b class="g">{big(p_oi)}</b></div>'
        f'<div class="kpi"><small>Rising / Falling</small><b><span style="color:#4ade80">{up}</span> / '
        f'<span style="color:#f87171">{dn}</span></b></div>'
        '</div>', unsafe_allow_html=True)
    def mini(title, rows):
        body = "".join(f"<tr><td>{n}</td><td class='{'g' if v >= 0 else 'r'}'>{v:+.2f}%</td></tr>" for n, v in rows)
        return f"<div class='mv'><h4>{title}</h4><table>{body}</table></div>"
    boxes = [mini("🟢 Top 5 Gainers", uni.nlargest(5, "Sym %Chg")[["Underlying", "Sym %Chg"]].values),
             mini("🔴 Top 5 Losers", uni.nsmallest(5, "Sym %Chg")[["Underlying", "Sym %Chg"]].values)]
    sec = uni[~uni["Sector"].isin(["Index", "Other"])].groupby("Sector")["Sym %Chg"].mean().round(2)
    if len(sec):
        boxes += [mini("🟢 Sector Gainers", sec.nlargest(5).reset_index().values),
                  mini("🔴 Sector Losers", sec.nsmallest(5).reset_index().values)]
    st.markdown('<div class="movers">' + "".join(boxes) + '</div>', unsafe_allow_html=True)
    st.caption(f"✅ {datetime.now(IST).strftime('%H:%M:%S')} IST | {len(df)} contracts | "
               "time per part (s, 0 = cached): " + ", ".join(f"{k} {v}" for k, v in T.items())
               + (f" | ⚠️ {len(errs)} fetch errors" if errs else "")
               + " | tap 📈 for TradingView | IV Chg vs first load | GEX = call − put, ₹Cr per 1% move")
    cfg = {"Chart": st.column_config.LinkColumn("Chart", display_text="📈")}
    for c in show.columns:
        if c in f2: cfg[c] = st.column_config.NumberColumn(c, format="%.2f")
        elif c in F0: cfg[c] = st.column_config.NumberColumn(c, format="%d")
        elif c in FG: cfg[c] = st.column_config.NumberColumn(c, format="%g")
    cfg["%Chg"] = cfg.get("%Chg") and st.column_config.NumberColumn("%Chg", format="%.2f%%")
    cfg["Sym %Chg"] = cfg.get("Sym %Chg") and st.column_config.NumberColumn("Sym %Chg", format="%.2f%%")
    cfg["OI Chg%"] = cfg.get("OI Chg%") and st.column_config.NumberColumn("OI Chg%", format="%.2f%%")
    cfg = {k: v for k, v in cfg.items() if v}

    data = show
    if len(show) <= 3000:
        m = lambda s: getattr(s, "map", None) or s.applymap
        sty = show.style
        numc = [c for c in ["Chg", "%Chg", "OI Chg", "OI Chg%", "IV Chg", "Sym %Chg", "GEX(Cr)", "Net GEX(Cr)"] if c in show]
        if numc: sty = m(sty)(color_num, subset=numc)
        txtc = [c for c in ["Buildup", "OR Break"] if c in show]
        if txtc: sty = m(sty)(color_bu, subset=txtc)
        fmt = {c: "{:.2f}" for c in f2 if c in show}
        fmt.update({c: "{:,.0f}" for c in F0 if c in show})
        fmt.update({c: "{:g}" for c in FG if c in show})
        data = sty.format(fmt, na_rep="")
    st.dataframe(data, use_container_width=True, height=700, column_config=cfg)

board()

with st.expander("🔧 Test connection (tap if no data)"):
    if st.button("Run test"):
        nex = nse[nse["underlying_symbol"] == "NIFTY"]["expiry"].min()
        for name, path, params in [
            ("Nifty LTP", "/v3/market-quote/ltp", {"instrument_key": "NSE_INDEX|Nifty 50"}),
            (f"Nifty option chain {nex}", "/v2/option/chain",
             {"instrument_key": "NSE_INDEX|Nifty 50", "expiry_date": nex}),
        ]:
            try:
                r = requests.get(BASE + path, headers=hdr, params=params, timeout=20)
                st.write(f"**{name}** → HTTP {r.status_code}"); st.code(r.text[:500])
            except Exception as e:
                st.write(f"**{name}** → failed"); st.code(str(e))

import os, time, gzip, json, io
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor

import requests
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Option Scanner", layout="wide")
BASE = "https://api.upstox.com/v2"

INDEXES = {
    "NIFTY": "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
    "FINNIFTY": "NSE_INDEX|Nifty Fin Service",
    "MIDCPNIFTY": "NSE_INDEX|NIFTY MID SELECT",
    "SENSEX": "BSE_INDEX|SENSEX",
}

# ---------------- sidebar ----------------
st.sidebar.header("Settings")
token = st.sidebar.text_input("Upstox access token", type="password",
                              value=os.environ.get("UPSTOX_TOKEN", ""))
idx_sel = st.sidebar.multiselect("Indexes", list(INDEXES), default=["NIFTY", "SENSEX"])
stocks_txt = st.sidebar.text_input("Stocks (comma separated)", "RELIANCE,HDFCBANK")
n_exp = st.sidebar.slider("Nearest expiries per underlying", 1, 4, 1)
top_n = st.sidebar.slider("Top N contracts", 20, 200, 200, 10)
min_vol = st.sidebar.number_input("Min volume", 0, value=0, step=1000)
opt_type = st.sidebar.radio("Type", ["Both", "CE", "PE"], horizontal=True)
hist_days = st.sidebar.selectbox("Past days for volume multiple", [2, 5, 10], index=0)
use_hist = st.sidebar.checkbox("Calc past-days volume multiple (slower)", False)
auto = st.sidebar.checkbox("Auto refresh", True)
secs = st.sidebar.slider("Refresh seconds", 5, 60, 10)
if st.sidebar.button("Reset IV baseline"):
    st.session_state.pop("iv0", None)

hdr = {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def get(path, params=None):
    r = requests.get(BASE + path, headers=hdr, params=params, timeout=20)
    r.raise_for_status()
    return r.json().get("data") or []


@st.cache_data(ttl=3600)
def stock_keys():
    url = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
    raw = gzip.decompress(requests.get(url, timeout=60).content)
    df = pd.DataFrame(json.loads(raw))
    df = df[df["segment"] == "NSE_EQ"]
    return dict(zip(df["trading_symbol"], df["instrument_key"]))


@st.cache_data(ttl=3600)
def contracts(token_, ukey):
    return get("/option/contract", {"instrument_key": ukey})


def snapshot(label, ukey, kind):
    rows = []
    cons = contracts(token, ukey)
    meta = {c["instrument_key"]: c for c in cons}
    exps = sorted({c["expiry"][:10] if isinstance(c["expiry"], str) else
                   time.strftime("%Y-%m-%d", time.gmtime(c["expiry"] / 1000))
                   for c in cons if str(c["expiry"])[:10] >= str(date.today())})
    for ex in exps[:n_exp]:
        try:
            chain = get("/option/chain", {"instrument_key": ukey, "expiry_date": ex})
        except Exception:
            continue
        for s in chain:
            for side, k in (("CE", "call_options"), ("PE", "put_options")):
                o = s.get(k)
                if not o:
                    continue
                md, gr = o.get("market_data", {}), o.get("option_greeks", {})
                key = o["instrument_key"]
                m = meta.get(key, {})
                lot = m.get("lot_size") or 1
                ltp, close = md.get("ltp") or 0, md.get("close_price") or 0
                vol, oi, poi = md.get("volume") or 0, md.get("oi") or 0, md.get("prev_oi") or 0
                rows.append({
                    "Contract": f"{label} {ex} {s['strike_price']:g} {side}",
                    "Symbol": m.get("trading_symbol", key),
                    "Index/Stock": kind, "Underlying": label, "Expiry": ex,
                    "Strike": s["strike_price"], "Type": side, "Key": key,
                    "LTP": ltp, "Chg": round(ltp - close, 2),
                    "%Chg": round((ltp - close) / close * 100, 2) if close else 0,
                    "Volume": vol, "Value(Cr)": round(ltp * vol * lot / 1e7, 2),
                    "OI": oi, "OI Chg": oi - poi,
                    "OI Chg%": round((oi - poi) / poi * 100, 2) if poi else 0,
                    "IV": round(gr.get("iv") or 0, 2),
                })
    return rows


@st.cache_data(ttl=3600)
def past_vol(token_, key, days):
    to, frm = date.today(), date.today() - timedelta(days=days * 2 + 6)
    try:
        c = get(f"/historical-candle/{key}/day/{to}/{frm}", None)
        c = c["candles"] if isinstance(c, dict) else c
    except Exception:
        return None
    c = [x for x in c if x[0][:10] < str(date.today())][:days]
    return sum(x[5] for x in c) / len(c) if c else None


st.title("📈 Live Option Scanner")
if not token:
    st.info("Enter your Upstox access token in the sidebar (menu ☰). It expires daily.")
    st.stop()

targets = [(n, INDEXES[n], "Index") for n in idx_sel]
try:
    sk = stock_keys()
    for s in [x.strip().upper() for x in stocks_txt.split(",") if x.strip()]:
        if s in sk:
            targets.append((s, sk[s], "Stock"))
        else:
            st.warning(f"Stock not found: {s}")
    with ThreadPoolExecutor(6) as ex_:
        res = list(ex_.map(lambda t: snapshot(*t), targets))
except requests.HTTPError as e:
    st.error(f"Upstox error: {e}. Token expired or invalid?")
    st.stop()

df = pd.DataFrame([r for x in res for r in x])
if df.empty:
    st.warning("No data. Market closed or nothing selected.")
    st.stop()

# IV change vs baseline
iv0 = st.session_state.setdefault("iv0", dict(zip(df["Key"], df["IV"])))
df["IV Chg"] = (df["IV"] - df["Key"].map(iv0).fillna(df["IV"])).round(2)

if opt_type != "Both":
    df = df[df["Type"] == opt_type]
df = df[df["Volume"] >= min_vol]

sort_by = st.selectbox("Rank top contracts by",
                       ["Volume", "Value(Cr)", "OI", "OI Chg", "OI Chg%", "%Chg", "IV", "IV Chg"])
df = df.sort_values(sort_by, ascending=False).head(top_n)

if use_hist:
    with ThreadPoolExecutor(8) as ex_:
        avg = list(ex_.map(lambda k: past_vol(token, k, hist_days), df["Key"]))
    df[f"Avg Vol {hist_days}d"] = [round(a) if a else None for a in avg]
    df[f"Vol x ({hist_days}d)"] = [round(v / a, 2) if a else None
                                   for v, a in zip(df["Volume"], avg)]

st.caption(f"{len(df)} contracts | updated {time.strftime('%H:%M:%S')} | "
           "tap any column header to sort | IV Chg is vs baseline (first load)")
st.dataframe(df.drop(columns=["Key"]), use_container_width=True, height=650, hide_index=True)

if auto:
    time.sleep(secs)
    st.rerun()

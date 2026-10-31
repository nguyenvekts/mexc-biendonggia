import os, re, json, math, time, requests
from datetime import datetime, timezone, timedelta

TOKEN = os.environ["TG_TOKEN"]
CHAT_ID = os.environ["TG_CHAT_ID"]
CG_KEY = os.environ.get("CG_API_KEY", "")

MIN_PCT = 20.0
MIN_VOL_USDT = 1_000_000
QUIET_START = (0, 0)
QUIET_END = (5, 30)
STOCK_BASES = {"COIN", "HOOD", "NVDA", "AAPL", "AMZN", "GOOGL", "TSLA", "MCD"}

MAX_ENRICH = 8
CACHE_FILE = "cg_cache.json"
CACHE_TTL_H = 24
MISS_TTL_H = 6

BASE = "https://contract.mexc.com/api/v1/contract"
LINK = "https://www.mexc.com/vi-VN/futures/{sym}?_from=market"
CG = "https://api.coingecko.com/api/v3"
CG_DETAIL_PARAMS = dict(
    localization="false",
    tickers="true",
    market_data="true",
    community_data="false",
    developer_data="false",
    sparkline="false",
)

TAG_RULES = [
    (r"\bmeme", "Meme"),
    (r"artificial intelligence|\bAI\b", "AI"),
    (r"decentralized finance|\bdefi\b", "DeFi"),
    (r"layer 1|\bl1\b", "Layer 1"),
    (r"layer 2|\bl2\b", "Layer 2"),
    (r"gaming|gamefi|metaverse", "Game/Metaverse"),
    (r"real world assets|\brwa\b", "RWA"),
    (r"\bnft", "NFT"),
    (r"privacy", "Privacy"),
    (r"oracle", "Oracle"),
    (r"depin", "DePIN"),
    (r"liquid staking|restaking", "Staking"),
    (r"stablecoin", "Stablecoin"),
    (r"socialfi|\bsocial\b", "SocialFi"),
    (r"pump\.fun", "Pump.fun"),
    (r"prediction market", "Prediction market"),
]


def now_vn():
    return datetime.now(timezone(timedelta(hours=7)))


def in_quiet_hours():
    n = now_vn()
    return QUIET_START <= (n.hour, n.minute) < QUIET_END


def get(path, params=None, tries=3):
    for i in range(tries):
        try:
            url = BASE + "/" + path
            r = requests.get(url, params=params, timeout=20)
            return r.json()["data"]
        except Exception as e:
            print("Lỗi gọi", path, "lần", i + 1, ":", e)
            time.sleep(5)
    raise RuntimeError("Không lấy được " + path)


def tg(text):
    chunks = []
    cur = ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > 3800:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        chunks.append(cur)
    url = "https://api.telegram.org/bot" + TOKEN + "/sendMessage"
    for c in chunks:
        payload = dict(
            chat_id=CHAT_ID,
            text=c,
            disable_web_page_preview="true",
        )
        r = requests.post(url, data=payload, timeout=15)
        print("Telegram:", r.status_code)


# ---------- CoinGecko + bộ nhớ ----------
cg_blocked = False


def cg_get(path, params=None):
    global cg_blocked
    if cg_blocked:
        return None
    headers = {}
    if CG_KEY:
        headers["x-cg-demo-api-key"] = CG_KEY
    time.sleep(2.5)
    try:
        r = requests.get(CG + path, params=params, headers=headers, timeout=20)
        if r.status_code == 429:
            print("CoinGecko 429: tạm dừng gọi CoinGecko trong lượt này")
            cg_blocked = True
            return None
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print("Lỗi CoinGecko", path, e)
        return None


def load_cache():
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_cache(c):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False)
    except Exception as e:
        print("Không lưu được cache:", e)


def rank_key(c):
    return c.get("market_cap_rank") or 10**9


def top_exchanges(tickers):
    vols = {}
    for t in tickers or []:
        if t.get("is_anomaly") or t.get("is_stale"):
            continue
        market = t.get("market") or {}
        m = market.get("name")
        conv = t.get("converted_volume") or {}
        v = conv.get("usd") or 0
        if m:
            vols[m] = vols.get(m, 0) + v
    ranked = sorted(vols.items(), key=lambda kv: kv[1], reverse=True)
    return [k for k, _ in ranked][:6]


def cg_lookup(base):
    names = [base]
    stripped = re.sub(r"^(1000000|100000|10000|1000|1M)", "", base)
    if stripped and stripped != base:
        names.append(stripped)
    for name in names:
        res = cg_get("/search", {"query": name})
        if res is None:
            return None
        exact = []
        for c in res.get("coins", []):
            sym = (c.get("symbol") or "").upper()
            if sym == name.upper():
                exact.append(c)
        if not exact:
            continue
        exact.sort(key=rank_key)
        best = exact[0]
        d = cg_get("/coins/" + best["id"], CG_DETAIL_PARAMS)
        if d is None:
            return None
        md = d.get("market_data") or {}
        mcap = (md.get("market_cap") or {}).get("usd")
        result = {}
        result["found"] = True
        result["ambiguous"] = len(exact) > 1
        result["categories"] = d.get("categories") or []
        result["mcap"] = mcap
        result["exchanges"] = top_exchanges(d.get("tickers"))
        return result
    return {"found": False}


def coin_info(base, cache):
    now = time.time()
    e = cache.get(base)
    if e:
        found = e["data"].get("found")
        ttl = CACHE_TTL_H if found else MISS_TTL_H
        if now - e["ts"] < ttl * 3600:
            return e["data"]
    data = cg_lookup(base)
    if data is not None:
        cache[base] = {"ts": now, "data": data}
        return data
    if e:
        return e["data"]
    return None


def make_tags(cats):
    tags = []
    for c in cats:
        for pat, lab in TAG_RULES:
            if re.search(pat, c, re.I) and lab not in tags:
                tags.append(lab)
    if not tags and cats:
        tags = [cats[0]]
    return tags[:4]


def cap_text(m):
    if not m:
        return "chưa rõ vốn hóa"
    if m < 1e8:
        lab = "Low-cap"
    elif m < 1e9:
        lab = "Mid-cap"
    else:
        lab = "Large-cap"
    return lab + " (~$" + format(m / 1e6, ",.0f") + "M)"


# ---------- Tương quan với BTC (tính từ nến MEXC) ----------
def hourly_closes(sym):
    start = int(time.time()) - 170 * 3600
    d = get("kline/" + sym, {"interval": "Min60", "start": start})
    closes = [float(x) for x in d["close"]]
    return dict(zip(d["time"], closes))


def btc_stats(sym, btc):
    try:
        c = hourly_closes(sym)
        step = 3600 if max(c) < 1e11 else 3600000
        xs = []
        ys = []
        for t in sorted(set(c) & set(btc)):
            p = t - step
            if p in c and p in btc:
                xs.append(math.log(btc[t] / btc[p]))
                ys.append(math.log(c[t] / c[p]))
        if len(xs) < 24:
            return None
        mx = sum(xs) / len(xs)
        my = sum(ys) / len(ys)
        sxx = sum((x - mx) ** 2 for x in xs)
        syy = sum((y - my) ** 2 for y in ys)
        sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        if sxx == 0 or syy == 0:
            return None
        corr = sxy / math.sqrt(sxx * syy)
        beta = sxy / sxx
        return corr, beta
    except Exception as e:
        print("Lỗi tính BTC:", sym, e)
        return None


def corr_label(r):
    if r >= 0.6:
        return "cao"
    if r >= 0.3:
        return "trung bình"
    if r > -0.3:
        return "thấp"
    return "ngược chiều"


def describe(sym, base, cache, btc):
    out = []
    info = coin_info(base, cache)
    if info is None:
        out.append("🏷 Không lấy được dữ liệu CoinGecko lượt này")
    elif not info.get("found"):
        out.append("🏷 Chưa có trên CoinGecko (token rất mới hoặc chưa lập chỉ mục)")
        out.append("🏦 Sàn: chưa có dữ liệu")
    else:
        tags = make_tags(info.get("categories") or [])
        parts = tags + [cap_text(info.get("mcap"))]
        line = "🏷 " + " • ".join(parts)
        if info.get("ambiguous"):
            line += " (khớp theo ký hiệu, có thể nhầm)"
        out.append(line)
        exch = ", ".join(info.get("exchanges") or [])
        out.append("🏦 Sàn: " + (exch or "chưa có dữ liệu"))
    if btc:
        st = btc_stats(sym, btc)
        if st:
            r, b = st
            txt = "tương quan " + corr_label(r)
            txt += " (" + format(r, "+.2f") + "), beta " + format(b, ".1f")
            out.append("₿ BTC (7 ngày, nến 1h): " + txt)
        else:
            out.append("₿ BTC: chưa đủ dữ liệu nến")
    return out


def fmt_price(px, sym):
    try:
        v = float(px.get(sym))
        if v >= 1:
            return format(v, ",.2f")
        return format(v, ".4f")
    except Exception:
        return "n/a"


def market_header(tickers):
    px = {}
    for d in tickers:
        px[d.get("symbol")] = d.get("lastPrice")
    btc_p = fmt_price(px, "BTC_USDT")
    sol_p = fmt_price(px, "SOL_USDT")
    return "📊 BTC: " + btc_p + " | SOL: " + sol_p + " (USDT)"


# ---------- Chương trình chính ----------
quiet = in_quiet_hours()
print("Giờ VN:", now_vn().strftime("%H:%M"), "| yên lặng:", quiet)
print("CG key:", "có" if CG_KEY else "KHÔNG")

tickers = get("ticker")
try:
    detail = {d["symbol"]: d for d in get("detail")}
except Exception as e:
    print("Không lấy được detail:", e)
    detail = {}

hits = []
for d in tickers:
    sym = d.get("symbol", "")
    if not sym.endswith("_USDT"):
        continue
    try:
        pct = float(d.get("riseFallRate") or 0) * 100
    except Exception:
        continue
    if pct < MIN_PCT:
        continue
    vol = float(d.get("amount24") or 0)
    info = detail.get(sym, {})
    base = info.get("baseCoin") or sym.split("_")[0]
    label = info.get("typeLabel")
    print("[ỨNG VIÊN]", sym, format(pct, "+.1f") + "%",
          "vol=" + format(vol / 1e6, ".2f") + "M", "typeLabel=" + str(label))
    if vol < MIN_VOL_USDT:
        print("   -> bỏ qua: khối lượng nhỏ")
        continue
    if base in STOCK_BASES or label == 2:
        print("   -> bỏ qua: cổ phiếu")
        continue
    hits.append((pct, sym, d["lastPrice"], vol, base))

hits.sort(reverse=True)
print("Số coin đủ điều kiện:", len(hits))

cache = load_cache()
if hits and quiet:
    print("Giờ yên lặng 00:00-05:30, không gửi tin.")
elif hits:
    try:
        btc = hourly_closes("BTC_USDT")
    except Exception as e:
        print("Không lấy được nến BTC:", e)
        btc = None
    head = "🟢 " + str(len(hits)) + " coin futures tăng ≥"
    head += format(MIN_PCT, ".0f") + "% (24h):"
    lines = [market_header(tickers), "", head, ""]
    for i, (pct, sym, price, vol, base) in enumerate(hits):
        row = sym + ": +" + format(pct, ".2f") + "% | giá " + str(price)
        row += " | KL24h ~" + format(vol / 1e6, ".1f") + "M USDT"
        lines.append(row)
        if i < MAX_ENRICH:
            lines += describe(sym, base, cache, btc)
        lines.append(LINK.format(sym=sym))
        lines.append("")
    tg("\n".join(lines))
save_cache(cache)

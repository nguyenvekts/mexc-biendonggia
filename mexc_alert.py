import os, re, json, math, time, requests
from datetime import datetime, timezone, timedelta

TOKEN   = os.environ["TG_TOKEN"]
CHAT_ID = os.environ["TG_CHAT_ID"]
CG_KEY  = os.environ.get("CG_API_KEY", "")

MIN_PCT      = 20.0
MIN_VOL_USDT = 1_000_000
QUIET_START  = (0, 0)
QUIET_END    = (5, 30)
STOCK_BASES  = {"COIN", "HOOD", "NVDA", "AAPL", "AMZN", "GOOGL", "TSLA", "MCD"}

MAX_ENRICH  = 8        # chỉ tra thông tin chi tiết cho tối đa 8 coin/lượt
CACHE_FILE  = "cg_cache.json"
CACHE_TTL_H = 24       # nhớ kết quả tìm thấy 24 giờ
MISS_TTL_H  = 6        # nhớ kết quả "không thấy" 6 giờ

BASE = "https://contract.mexc.com/api/v1/contract"
LINK = "https://www.mexc.com/vi-VN/futures/{sym}?_from=market"
CG   = "https://api.coingecko.com/api/v3"

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
            return requests.get(f"{BASE}/{path}", params=params, timeout=20).json()["data"]
        except Exception as e:
            print(f"Lỗi gọi {path} (lần {i+1}):", e)
            time.sleep(5)
    raise RuntimeError(f"Không lấy được {path}")

def tg(text):
    chunks, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > 3800:
            chunks.append(cur); cur = ""
        cur += line + "\n"
    if cur.strip():
        chunks.append(cur)
    for c in chunks:
        r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                          data={"chat_id": CHAT_ID, "text": c,
                                "disable_web_page_preview": "true"}, timeout=15)
        print("Telegram:", r.status_code)

# ---------- CoinGecko + bộ nhớ ----------
cg_blocked = False

def cg_get(path, params=None):
    global cg_blocked
    if cg_blocked:
        return None
    headers = {"x-cg-demo-api-key": CG_KEY} if CG_KEY else {}
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

def cg_lookup(base):
    names = [base]
    stripped = re.sub(r"^(1000000|100000|10000|1000|1M)", "", base)
    if stripped and stripped != base:
        names.append(stripped)
    for name in names:
        res = cg_get("/search", {"query": name})
        if res is None:
            return None
        exact = [c for c in res.get("coins", [])
                 if (c.get("symbol") or "").upper() == name.upper()]
        if not exact:
            continue
        exact.sort(key=lambda c: c.get("market_cap_rank") or 10**9)
        best = exact[0]
        d = cg_get(f"/coins/{best['id']}", {
            "localization": "false", "tickers": "true", "market_data": "true",
            "community_data": "false", "developer_data": "false", "sparkline": "false"})
        if d is None:
            return None
        mcap = ((d.get("market_data") or {}).get("market_cap") or {}).get("usd")
        vols = {}
        for t in d.get("tickers") or []:
            if t.get("is_anomaly") or t.get("is_stale"):
                continue
            m = (t.get("market") or {}).get("name")
            v = (t.get("converted_volume") or {}).get("usd") or 0
            if m:
                vols[m] = vols.get(m, 0) + v
        exch = [k for k, _ in sorted(vols.items(), key=lambda x: -x[1])][:6]
        return {"found": True, "ambiguous": len(exact) > 1,
                "categories": d.get("categories") or [], "mcap": mcap, "exchanges": exch}
    return {"found": False}

def coin_info(base, cache):
    now = time.time()
    e = cache.get(base)
    if e:
        ttl = CACHE_TTL_H if e["data"].get("found") else MISS_TTL_H
        if now - e["ts"] < ttl * 3600:
            return e["data"]
    data = cg_lookup(base)
    if data is not None:
        cache[base] = {"ts": now, "data": data}
        return data
    return e["data"] if e else None   # lỗi: dùng bản cũ nếu có

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
    lab = "Low-cap" if m < 1e8 else "Mid-cap" if m < 1e9 else "Large-cap"
    return f"{lab} (~${m/1e6:,.0f}M)"

# ---------- Tương quan với BTC (tính từ nến MEXC) ----------
def hourly_closes(sym):
    start = int(time.time()) - 170 * 3600
    d = get(f"kline/{sym}", {"interval": "Min60", "start": start})
    return dict(zip(d["time"], [float(x) for x in d["close"]]))

def btc_stats(sym, btc):
    try:
        c = hourly_closes(sym)
        step = 3600 if max(c) < 1e11 else 3600000
        xs, ys = [], []
        for t in sorted(set(c) & set(btc)):
            p = t - step
            if p in c and p in btc:
                xs.append(math.log(btc[t] / btc[p]))
                ys.append(math.log(c[t] / c[p]))
        if len(xs) < 24:
            return None
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        sxx = sum((x - mx) ** 2 for x in xs)
        syy = sum((y - my) ** 2 for y in ys)
        sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        if sxx == 0 or syy == 0:
            return None
        return sxy / math.sqrt(sxx * syy), sxy / sxx
    except Exception as e:
        print("Lỗi tính BTC:", sym, e)
        return None

def corr_label(r):
    return "cao" if r >= 0.6 else "trung bình" if r >= 0.3 else "thấp" if r > -0.3 else "ngược chiều"

def describe(sym, base, cache, btc):
    out = []
    info = coin_info(base, cache)
    if info is None:
        out.append("🏷 Không lấy được dữ liệu CoinGecko lượt này")
    elif not info.get("found"):
        out.append("🏷 Chưa có trên CoinGecko (token rất mới hoặc chưa được lập chỉ mục)")
        out.append("🏦 Sàn: chưa có dữ liệu")
    else:
        tags = make_tags(info.get("categories") or [])
        line = "🏷 " + " • ".join(tags + [cap_text(info.get("mcap"))])
        if info.get("ambiguous"):
            line += " (khớp theo ký hiệu, có thể nhầm)"
        out.append(line)
        out.append("🏦 Sàn: " + (", ".join(info.get("exchanges") or []) or "chưa có dữ liệu"))
    if btc:
        st = btc_stats(sym, btc)
        if st:
            out.append(f"₿ BTC (7 ngày, nến 1h): tương quan {corr_label(st[0])} ({st[0]:+.2f}), beta {st[1]:.1f}")
        else:
            out.append("₿ BTC: chưa đủ dữ liệu nến")
    return out
def market_header(tickers):
    px = {d.get("symbol"): d.get("lastPrice") for d in tickers}

    def fmt_price(sym):
        try:
            v = float(px.get(sym))
            return f"{v:,.2f}" if v >= 1 else f"{v:.4f}"
        except Exception:
            return "n/a"

    return f"📊 BTC: {fmt_price('BTC_USDT')} | SOL: {fmt_price('SOL_USDT')} (USDT)"
    
# ---------- Chương trình chính ----------
quiet = in_quiet_hours()
print("Giờ VN:", now_vn().strftime("%H:%M"), "| yên lặng:", quiet,
      "| CG key:", "có" if CG_KEY else "KHÔNG")

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
    pct = float(d["riseFallRate"]) * 100
    if pct < MIN_PCT:
        continue
    vol  = float(d.get("amount24", 0))
    info = detail.get(sym, {})
    base = info.get("baseCoin") or sym.split("_")[0]
    label = info.get("typeLabel")
    print(f"[ỨNG VIÊN] {sym} +{pct:.1f}% vol={vol/1e6:.2f}M typeLabel={label}")
    if vol < MIN_VOL_USDT:
        print("   -> bỏ qua: khối lượng nhỏ"); continue
    if base in STOCK_BASES or label == 2:
        print("   -> bỏ qua: cổ phiếu"); continue
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
       lines = [market_header(tickers), "", f"🟢 {len(hits)} coin futures tăng ≥{MIN_PCT:.0f}% (24h):", ""]
    for i, (pct, sym, price, vol, base) in enumerate(hits):
        lines.append(f"{sym}: +{pct:.2f}% | giá {price} | KL24h ~{vol/1e6:.1f}M USDT")
        if i < MAX_ENRICH:
            lines += describe(sym, base, cache, btc)
        lines.append(LINK.format(sym=sym))
        lines.append("")
    tg("\n".join(lines))
save_cache(cache)

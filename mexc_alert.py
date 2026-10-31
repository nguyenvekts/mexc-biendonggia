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
        exch = [k for k, _ in sorted(vols.items(), key=lambda x:

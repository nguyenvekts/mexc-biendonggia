import os, requests
from datetime import datetime, timezone, timedelta

TOKEN   = os.environ["TG_TOKEN"]
CHAT_ID = os.environ["TG_CHAT_ID"]

MIN_PCT      = 20.0        # ngưỡng tăng 24h (%)
MIN_VOL_USDT = 1_000_000   # khối lượng 24h tối thiểu (USDT)

# Khung giờ yên lặng theo giờ Việt Nam (UTC+7): 00:00 -> 05:30
QUIET_START = (0, 0)
QUIET_END   = (5, 30)

# Mã cổ phiếu cần loại (tự bổ sung khi MEXC thêm mã mới)
STOCK_BASES = {"COIN", "HOOD", "NVDA", "AAPL", "AMZN", "GOOGL", "TSLA", "MCD"}

BASE = "https://contract.mexc.com/api/v1/contract"
LINK = "https://www.mexc.com/vi-VN/futures/{sym}?_from=market"

def now_vn():
    return datetime.now(timezone(timedelta(hours=7)))

def in_quiet_hours():
    t = (now_vn().hour, now_vn().minute)
    return QUIET_START <= t < QUIET_END

def get(path):
    return requests.get(f"{BASE}/{path}", timeout=20).json()["data"]

def tg(text):
    # Chia theo dòng (không cắt giữa link), mỗi tin tối đa ~3800 ký tự
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

tickers = get("ticker")
try:
    detail = {d["symbol"]: d for d in get("detail")}
except Exception as e:
    print("Không lấy được detail:", e)
    detail = {}

print("Giờ VN:", now_vn().strftime("%H:%M"), "| yên lặng:", in_quiet_hours())
print("Tổng số cặp:", len(tickers))

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
    label, plate, tags = info.get("typeLabel"), info.get("conceptPlate"), info.get("tagIdList")
    print(f"[ỨNG VIÊN] {sym} +{pct:.1f}% vol={vol/1e6:.2f}M typeLabel={label} plate={plate} tags={tags}")

    if vol < MIN_VOL_USDT:
        print("   -> [BỎ QUA khối lượng nhỏ]"); continue
    if base in STOCK_BASES or label == 2:
        print("   -> [BỎ QUA cổ phiếu]"); continue

    hits.append((pct, sym, d["lastPrice"], vol))

hits.sort(reverse=True)
print("Số coin đủ điều kiện:", len(hits))

if hits and in_quiet_hours():
    print("Đang trong giờ yên lặng 00:00-05:30 (giờ VN), không gửi tin.")
elif hits:
    lines = [f"🟢 {len(hits)} coin futures tăng ≥{MIN_PCT:.0f}% (24h):", ""]
    for pct, sym, price, vol in hits:
        lines.append(f"{sym}: +{pct:.2f}% | giá {price} | KL24h ~{vol/1e6:.1f}M USDT")
        lines.append(LINK.format(sym=sym))
        lines.append("")
    tg("\n".join(lines))

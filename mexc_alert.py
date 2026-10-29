import os, requests

TOKEN   = os.environ["TG_TOKEN"]
CHAT_ID = os.environ["TG_CHAT_ID"]

MIN_PCT      = 20.0        # ngưỡng tăng 24h (%)
MIN_VOL_USDT = 1_000_000   # khối lượng 24h tối thiểu (USDT)

# Mã cổ phiếu cần loại (tự bổ sung khi MEXC thêm mã mới)
STOCK_BASES = {"COIN", "HOOD", "NVDA", "AAPL", "AMZN", "GOOGL", "TSLA", "MCD"}

BASE = "https://contract.mexc.com/api/v1/contract"

def get(path):
    return requests.get(f"{BASE}/{path}", timeout=20).json()["data"]

def tg(text):
    for i in range(0, len(text), 3800):
        r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                          data={"chat_id": CHAT_ID, "text": text[i:i+3800]}, timeout=15)
        print("Telegram:", r.status_code)

tickers = get("ticker")
try:
    detail = {d["symbol"]: d for d in get("detail")}
except Exception as e:
    print("Không lấy được detail:", e)
    detail = {}

print("Tổng số cặp:", len(tickers), "| mẫu ticker:", tickers[0])

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
print("Số coin gửi báo:", len(hits))

if hits:
    lines = [f"🟢 {len(hits)} coin futures tăng ≥{MIN_PCT:.0f}% (24h):"]
    for pct, sym, price, vol in hits:
        lines.append(f"{sym}: +{pct:.2f}% | giá {price} | KL24h ~{vol/1e6:.1f}M USDT")
    tg("\n".join(lines))

import os, requests

TOKEN   = os.environ["TG_TOKEN"]
CHAT_ID = os.environ["TG_CHAT_ID"]
MIN_PCT = 15.0   # ngưỡng tăng 24h (%)

URL = "https://contract.mexc.com/api/v1/contract/ticker"

def tg(text):
    # Telegram giới hạn 4096 ký tự/tin, nên cắt thành từng đoạn
    for i in range(0, len(text), 3800):
        r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                          data={"chat_id": CHAT_ID, "text": text[i:i+3800]}, timeout=15)
        print("Telegram:", r.status_code)

data = requests.get(URL, timeout=20).json()["data"]
print("Tổng số cặp:", len(data), "| mẫu:", data[0])

hits = []
for d in data:
    sym = d.get("symbol", "")
    if not sym.endswith("_USDT"):
        continue
    pct = float(d["riseFallRate"]) * 100   # riseFallRate là số thập phân (0.15 = 15%)
    if pct >= MIN_PCT:
        hits.append((pct, sym, d["lastPrice"], float(d.get("amount24", 0))))

hits.sort(reverse=True)
print("Số coin vượt ngưỡng:", len(hits))

if hits:
    lines = [f"🟢 {len(hits)} coin futures tăng ≥{MIN_PCT:.0f}% (24h):"]
    for pct, sym, price, amt in hits:
        lines.append(f"{sym}: +{pct:.2f}% | giá {price} | KL24h ~{amt/1e6:.1f}M USDT")
    tg("\n".join(lines))

import os, time, requests
from datetime import datetime, timezone, timedelta

TOKEN   = os.environ["TG_TOKEN"]
CHAT_ID = os.environ["TG_CHAT_ID"]

MIN_PCT      = 20.0        # ngưỡng tăng 24h (%)
MIN_VOL_USDT = 1_000_000   # khối lượng 24h tối thiểu (USDT)
QUIET_START  = (0, 0)      # giờ yên lặng theo giờ VN
QUIET_END    = (5, 30)
STOCK_BASES  = {"COIN", "HOOD", "NVDA", "AAPL", "AMZN", "GOOGL", "TSLA", "MCD"}

CHECK_EVERY = 15 * 60      # kiểm tra mỗi 15 phút
RUN_FOR     = 59 * 60      # mỗi lần chạy kéo dài ~59 phút

BASE = "https://contract.mexc.com/api/v1/contract"
LINK = "https://www.mexc.com/vi-VN/futures/{sym}?_from=market"

def now_vn():
    return datetime.now(timezone(timedelta(hours=7)))

def in_quiet_hours():
    n = now_vn()
    return QUIET_START <= (n.hour, n.minute) < QUIET_END

def get(path, tries=3):
    for i in range(tries):
        try:
            return requests.get(f"{BASE}/{path}", timeout=20).json()["data"]
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

def check_once():
    quiet = in_quiet_hours()
    print("=" * 30, "Giờ VN:", now_vn().strftime("%H:%M"), "| yên lặng:", quiet)
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
        hits.append((pct, sym, d["lastPrice"], vol))

    hits.sort(reverse=True)
    print("Số coin đủ điều kiện:", len(hits))
    if not hits:
        return
    if quiet:
        print("Giờ yên lặng 00:00-05:30, không gửi tin.")
        return
    lines = [f"🟢 {len(hits)} coin futures tăng ≥{MIN_PCT:.0f}% (24h):", ""]
    for pct, sym, price, vol in hits:
        lines.append(f"{sym}: +{pct:.2f}% | giá {price} | KL24h ~{vol/1e6:.1f}M USDT")
        lines.append(LINK.format(sym=sym))
        lines.append("")
    tg("\n".join(lines))

start = time.time()
if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
    tg("✅ Bot MEXC đang chạy (tin thử khi bạn bấm chạy tay).")

while True:
    t0 = time.time()
    try:
        check_once()
    except Exception as e:
        print("Lỗi vòng kiểm tra:", e)
    nxt, deadline = t0 + CHECK_EVERY, start + RUN_FOR
    if nxt > deadline:
        time.sleep(max(deadline - time.time(), 0))
        break
    time.sleep(max(nxt - time.time(), 0))

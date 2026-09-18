# ============================================================
# main.py — Entry point chính
# ============================================================
import json, logging, sys, os
from datetime import datetime
import pytz

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

def main():
    logger.info("🚀 VN Market AI Agent starting...")

    from data_collector import build_snapshot, get_trading_date
    from ai_agent import run_agent
    from notifier import send_telegram

    # Bước 1: Ngày giao dịch
    date = get_trading_date()
    logger.info(f"📅 Trading date: {date}")

    # Bước 2: Thu thập dữ liệu
    logger.info("[1/3] Collecting market data...")
    snapshot = build_snapshot(date)

    os.makedirs("snapshots", exist_ok=True)
    path = f"snapshots/market_snapshot_{date}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    # Lưu thêm bản "mới nhất" tên cố định để bot Q&A (telegram_qa.py) luôn
    # biết chính xác file nào cần đọc, không phải tìm theo ngày.
    with open("snapshots/latest.json", "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    logger.info(f"✅ Snapshot saved: {path}")

    # Bước 3: AI phân tích
    logger.info("[2/3] Running AI analysis...")
    analysis = run_agent(snapshot)
    logger.info(f"✅ Analysis done ({len(analysis)} chars)")

    os.makedirs("reports", exist_ok=True)
    rpath = f"reports/analysis_{date}.md"
    with open(rpath, "w", encoding="utf-8") as f:
        f.write(f"# Phân tích thị trường {date}\n\n{analysis}")
    with open("reports/latest.md", "w", encoding="utf-8") as f:
        f.write(f"# Phân tích thị trường {date}\n\n{analysis}")
    logger.info(f"✅ Report saved: {rpath}")

    # Bước 4: Gửi Telegram
    logger.info("[3/3] Sending Telegram...")
    send_telegram(snapshot, analysis)

    logger.info("🏁 Done!")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"❌ FATAL: {e}", exc_info=True)
        sys.exit(1)

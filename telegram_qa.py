# ============================================================
# telegram_qa.py — Trả lời câu hỏi của người dùng trong Telegram,
# dựa trên dữ liệu thị trường + bản phân tích gần nhất đã thu thập.
# Chạy định kỳ mỗi 5 phút (xem .github/workflows/telegram-qa.yml).
# ============================================================
import json
import logging
import os
import sys

import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

STATE_PATH    = "state/telegram_state.json"
SNAPSHOT_PATH = "snapshots/latest.json"
REPORT_PATH   = "reports/latest.md"

GEMINI_MODEL = "gemini-3.5-flash"
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)

QA_SYSTEM_PROMPT = """Bạn là trợ lý trả lời câu hỏi về bản tin thị trường chứng khoán/tài
chính mà hệ thống đã gửi cho người dùng qua Telegram. Bạn CHỈ được trả lời dựa trên dữ
liệu và bản phân tích được cung cấp bên dưới — không tự bịa thêm số liệu không có trong
dữ liệu đó.

Nếu câu hỏi không liên quan gì tới thị trường/dữ liệu đã cung cấp (ví dụ hỏi chuyện phiếm,
chủ đề khác), hãy trả lời ngắn gọn rằng bạn chỉ hỗ trợ các câu hỏi liên quan tới bản tin
thị trường đã gửi.

Nếu dữ liệu hiện có không đủ để trả lời chính xác (ví dụ một chỉ số đang là N/A), hãy nói
rõ là chưa có đủ thông tin thay vì đoán số liệu.

Trả lời ngắn gọn, rõ ràng, bằng tiếng Việt, phù hợp để đọc trên Telegram (không dùng cú
pháp Markdown phức tạp — chỉ dùng chữ thường, có thể dùng gạch đầu dòng đơn giản bằng dấu -)."""


def load_state() -> dict:
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"last_update_id": 0}


def save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def load_context() -> str:
    """Ghép dữ liệu snapshot + báo cáo phân tích gần nhất thành ngữ cảnh cho AI."""
    parts = []
    if os.path.exists(SNAPSHOT_PATH):
        with open(SNAPSHOT_PATH, "r", encoding="utf-8") as f:
            snapshot = json.load(f)
        parts.append("## DỮ LIỆU THỊ TRƯỜNG GẦN NHẤT (JSON)\n" +
                      json.dumps(snapshot, ensure_ascii=False, indent=2))
    if os.path.exists(REPORT_PATH):
        with open(REPORT_PATH, "r", encoding="utf-8") as f:
            report = f.read()
        parts.append("## BÁO CÁO PHÂN TÍCH GẦN NHẤT\n" + report)
    if not parts:
        return "(Chưa có dữ liệu thị trường nào được thu thập.)"
    return "\n\n".join(parts)


def ask_gemini(question: str, context: str) -> str:
    api_key = os.environ["GEMINI_API_KEY"]
    prompt = f"{context}\n\n---\n\nCÂU HỎI CỦA NGƯỜI DÙNG: {question}"
    payload = {
        "system_instruction": {"parts": [{"text": QA_SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 6000}
    }
    resp = requests.post(GEMINI_URL, params={"key": api_key}, json=payload, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        raise RuntimeError(f"Gemini trả về dữ liệu không như mong đợi: {data}")


def send_reply(token: str, chat_id: str, text: str, reply_to_message_id: int):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    r = requests.post(url, json={
        "chat_id"                    : chat_id,
        "text"                       : text[:4000],
        "reply_to_message_id"        : reply_to_message_id,
        "allow_sending_without_reply": True
    }, timeout=15)
    if not r.ok:
        logger.error(f"❌ Telegram sendMessage lỗi {r.status_code}: {r.text}")
    r.raise_for_status()


def main():
    token   = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    is_first_run = not os.path.exists(STATE_PATH)
    state  = load_state()
    offset = state.get("last_update_id", 0) + 1

    url = f"https://api.telegram.org/bot{token}/getUpdates"
    r = requests.get(url, params={"offset": offset, "timeout": 0}, timeout=15)
    r.raise_for_status()
    updates = r.json().get("result", [])
    logger.info(f"📥 Nhận {len(updates)} update mới (offset={offset})")

    if not updates:
        logger.info("Không có tin nhắn mới, kết thúc.")
        if is_first_run:
            save_state(state)
        return

    max_update_id = state.get("last_update_id", 0)

    # Lần chạy đầu tiên: chỉ đánh dấu đã đọc hết tin nhắn cũ (nếu có, ví dụ lúc
    # bạn bấm Start hoặc nhắn thử trước khi tính năng này tồn tại), KHÔNG trả
    # lời chúng — tránh việc bot bất ngờ trả lời hàng loạt tin nhắn cũ.
    if is_first_run:
        for upd in updates:
            max_update_id = max(max_update_id, upd["update_id"])
        state["last_update_id"] = max_update_id
        save_state(state)
        logger.warning(f"🆕 Lần chạy đầu tiên — bỏ qua {len(updates)} tin nhắn cũ, "
                        f"chỉ trả lời tin nhắn gửi SAU thời điểm này.")
        return

    context = load_context()

    for upd in updates:
        max_update_id = max(max_update_id, upd["update_id"])
        msg = upd.get("message")
        if not msg or "text" not in msg:
            continue

        # Chỉ trả lời tin nhắn từ đúng chat đã cấu hình (chủ bot) — tránh
        # người lạ (nếu bot lỡ bị thêm vào group khác) làm tốn quota Gemini.
        if str(msg["chat"]["id"]) != str(chat_id):
            logger.warning(f"Bỏ qua tin nhắn từ chat lạ: {msg['chat']['id']}")
            continue

        text = msg["text"].strip()
        if text.startswith("/"):
            continue  # bỏ qua lệnh hệ thống như /start

        logger.info(f"❓ Câu hỏi: {text}")
        try:
            answer = ask_gemini(text, context)
        except Exception as e:
            logger.error(f"✗ Lỗi gọi Gemini: {e}")
            answer = "Xin lỗi, hiện mình không trả lời được câu hỏi này do lỗi hệ thống. Vui lòng thử lại sau."

        try:
            send_reply(token, chat_id, answer, msg["message_id"])
            logger.info("✅ Đã trả lời")
        except Exception as e:
            logger.error(f"✗ Lỗi gửi Telegram: {e}")

    state["last_update_id"] = max_update_id
    save_state(state)
    logger.info(f"💾 Đã lưu state, last_update_id={max_update_id}")


if __name__ == "__main__":
    main()

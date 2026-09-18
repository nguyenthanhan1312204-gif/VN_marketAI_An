# ============================================================
# notifier.py — Gửi bản tin Telegram
# ============================================================
import requests
import os
import html
import re

def _markdown_to_telegram_html(text: str) -> str:
    """Chuyển Markdown cơ bản (Gemini hay trả về **đậm**, ### tiêu đề) sang
    thẻ HTML mà Telegram hiểu, đồng thời escape an toàn phần còn lại."""
    escaped = html.escape(text)
    # **đậm** -> <b>đậm</b>
    escaped = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', escaped)
    # ### Tiêu đề (ở đầu dòng) -> <b>Tiêu đề</b>
    escaped = re.sub(r'^#{1,6}\s*(.+)$', r'<b>\1</b>', escaped, flags=re.MULTILINE)
    return escaped

def send_telegram(snapshot: dict, analysis: str):
    token   = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    us  = snapshot.get("us_market", {})
    vn  = snapshot.get("vn_market", {})
    date = snapshot["metadata"]["date"]

    def pct(d, k):
        if not d or d.get(k) is None: return "N/A"
        v = d[k]
        emoji = "🟢" if v > 0 else "🔴" if v < 0 else "⚪"
        return f"{emoji} {v:+.2f}%"

    def val(d, k, fmt=".2f"):
        if not d or d.get(k) is None: return "N/A"
        return f"{d[k]:,{fmt}}"

    sp  = us.get("sp500")  or {}
    nd  = us.get("nasdaq") or {}
    dxy = us.get("dxy")    or {}
    vix = us.get("vix")    or {}
    vni = vn.get("vnindex") or {}
    fn  = vn.get("foreign_net") or {}
    usd = vn.get("usd_vnd") or {}

    # Cắt phần AI analysis vừa đủ cho Telegram (max ~3000 ký tự)
    ai_text_raw = analysis[:2800] + ("..." if len(analysis) > 2800 else "")
    # Escape để nội dung AI (có thể chứa <, >, & hoặc markdown lạ)
    # không phá cú pháp HTML của Telegram, đồng thời hiển thị đậm/tiêu đề đúng
    ai_text = _markdown_to_telegram_html(ai_text_raw)

    message = f"""📊 <b>BẢN TIN THỊ TRƯỜNG · {date}</b>
━━━━━━━━━━━━━━━━━━━

🌍 <b>THẾ GIỚI HÔM QUA</b>
S&amp;P500  {val(sp,'close')}  {pct(sp,'change_pct')}
Nasdaq   {val(nd,'close')}  {pct(nd,'change_pct')}
DXY      {val(dxy,'close')}  {pct(dxy,'change_pct')}
VIX      {val(vix,'close')}  {pct(vix,'change_pct')}

🇻🇳 <b>VIỆT NAM HÔM QUA</b>
VN-Index  {val(vni,'close')} điểm  {pct(vni,'change_pct')}
Khối ngoại  {val(fn,'net_bn_vnd')} tỷ VND
USD/VND  {val(usd,'sell_rate','.0f')}

━━━━━━━━━━━━━━━━━━━
{ai_text}"""

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    r = requests.post(url, json={
        "chat_id"                : chat_id,
        "text"                   : message,
        "parse_mode"             : "HTML",
        "disable_web_page_preview": True
    }, timeout=15)

    if not r.ok:
        print(f"❌ Telegram error {r.status_code}: {r.text}")

    r.raise_for_status()
    print(f"✅ Telegram sent: {r.json().get('ok')}")
    return r.json()

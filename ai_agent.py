# ============================================================
# ai_agent.py — AI phân tích và dự báo
# ============================================================
import os
import requests

GEMINI_MODEL = "gemini-3.5-flash"
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)

SYSTEM_PROMPT = """Bạn là chuyên gia phân tích chiến lược tài chính cấp cao, chuyên về thị trường chứng khoán Việt Nam và phân tích liên thị trường (Intermarket Analysis).

## NGUYÊN TẮC PHÂN TÍCH LIÊN THỊ TRƯỜNG:

**DXY ↔ VN-Index:** DXY tăng → USD mạnh → tỷ giá USD/VND tăng → khối ngoại bán ròng → VNIndex chịu áp lực. Ngưỡng quan trọng: >107 là rủi ro cao.

**VIX ↔ Tâm lý:** VIX<17 (risk-on tốt), 17-25 (thận trọng), >25 (hoảng loạn, khối ngoại rút khỏi EM).

**S&P500/Nasdaq ↔ VN:** Tương quan ~0.55. Phiên Mỹ tốt → tâm lý tích cực cho phiên sáng VN hôm sau.

**Khối ngoại:** Bán ròng >500 tỷ là áp lực lớn. Tự doanh mua ròng khi thị trường giảm = tín hiệu đỡ giá.

## YÊU CẦU:
- Dẫn chứng số liệu cụ thể, lập luận nhân quả rõ ràng
- Ngắn gọn, chuyên nghiệp, bằng tiếng Việt
- Nêu xác suất, không dự báo chắc chắn"""

def build_prompt(snapshot: dict) -> str:
    us  = snapshot.get("us_market", {})
    eu  = snapshot.get("eu_market", {})
    vn  = snapshot.get("vn_market", {})
    dv  = snapshot.get("derived",   {})
    date = snapshot["metadata"]["date"]

    def v(d, k):
        if not d or d.get(k) is None: return "N/A"
        val = d[k]
        if isinstance(val, float):
            return f"{val:+.2f}%" if "pct" in k else f"{val:,.2f}"
        return str(val)

    return f"""# DỮ LIỆU THỊ TRƯỜNG NGÀY {date}

## 🇺🇸 MỸ
- S&P500: {v(us.get('sp500'),'close')} ({v(us.get('sp500'),'change_pct')})
- Nasdaq:  {v(us.get('nasdaq'),'close')} ({v(us.get('nasdaq'),'change_pct')})
- DXY:     {v(us.get('dxy'),'close')} ({v(us.get('dxy'),'change_pct')})
- VIX:     {v(us.get('vix'),'close')} ({v(us.get('vix'),'change_pct')})
- Vàng:    {v(us.get('gold'),'close')} ({v(us.get('gold'),'change_pct')})

## 🇪🇺 CHÂU ÂU
- STOXX50: {v(eu.get('stoxx50'),'close')} ({v(eu.get('stoxx50'),'change_pct')})
- FTSE100: {v(eu.get('ftse100'),'close')} ({v(eu.get('ftse100'),'change_pct')})
- DAX:     {v(eu.get('dax'),'close')} ({v(eu.get('dax'),'change_pct')})

## 🇻🇳 VIỆT NAM
- VN-Index:   {v(vn.get('vnindex'),'close')} điểm ({v(vn.get('vnindex'),'change_pct')})
- Khối ngoại: {v(vn.get('foreign_net'),'net_bn_vnd')} tỷ ({(vn.get('foreign_net') or {}).get('action','N/A')})
- USD/VND:    {v(vn.get('usd_vnd'),'sell_rate')}

## TÍN HIỆU
- Tâm lý Mỹ: {dv.get('us_sentiment','N/A')}
- DXY trend: {dv.get('dxy_trend','N/A')}
- Risk-on: {'Có ✅' if dv.get('risk_on') else 'Không ⚠️'}

---

Hãy phân tích và đưa ra dự báo theo format:

### 📌 ĐIỂM TIN CHÍNH (3 điểm ngắn)

### 🔗 PHÂN TÍCH TÁC ĐỘNG (nhân quả liên thị trường)

### 📊 3 KỊCH BẢN VN-INDEX HÔM NAY

🟢 TÍCH CỰC (X%): biên độ ... | điều kiện: ...
🟡 TRUNG LẬP (X%): biên độ ... | điều kiện: ...
🔴 TIÊU CỰC (X%): biên độ ... | điều kiện: ...

### 💡 KHUYẾN NGHỊ NGẮN HẠN

⚠️ Phân tích tham khảo, không phải khuyến nghị đầu tư."""

def run_agent(snapshot: dict) -> str:
    api_key = os.environ["GEMINI_API_KEY"]

    payload = {
        "system_instruction": {
            "parts": [{"text": SYSTEM_PROMPT}]
        },
        "contents": [
            {
                "role": "user",
                "parts": [{"text": build_prompt(snapshot)}]
            }
        ],
        "generationConfig": {
            # Gemini free tier giới hạn theo RPM/TPM/RPD (lượt gọi & token mỗi PHÚT,
            # lượt gọi mỗi NGÀY) — không phải "tổng token/ngày". Với tần suất dùng ở
            # đây (vài lượt gọi/ngày), ta còn rất xa các giới hạn đó. maxOutputTokens
            # chỉ giới hạn độ dài 1 câu trả lời — đặt rộng rãi (8000, cách xa mức tối
            # đa ~64000 của model) để không bao giờ bị cắt cụt giữa chừng nữa.
            "maxOutputTokens": 8000
        }
    }

    resp = requests.post(
        GEMINI_URL,
        params={"key": api_key},
        json=payload,
        timeout=120
    )
    resp.raise_for_status()
    data = resp.json()

    try:
        candidate = data["candidates"][0]
        text = candidate["content"]["parts"][0]["text"]
        finish_reason = candidate.get("finishReason")
        if finish_reason == "MAX_TOKENS":
            print(f"⚠️ Gemini bị cắt do hết token dù đã tăng giới hạn (finishReason=MAX_TOKENS, {len(text)} chars)")
        return text
    except (KeyError, IndexError):
        raise RuntimeError(f"Gemini trả về dữ liệu không như mong đợi: {data}")

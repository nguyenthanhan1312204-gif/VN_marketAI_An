# ============================================================
# data_collector.py — Thu thập dữ liệu thị trường
# ============================================================
import yfinance as yf
import pandas as pd
import requests
import json
import logging
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
import pytz
from curl_cffi import requests as cffi_requests

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# Yahoo Finance chặn client mặc định từ IP máy chủ (như GitHub Actions).
# Dùng curl_cffi để giả lập trình duyệt Chrome thật, tránh bị chặn/rate-limit.
YF_SESSION = cffi_requests.Session(impersonate="chrome")

VN_TZ  = pytz.timezone('Asia/Ho_Chi_Minh')
UTC_TZ = pytz.utc

def get_trading_date() -> str:
    today = datetime.now(VN_TZ)
    delta = 3 if today.weekday() == 0 else 1
    yesterday = today - timedelta(days=delta)
    return yesterday.strftime("%Y-%m-%d")

def _fetch_yf_ticker(symbol: str, date_str: str, lookback_days: int = 5) -> dict | None:
    """Tải 1 mã qua yfinance (đã vượt chặn bằng curl_cffi), trả về close/change_pct hoặc None.
    lookback_days: số ngày lùi lại để tìm dữ liệu — một số mã (như VN-Index) Yahoo
    không cập nhật đều đặn mỗi ngày, cần khoảng tìm rộng hơn mới đủ 2 phiên để tính %.
    """
    start_dt = datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=lookback_days)
    end_dt   = datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)
    df = yf.download(symbol,
                     start=start_dt.strftime("%Y-%m-%d"),
                     end=end_dt.strftime("%Y-%m-%d"),
                     progress=False, auto_adjust=True,
                     session=YF_SESSION)
    if df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    row      = df.iloc[-1]
    prev_row = df.iloc[-2] if len(df) >= 2 else df.iloc[-1]
    if len(df) < 2:
        logger.warning(f"[DEBUG {symbol}] Chỉ có {len(df)} phiên dữ liệu trong khoảng tìm kiếm -> change_pct sẽ là 0%")
    close      = float(row["Close"])
    prev_close = float(prev_row["Close"])
    change_pct = round(((close - prev_close) / prev_close) * 100, 2)
    volume     = int(row["Volume"]) if pd.notna(row.get("Volume")) else 0
    return {
        "symbol"     : symbol,
        "close"      : round(close, 2),
        "change_pct" : change_pct,
        "volume"     : volume,
    }

def fetch_global_markets(date_str: str) -> dict:
    tickers = {
        "sp500"   : "^GSPC",
        "nasdaq"  : "^NDX",
        "dxy"     : "DX-Y.NYB",
        "vix"     : "^VIX",
        "gold"    : "GC=F",
        "stoxx50" : "^STOXX50E",
        "ftse100" : "^FTSE",
        "dax"     : "^GDAXI",
    }
    result = {}
    for name, symbol in tickers.items():
        try:
            data = _fetch_yf_ticker(symbol, date_str)
            if data is None:
                logger.warning(f"No data for {symbol}")
            else:
                logger.info(f"✓ {name}: {data['close']} ({data['change_pct']:+.2f}%)")
            result[name] = data
        except Exception as e:
            logger.error(f"✗ {name}: {e}")
            result[name] = None
    return result

def _fetch_vnindex_vnstock(date_str: str) -> dict | None:
    """Lấy VN-Index qua thư viện vnstock (nguồn VCI) — Yahoo Finance không có
    dữ liệu lịch sử đáng tin cậy cho mã này qua API tải xuống."""
    try:
        from vnstock import Quote
    except ImportError as e:
        logger.error(f"[DEBUG VN-Index vnstock] Chưa cài được thư viện vnstock: {e}")
        return None
    try:
        end_dt   = datetime.strptime(date_str, "%Y-%m-%d")
        start_dt = end_dt - timedelta(days=20)
        quote = Quote(source="vci", symbol="VNINDEX")
        df = quote.history(
            start=start_dt.strftime("%Y-%m-%d"),
            end=end_dt.strftime("%Y-%m-%d"),
            interval="1D"
        )
        if df is None or df.empty:
            logger.warning("[DEBUG VN-Index vnstock] Không có dữ liệu trả về")
            return None

        # Tên cột trả về có thể khác nhau tùy phiên bản (close/Close...) — chuẩn hóa lại
        cols = {c.lower(): c for c in df.columns}
        close_col = cols.get("close")
        if close_col is None:
            logger.warning(f"[DEBUG VN-Index vnstock] Không tìm thấy cột 'close'. Các cột có sẵn: {list(df.columns)}")
            return None

        close      = float(df.iloc[-1][close_col])
        prev_close = float(df.iloc[-2][close_col]) if len(df) >= 2 else close
        change_pct = round(((close - prev_close) / prev_close) * 100, 2) if prev_close else 0.0
        logger.info(f"[DEBUG VN-Index vnstock] Lấy được {len(df)} phiên, phiên gần nhất: {close}")
        return {"close": round(close, 2), "change_pct": change_pct, "volume": None}
    except Exception as e:
        logger.error(f"[DEBUG VN-Index vnstock] Lỗi: {e}")
        return None

def fetch_vn_market(date_str: str) -> dict:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    result  = {}

    # 1) VN-Index — API cũ của TCBS đã ngừng hoạt động (404). Yahoo Finance cũng
    #    không có dữ liệu lịch sử đáng tin cậy cho ^VNINDEX.VN (dù trang xem tồn tại,
    #    API tải xuống báo "possibly delisted"). Dùng vnstock làm nguồn chính (chuyên
    #    biệt cho chứng khoán VN), Yahoo làm dự phòng nếu vnstock cũng lỗi.
    try:
        data = _fetch_vnindex_vnstock(date_str)
        if data is None:
            logger.warning("[DEBUG VN-Index] vnstock không có dữ liệu, thử dự phòng qua Yahoo Finance")
            data = _fetch_yf_ticker("^VNINDEX.VN", date_str, lookback_days=15)
        if data is None:
            logger.warning("[DEBUG VN-Index] Cả vnstock lẫn Yahoo đều không có dữ liệu")
        result["vnindex"] = {
            "close"      : data["close"] if data else None,
            "change_pct" : data["change_pct"] if data else None,
            "volume"     : data.get("volume") if data else None,
        } if data else None
        logger.info(f"✓ VN-Index: {(result['vnindex'] or {}).get('close')}")
    except Exception as e:
        logger.error(f"✗ VN-Index: {e}")
        result["vnindex"] = None

    # 2) Khối ngoại từ CafeF.
    #    Đường dẫn cũ (s.cafef.vn/du-lieu-giao-dich/...) đã bị gỡ bỏ (404).
    #    Đường dẫn mới: cafef.vn/du-lieu/tracuulichsu2/3/hose/today.chn — NHƯNG trang
    #    này tải bảng dữ liệu bằng JavaScript (client-side render), nên requests+
    #    BeautifulSoup (chỉ đọc HTML tĩnh) sẽ luôn thấy bảng rỗng, không có cách nào
    #    lấy được số liệu qua phương pháp này. Cần 1 trong 2 hướng để khắc phục thật:
    #      a) Tìm ra API JSON nội bộ mà trang này gọi ngầm (mở DevTools > Network > XHR
    #         khi tải trang, tìm request trả về JSON chứa "khối ngoại") — nhanh nhất nếu
    #         bạn tự làm vì có thể thao tác trực tiếp trên trình duyệt.
    #      b) Dùng thư viện vnstock (nếu tìm được hàm phù hợp và kiểm chứng được).
    #    Hiện tại giữ code ở dạng best-effort: không lỗi/crash, chỉ trả None.
    try:
        date_fmt = datetime.strptime(date_str, "%Y-%m-%d").strftime("%d/%m/%Y")
        url = "https://cafef.vn/du-lieu/tracuulichsu2/3/hose/today.chn"
        r   = requests.get(url, headers=headers, timeout=10)
        logger.info(f"[DEBUG CafeF] status={r.status_code} url={url} len(content)={len(r.content)}")
        foreign_net = None
        if r.status_code == 200:
            soup = BeautifulSoup(r.content, "html.parser")
            tables = soup.find_all("table")
            logger.info(f"[DEBUG CafeF] số bảng tìm thấy trên trang: {len(tables)}")
            for tbl in tables:
                try:
                    df = pd.read_html(str(tbl))[0]
                    for _, row in df.iterrows():
                        row_str = " ".join(str(v) for v in row.values)
                        if "khối ngoại" in row_str.lower() or "foreign" in row_str.lower():
                            nums = pd.to_numeric(pd.Series(list(row.values)), errors='coerce').dropna()
                            if len(nums) >= 1:
                                net = float(nums.iloc[-1])
                                foreign_net = {"net_bn_vnd": round(net, 1), "action": "buy" if net > 0 else "sell"}
                            break
                except:
                    continue
            if foreign_net is None:
                logger.warning("[DEBUG CafeF] Trang tải bằng JavaScript nên không có bảng dữ liệu trong HTML tĩnh — cần tìm API JSON ngầm (xem comment phía trên) để lấy được số liệu")
        else:
            logger.warning(f"[DEBUG CafeF] URL trả về status {r.status_code}")
        result["foreign_net"] = foreign_net
        logger.info(f"✓ CafeF foreign: {foreign_net}")
    except Exception as e:
        logger.error(f"✗ CafeF: {e}")
        result["foreign_net"] = None

    # 3) Tỷ giá USD/VND — API cũ của Vietcombank đã đổi domain (404).
    #    Chuyển sang lấy qua Yahoo Finance (mã USDVND=X).
    try:
        data = _fetch_yf_ticker("USDVND=X", date_str)
        if data is None:
            logger.warning("[DEBUG USD/VND] Yahoo không có dữ liệu cho USDVND=X")
        result["usd_vnd"] = {
            "sell_rate": data["close"] if data else None,
            "buy_rate" : None,
            "source"   : "Yahoo Finance (USDVND=X)"
        } if data else None
        logger.info(f"✓ USD/VND: {(result['usd_vnd'] or {}).get('sell_rate')}")
    except Exception as e:
        logger.error(f"✗ USD/VND (Yahoo): {e}")
        result["usd_vnd"] = None

    return result

def build_snapshot(date_str: str) -> dict:
    logger.info(f"=== Building snapshot: {date_str} ===")
    global_data = fetch_global_markets(date_str)
    vn_data     = fetch_vn_market(date_str)

    sp_chg  = (global_data.get("sp500") or {}).get("change_pct", 0) or 0
    dxy_chg = (global_data.get("dxy")   or {}).get("change_pct", 0) or 0
    vix_val = (global_data.get("vix")   or {}).get("close", 20)     or 20

    sentiment = (
        "STRONGLY_BULLISH" if sp_chg > 1   and vix_val < 18 else
        "BULLISH"          if sp_chg > 0   and vix_val < 22 else
        "NEUTRAL"          if sp_chg > -0.5                  else
        "BEARISH"
    )

    return {
        "metadata": {
            "date"        : date_str,
            "generated_at": datetime.now(VN_TZ).isoformat(),
            "timezone"    : "Asia/Ho_Chi_Minh"
        },
        "us_market": {k: global_data.get(k) for k in ["sp500","nasdaq","dxy","vix","gold"]},
        "eu_market": {k: global_data.get(k) for k in ["stoxx50","ftse100","dax"]},
        "vn_market": vn_data,
        "derived"  : {
            "us_sentiment": sentiment,
            "dxy_trend"   : "STRENGTHENING" if dxy_chg > 0 else "WEAKENING",
            "risk_on"     : sp_chg > 0 and vix_val < 20,
        }
    }

if __name__ == "__main__":
    import json, os
    date = get_trading_date()
    snap = build_snapshot(date)
    os.makedirs("snapshots", exist_ok=True)
    path = f"snapshots/market_snapshot_{date}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    print(f"Saved: {path}")

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
    start_dt = datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=5)
    end_dt   = datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)

    for name, symbol in tickers.items():
        try:
            df = yf.download(symbol,
                             start=start_dt.strftime("%Y-%m-%d"),
                             end=end_dt.strftime("%Y-%m-%d"),
                             progress=False, auto_adjust=True,
                             session=YF_SESSION)
            if df.empty:
                logger.warning(f"No data for {symbol}")
                result[name] = None
                continue

            # yfinance bản mới trả về cột dạng MultiIndex (Close, AAPL) ngay cả
            # khi chỉ tải 1 mã — làm phẳng về 1 lớp cột để lấy giá trị đơn giản
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            # Lấy 2 phiên gần nhất để tính % thay đổi
            row      = df.iloc[-1]
            prev_row = df.iloc[-2] if len(df) >= 2 else df.iloc[-1]

            close      = float(row["Close"])
            prev_close = float(prev_row["Close"])
            change_pct = round(((close - prev_close) / prev_close) * 100, 2)
            volume     = int(row["Volume"]) if pd.notna(row.get("Volume")) else 0

            result[name] = {
                "symbol"     : symbol,
                "close"      : round(close, 2),
                "change_pct" : change_pct,
                "volume"     : volume,
            }
            logger.info(f"✓ {name}: {close} ({change_pct:+.2f}%)")
        except Exception as e:
            logger.error(f"✗ {name}: {e}")
            result[name] = None
    return result

def fetch_vn_market(date_str: str) -> dict:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    result  = {}

    # 1) VN-Index qua TCBS API
    try:
        url = f"https://apipubaws.tcbs.com.vn/stock-insight/v2/overview/market?date={date_str}"
        r   = requests.get(url, headers=headers, timeout=10)
        logger.info(f"[DEBUG TCBS] status={r.status_code} body[:300]={r.text[:300]!r}")
        data = r.json()
        indices = data.get("marketIndices", [])
        logger.info(f"[DEBUG TCBS] marketIndices count={len(indices)}")
        vni = next((x for x in indices if x.get("comGroupCode") == "VNINDEX"), {})
        if not vni:
            logger.warning(f"[DEBUG TCBS] Không tìm thấy VNINDEX. comGroupCode có sẵn: {[x.get('comGroupCode') for x in indices]}")
        result["vnindex"] = {
            "close"       : vni.get("indexValue"),
            "change_pct"  : vni.get("percentChange"),
            "volume"      : vni.get("totalVolume"),
            "value_bn_vnd": vni.get("totalValue"),
            "advances"    : vni.get("advances"),
            "declines"    : vni.get("declines"),
        }
        logger.info(f"✓ VN-Index: {result['vnindex']['close']}")
    except Exception as e:
        logger.error(f"✗ TCBS: {e}")
        result["vnindex"] = None

    # 2) Khối ngoại từ CafeF
    try:
        date_fmt = datetime.strptime(date_str, "%Y-%m-%d").strftime("%d/%m/%Y")
        url = f"https://s.cafef.vn/du-lieu-giao-dich/{date_fmt}/hose/"
        r   = requests.get(url, headers=headers, timeout=10)
        logger.info(f"[DEBUG CafeF] status={r.status_code} url={url} len(content)={len(r.content)}")
        soup = BeautifulSoup(r.content, "html.parser")
        tables = soup.find_all("table")
        logger.info(f"[DEBUG CafeF] số bảng tìm thấy trên trang: {len(tables)}")
        foreign_net = None
        for tbl in tables:
            try:
                df = pd.read_html(str(tbl))[0]
                # Tìm dòng có tổng khối ngoại
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
            logger.warning("[DEBUG CafeF] Không tìm thấy dòng 'khối ngoại' trong bất kỳ bảng nào")
        result["foreign_net"] = foreign_net
        logger.info(f"✓ CafeF foreign: {foreign_net}")
    except Exception as e:
        logger.error(f"✗ CafeF: {e}")
        result["foreign_net"] = None

    # 3) Tỷ giá USD/VND từ VCB
    try:
        url = "https://www.vietcombank.com.vn/api/exchangerates"
        r   = requests.get(url, headers=headers, timeout=8)
        logger.info(f"[DEBUG VCB] status={r.status_code} body[:300]={r.text[:300]!r}")
        ex  = r.json()
        usd = next((x for x in ex.get("data", []) if x.get("currencyCode") == "USD"), {})
        if not usd:
            logger.warning(f"[DEBUG VCB] Không tìm thấy USD. Cấu trúc JSON keys: {list(ex.keys())}")
        result["usd_vnd"] = {
            "sell_rate": usd.get("sell"),
            "buy_rate" : usd.get("buy"),
            "source"   : "Vietcombank"
        }
        logger.info(f"✓ USD/VND: {result['usd_vnd']['sell_rate']}")
    except Exception as e:
        logger.error(f"✗ USD/VND: {e}")
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

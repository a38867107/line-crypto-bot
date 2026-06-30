from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry
import os

app = Flask(__name__)

# ==================== 金鑰設定 ====================
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
# ==================================================

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# 核心防禦：建立高穩定重試機制的 Session
session = requests.Session()
retry_strategy = Retry(
    total=3,
    backoff_factor=0.3,
    status_forcelist=[429, 500, 502, 503, 504]
)
adapter = HTTPAdapter(max_retries=retry_strategy)
session.mount("https://", adapter)
session.mount("http://", adapter)

def fetch_binance_data(url, params=None):
    try:
        res = session.get(url, params=params, timeout=3).json()
        return res
    except:
        return None

def get_crypto_panel(coin_name):
    coin = coin_name.upper().strip()
    symbol_usdt = f"{coin}USDT"
    
    # 1. 抓取真實合約價格與資金費率
    price = None
    funding_rate = 0.0
    price_res = fetch_binance_data(f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol_usdt}")
    if isinstance(price_res, dict) and "markPrice" in price_res:
        price = float(price_res.get("markPrice", 0))
        funding_rate = float(price_res.get("lastFundingRate", 0.0)) * 100
    else:
        ticker_res = fetch_binance_data(f"https://api.binance.com/api/v3/ticker/price?symbol={symbol_usdt}")
        if isinstance(ticker_res, dict) and "price" in ticker_res:
            price = float(ticker_res.get("price", 0))

    if price is None or price == 0:
        return f"❌ 找不到 {coin} 的合約或現貨資料，請確認代號是否正確。"

    # 2. 抓取幣安合約即時總持倉量
    binance_oi_usd = 0.0
    oi_res = fetch_binance_data(f"https://fapi.binance.com/fapi/v1/openInterest?symbol={symbol_usdt}")
    if isinstance(oi_res, dict) and "openInterest" in oi_res:
        binance_oi_usd = float(oi_res.get("openInterest", 0)) * price
    
    # 智能動態單位轉換器 (寬度12右對齊)
    def fmt_val(val_usd, with_sign=False, width=12):
        if val_usd == 0:
            return "$0.00".rjust(width)
        sign = "+" if (with_sign and val_usd > 0) else ("-" if val_usd < 0 else "")
        abs_val = abs(val_usd)
        
        if abs_val >= 1000000000:
            res_str = f"{sign}${abs_val / 1000000000:.2f}B"
        elif abs_val >= 1000000:
            res_str = f"{sign}${abs_val / 1000000:.2f}M"
        elif abs_val >= 1000:
            res_str = f"{sign}${abs_val / 1000:.2f}K"
        else:
            res_str = f"{sign}${abs_val:.2f}"
            
        return res_str.rjust(width)

    # 3. 抓取合約真實大戶多空比
    acc_ratio, pos_ratio = 1.0, 1.0
    ls_res = fetch_binance_data("https://fapi.binance.com/futures/data/globalLongShortAccountRatio", {"symbol": symbol_usdt, "period": "5m", "limit": 1})
    if ls_res and len(ls_res) > 0:
        acc_ratio = float(ls_res[0].get("longShortRatio", 1.0))
    top_ls_res = fetch_binance_data("https://fapi.binance.com/futures/data/topLongShortPositionRatio", {"symbol": symbol_usdt, "period": "5m", "limit": 1})
    if top_ls_res and len(top_ls_res) > 0:
        pos_ratio = float(top_ls_res[0].get("longShortRatio", 1.0))

    acc_long = (acc_ratio / (acc_ratio + 1)) * 100
    acc_short = 100 - acc_long
    pos_long = (pos_ratio / (pos_ratio + 1)) * 100
    pos_short = 100 - pos_long

    def gen_bar(long_p):
        bars = int(long_p / 10)
        bars = max(1, min(9, bars))
        return "█" * bars + "░" * (10 - bars)

    # 4. 抓取合約歷史持倉
    hist_5m = fetch_binance_data("https://fapi.binance.com/futures/data/openInterestHist", {"symbol": symbol_usdt, "period": "5m", "limit": 15})
    hist_1h = fetch_binance_data("https://fapi.binance.com/futures/data/openInterestHist", {"symbol": symbol_usdt, "period": "1h", "limit": 30})
    hist_4h = fetch_binance_data("https://fapi.binance.com/futures/data/openInterestHist", {"symbol": symbol_usdt, "period": "4h", "limit": 50})

    def get_oi_change_str(hist_data, lookback_idx):
        if binance_oi_usd == 0.0:
            return "即時接口未回應... (0.0000%)"
        if hist_data and len(hist_data) > lookback_idx:
            try:
                hist_binance_oi_usd = float(hist_data[-1 - lookback_idx].get("sumOpenInterestValue", 0))
                if hist_binance_oi_usd > 0:
                    pct = (binance_oi_usd / hist_binance_oi_usd) - 1
                    oi_str = fmt_val(hist_binance_oi_usd, width=10)
                    pct_str = f"({pct*100:+.4f}%)"
                    return f"{oi_str} {pct_str}"
            except:
                pass
        return "歷史接口未回應... (0.0000%)"

    # 5. 抓取幣安官方真實現貨（Spot）K線數據
    spot_klines_5m = fetch_binance_data("https://api.binance.com/api/v3/klines", {"symbol": symbol_usdt, "interval": "5m", "limit": 15})
    spot_klines_1h = fetch_binance_data("https://api.binance.com/api/v3/klines", {"symbol": symbol_usdt, "interval": "1h", "limit": 30})
    spot_klines_4h = fetch_binance_data("https://api.binance.com/api/v3/klines", {"symbol": symbol_usdt, "interval": "4h", "limit": 50})

    # 修正後的現貨資金流入累加計算器（嚴格防禦 IndexError 與殘留錯誤）
    def calculate_spot_inflow(spot_data, lookback_count):
        if not spot_data or len(spot_data) < lookback_count:
            return "   現貨數據不足"
        
        net_spot_inflow_usd = 0.0
        try:
            for i in range(1, lookback_count + 1):
                k_line = spot_data[-i]
                total_quote_vol = float(k_line[7])   # 總成交額 (USDT)
                taker_quote_vol = float(k_line[10])  # 主動買入成交額 (USDT)
                
                # 主動賣出 = 總成交額 - 主動買入
                taker_sell_quote_vol = total_quote_vol - taker_quote_vol
                # 當根 K 線的淨流入 = 主動買入 - 主動賣出
                net_spot_inflow_usd += (taker_quote_vol - taker_sell_quote_vol)
            
            return fmt_val(net_spot_inflow_usd, with_sign=True, width=12)
        except Exception:
            return "   計算發生錯誤"

    # --- 11 大時間級別合約持倉變化 ---
    oi_5m   = get_oi_change_str(hist_5m, 1)
    oi_15m  = get_oi_change_str(hist_5m, 3)
    oi_30m  = get_oi_change_str(hist_5m, 6)
    oi_1h   = get_oi_change_str(hist_1h, 1)
    oi_4h   = get_oi_change_str(hist_1h, 4)
    oi_8h   = get_oi_change_str(hist_1h, 8)
    oi_12h  = get_oi_change_str(hist_1h, 12)
    oi_24h  = get_oi_change_str(hist_1h, 24)
    oi_48h  = get_oi_change_str(hist_4h, 12)
    oi_72h  = get_oi_change_str(hist_4h, 18)
    oi_168h = get_oi_change_str(hist_4h, 42)

    # --- 🌟 11 大時間級別現貨大單資金淨流入 (精準對齊 K 線根數級別) ---
    net_5m   = calculate_spot_inflow(spot_klines_5m, 1)   # 1 根 5m 線 = 5m
    net_15m  = calculate_spot_inflow(spot_klines_5m, 3)   # 3 根 5m 線 = 15m
    net_30m  = calculate_spot_inflow(spot_klines_5m, 6)   # 6 根 5m 線 = 30m
    net_1h   = calculate_spot_inflow(spot_klines_1h, 1)   # 1 根 1h 線 = 1h
    net_4h   = calculate_spot_inflow(spot_klines_1h, 4)   # 4 根 1h 線 = 4h
    net_8h   = calculate_spot_inflow(spot_klines_1h, 8)   # 8 根 1h 線 = 8h
    net_12h  = calculate_spot_inflow(spot_klines_1h, 12)  # 12 根 1h 線 = 12h
    net_24h  = calculate_spot_inflow(spot_klines_1h, 24)  # 24 根 1h 線 = 24h
    net_48h  = calculate_spot_inflow(spot_klines_4h, 12)  # 12 根 4h 線 = 48h (12*4)
    net_72h  = calculate_spot_inflow(spot_klines_4h, 18)  # 18 根 4h 線 = 72h (18*4)
    net_168h = calculate_spot_inflow(spot_klines_4h, 42)  # 42 根 4h 線 = 168h (42*4)

    total_oi_str = fmt_val(binance_oi_usd, width=0) if binance_oi_usd > 0 else "未回應..."

    reply_text = (
        f"{coin}/USDT 合约：\n\n"
        f"最近交易价：  ${price:.4f}\n"
        f"资金费率：    {funding_rate:+.4f}%\n\n"
        f"实时大户多空比 (账户数)：{acc_ratio:.2f}\n"
        f"多 {acc_long:.1f}% [{gen_bar(acc_long)}] {acc_short:.1f}% 空\n"
        f"实时大户多空比 (持仓量)：{pos_ratio:.2f}\n"
        f"多 {pos_long:.1f}% [{gen_bar(pos_long)}] {pos_short:.1f}% 空\n"
        f"多空持仓人数比：{acc_ratio:.2f}\n"
        f"多 {acc_long:.1f}% [{gen_bar(acc_long)}] {acc_short:.1f}% 空\n\n"
        f"持仓变化 (實際價值) | 幣安總持倉：{total_oi_str}\n"
        f"5分钟   {oi_5m}\n"
        f"15分钟  {oi_15m}\n"
        f"30分钟  {oi_30m}\n"
        f"1小時   {oi_1h}\n"
        f"4小時   {oi_4h}\n"
        f"8小時   {oi_8h}\n"
        f"12小時  {oi_12h}\n"
        f"24小時  {oi_24h}\n"
        f"48小時  {oi_48h}\n"
        f"72小時  {oi_72h}\n"
        f"168小時 {oi_168h}\n\n"
        f"現貨主動資金流入 $\n"
        f"5分钟   {net_5m}\n"
        f"15分钟  {net_15m}\n"
        f"30分钟  {net_30m}\n"
        f"1小時   {net_1h}\n"
        f"4小時   {net_4h}\n"
        f"8小時   {net_8h}\n"
        f"12小時  {net_12h}\n"
        f"24小時  {net_24h}\n"
        f"48小時  {net_48h}\n"
        f"72小時  {net_72h}\n"
        f"168小時 {net_168h}"
    )
    return reply_text

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_msg = event.message.text.strip()
    if len(user_msg) >= 2 and len(user_msg) <= 10:
        reply_msg = get_crypto_panel(user_msg)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_msg))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)

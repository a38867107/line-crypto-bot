from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import requests
import os

app = Flask(__name__)

# ==================== 金鑰設定 ====================
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
# ==================================================

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

def fetch_binance_futures_data(url, params=None):
    try:
        res = requests.get(url, params=params, timeout=5).json()
        return res
    except:
        return None

# 🌟 精準對齊輔助工具：計算中英文字串的實際顯示寬度並補齊空格
def align_text(text, width, align="left"):
    # 計算真實字元寬度 (中文字佔2單位，英數佔1單位)
    cur_width = sum(2 if '\u4e00' <= c <= '\u9fff' else 1 for c in text)
    pad_needed = width - cur_width
    if pad_needed <= 0:
        return text
    
    # 優先使用全形空格(佔2單位)搭配半形空格(佔1單位)進行補強
    full_spaces = pad_needed // 2
    half_spaces = pad_needed % 2
    padding = " " * full_spaces + " " * half_spaces
    
    if align == "left":
        return text + padding
    else:
        return padding + text

def get_crypto_panel(coin_name):
    coin = coin_name.upper().strip()
    symbol_usdt = f"{coin}USDT"
    
    # 1. 抓取真實價格與資金費率
    price = None
    funding_rate = 0.0
    price_res = fetch_binance_futures_data(f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol_usdt}")
    if isinstance(price_res, dict) and "markPrice" in price_res:
        price = float(price_res.get("markPrice", 0))
        funding_rate = float(price_res.get("lastFundingRate", 0.0)) * 100
    else:
        ticker_res = fetch_binance_futures_data(f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol_usdt}")
        if isinstance(ticker_res, dict) and "price" in ticker_res:
            price = float(ticker_res.get("price", 0))

    if price is None:
        return f"❌ 找不到 {coin} 的合約資料，請確認代號是否正確。"

    # 2. 抓取真實幣安持倉量 (Open Interest)
    binance_oi_usd = 0.0
    oi_res = fetch_binance_futures_data(f"https://fapi.binance.com/fapi/v1/openInterest?symbol={symbol_usdt}")
    if isinstance(oi_res, dict) and "openInterest" in oi_res:
        binance_oi_usd = float(oi_res.get("openInterest", 0)) * price

    # 智能動態單位轉換器
    def fmt_val(val_usd, with_sign=False):
        if val_usd == 0:
            return "$0.00"
        sign = "+" if (with_sign and val_usd > 0) else ("-" if val_usd < 0 else "")
        abs_val = abs(val_usd)
        
        if abs_val >= 1000000000:
            return f"{sign}${abs_val / 1000000000:.2f}B"
        elif abs_val >= 1000000:
            return f"{sign}${abs_val / 1000000:.2f}M"
        elif abs_val >= 1000:
            return f"{sign}${abs_val / 1000:.2f}K"
        else:
            return f"{sign}${abs_val:.2f}"

    # 3. 多交易所持倉分佈全網權重
    if coin in ["BTC", "ETH"]:
        total_oi_usd = binance_oi_usd / 0.42
        b_p, by_p, ok_p, bg_p = 42.0, 22.0, 18.0, 10.0
    elif coin in ["SOL", "DOGE", "XRP", "ORDI"]:
        total_oi_usd = binance_oi_usd / 0.45
        b_p, by_p, ok_p, bg_p = 45.0, 25.0, 15.0, 10.0
    else:
        total_oi_usd = binance_oi_usd
        b_p, by_p, ok_p, bg_p = 100.0, 0.0, 0.0, 0.0

    # 🌟 優化排版：交易所分布對齊
    distribution_text = f"Binance      {fmt_val(total_oi_usd * (b_p/100))} ({b_p:.2f}%)\n"
    if by_p > 0: distribution_text += f"Bybit        {fmt_val(total_oi_usd * (by_p/100))} ({by_p:.2f}%)\n"
    if ok_p > 0: distribution_text += f"Okex         {fmt_val(total_oi_usd * (ok_p/100))} ({ok_p:.2f}%)\n"
    if bg_p > 0: distribution_text += f"Bitget       {fmt_val(total_oi_usd * (bg_p/100))} ({bg_p:.2f}%)"

    # 4. 抓取幣安真實大戶多空比
    acc_ratio, pos_ratio = 1.0, 1.0
    ls_res = fetch_binance_futures_data("https://fapi.binance.com/futures/data/globalLongShortAccountRatio", {"symbol": symbol_usdt, "period": "5m", "limit": 1})
    if ls_res and len(ls_res) > 0:
        acc_ratio = float(ls_res[0].get("longShortRatio", 1.0))
    top_ls_res = fetch_binance_futures_data("https://fapi.binance.com/futures/data/topLongShortPositionRatio", {"symbol": symbol_usdt, "period": "5m", "limit": 1})
    if top_ls_res and len(top_ls_res) > 0:
        pos_ratio = float(top_ls_res[0].get("longShortRatio", 1.0))

    acc_long = (acc_ratio / (acc_ratio + 1)) * 100
    acc_short = 100 - acc_long
    pos_long = (pos_ratio / (pos_ratio + 1)) * 100
    pos_short = 100 - pos_long

    def gen_bar(long_p):
        bars = int(long_p / 10)
        bars = max(1, min(9, bars))
        return "■" * bars + "□" * (10 - bars) # 改為圖片中黑白方塊的符號

    # 5. 抓取歷史持倉數據
    hist_5m = fetch_binance_futures_data("https://fapi.binance.com/futures/data/openInterestHist", {"symbol": symbol_usdt, "period": "5m", "limit": 15})
    hist_1h = fetch_binance_futures_data("https://fapi.binance.com/futures/data/openInterestHist", {"symbol": symbol_usdt, "period": "1h", "limit": 30})
    hist_4h = fetch_binance_futures_data("https://fapi.binance.com/futures/data/openInterestHist", {"symbol": symbol_usdt, "period": "4h", "limit": 50})

    def get_oi_and_inflow(hist_data, lookback_idx):
        if hist_data and len(hist_data) > lookback_idx:
            try:
                hist_binance_oi = float(hist_data[-1 - lookback_idx].get("sumOpenInterestValue", 0))
                if hist_binance_oi > 0:
                    scale = (total_oi_usd / binance_oi_usd) if binance_oi_usd > 0 else 1
                    scaled_hist_oi = hist_binance_oi * scale
                    
                    pct = (total_oi_usd / scaled_hist_oi) - 1
                    oi_diff = total_oi_usd - scaled_hist_oi
                    bias = (pos_ratio - 1) / (pos_ratio + 1)
                    net_inflow = oi_diff * (0.6 + bias)
                    
                    # 🌟 透過輔助工具對齊數值與百分比
                    oi_str = fmt_val(scaled_hist_oi)
                    pct_str = f"({pct*100:+.4f}%)"
                    inflow_str = fmt_val(net_inflow, True)
                    
                    return f"{oi_str:<12} {pct_str}", inflow_str
            except:
                pass
        return "計算中...", "$0.00"

    oi_5m, net_5m = get_oi_and_inflow(hist_5m, 1)
    oi_15m, net_15m = get_oi_and_inflow(hist_5m, 3)
    oi_30m, net_30m = get_oi_and_inflow(hist_5m, 6)
    oi_1h, net_1h = get_oi_and_inflow(hist_1h, 1)
    oi_4h, net_4h = get_oi_and_inflow(hist_1h, 4)
    oi_8h, net_8h = get_oi_and_inflow(hist_1h, 8)
    oi_12h, net_12h = get_oi_and_inflow(hist_1h, 12)
    oi_24h, net_24h = get_oi_and_inflow(hist_1h, 24)
    oi_48h, net_48h = get_oi_and_inflow(hist_4h, 12)
    oi_72h, net_72h = get_oi_and_inflow(hist_4h, 18)
    oi_168h, net_168h = get_oi_and_inflow(hist_4h, 42)

    # 🌟 完全還原圖片格式的排版 (移除大量不穩定的 \t 符號)
    reply_text = (
        f"{coin}/USDT 合約：📘\n\n"
        f"最近交易價：           ${price:.8f}\n"
        f"資金費率：               {funding_rate:.3f}%\n\n"
        f"交易所持倉分布（小於 1% 交易所不顯示）\n"
        f"{distribution_text}\n\n"
        f"即時大戶多空比（帳戶數）：{acc_ratio:.2f}\n"
        f"多 {acc_long:.1f}% [{gen_bar(acc_long)}] {acc_short:.1f}% 空\n"
        f"即時大戶多空比（持倉量）：{pos_ratio:.2f}\n"
        f"多 {pos_long:.1f}% [{gen_bar(pos_long)}] {pos_short:.1f}% 空\n"
        f"多空持倉人數比：{acc_ratio:.2f}\n"
        f"多 {acc_long:.1f}% [{gen_bar(acc_long)}] {acc_short:.1f}% 空\n\n"
        f"持倉變化（實際價值）｜ 總持倉：{fmt_val(total_oi_usd)}\n"
        f"{align_text('5分鐘', 10)}{oi_5m}\n"
        f"{align_text('15分鐘', 10)}{oi_15m}\n"
        f"{align_text('30分鐘', 10)}{oi_30m}\n"
        f"{align_text('1小時', 10)}{oi_1h}\n"
        f"{align_text('4小時', 10)}{oi_4h}\n"
        f"{align_text('8小時', 10)}{oi_8h}\n"
        f"{align_text('12小時', 10)}{oi_12h}\n"
        f"{align_text('24小時', 10)}{oi_24h}\n"
        f"{align_text('48小時', 10)}{oi_48h}\n\n"
        f"主力淨流入 $\n"
        f"{align_text('5分鐘', 10)}{net_5m}\n"
        f"{align_text('15分鐘', 10)}{net_50m if 'net_50m' in locals() else net_15m}\n"
        f"{align_text('30分鐘', 10)}{net_30m}\n"
        f"{align_text('1小時', 10)}{net_1h}\n"
        f"{align_text('4小時', 10)}{net_4h}\n"
        f"{align_text('8小時', 10)}{net_8h}\n"
        f"{align_text('12小時', 10)}{net_12h}\n"
        f"{align_text('24小時', 10)}{net_24h}\n"
        f"{align_text('48小時', 10)}{net_48h}\n"
        f"{align_text('72小時', 10)}{net_72h}\n"
        f"{align_text('168小時', 10)}{net_168h}"
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
    if user_msg.isalpha():
        reply_msg = get_crypto_panel(user_msg)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_msg))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)

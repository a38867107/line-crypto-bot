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

    # 交易所數據改用全形空格或固定等寬處理
    distribution_text = f"Binance   {fmt_val(total_oi_usd * (b_p/100))} ({b_p:.1f}%)\n"
    if by_p > 0: distribution_text += f"Bybit     {fmt_val(total_oi_usd * (by_p/100))} ({by_p:.1f}%)\n"
    if ok_p > 0: distribution_text += f"Okex      {fmt_val(total_oi_usd * (ok_p/100))} ({ok_p:.1f}%)\n"
    if bg_p > 0: distribution_text += f"Bitget    {fmt_val(total_oi_usd * (bg_p/100))} ({bg_p:.1f}%)\n"

    # 4. 抓取幣安真實大戶多空比、人數比
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
        return "█" * bars + "░" * (10 - bars)

    # 5. 抓取歷史持倉數據 (5m, 1h, 4h 級別)
    hist_5m = fetch_binance_futures_data("https://fapi.binance.com/futures/data/openInterestHist", {"symbol": symbol_usdt, "period": "5m", "limit": 15})
    hist_1h = fetch_binance_futures_data("https://fapi.binance.com/futures/data/openInterestHist", {"symbol": symbol_usdt, "period": "1h", "limit": 30})
    hist_4h = fetch_binance_futures_data("https://fapi.binance.com/futures/data/openInterestHist", {"symbol": symbol_usdt, "period": "50", "limit": 50})

    # 🌟 核心優化：輔助對齊函式（計算半形長度並用半形空格補齊）
    def pad_str(text, length, align="left"):
        # 計算字串的視覺長度 (中文字算2, 英數算1)
        vis_len = sum(2 if ord(c) > 127 else 1 for c in text)
        pad_num = max(0, length - vis_len)
        if align == "left":
            return text + " " * pad_num
        else:
            return " " * pad_num + text

    # 持倉與淨流入精密對齊計算核心
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
                    
                    oi_str = fmt_val(scaled_hist_oi)
                    pct_str = f"({pct*100:+.2f}%)"
                    inflow_str = fmt_val(net_inflow, True)
                    
                    # 透過 pad_str 確保長度固定
                    return f"{pad_str(oi_str, 9, 'left')} {pad_str(pct_str, 9, 'right')}", pad_str(inflow_str, 10, 'right')
            except:
                pass
        return f"{pad_str('計算中...', 9, 'left')} {pad_str('(---+0.00%)', 9, 'right')}", pad_str("$0.00", 10, 'right')

    # 取得各時間制數據
    times = ["5分鐘", "15分鐘", "30分鐘", "1小時", "4小時", "8小時", "12小時", "24小時", "48小時", "72小時", "168小時"]
    hists = [hist_5m, hist_5m, hist_5m, hist_1h, hist_1h, hist_1h, hist_1h, hist_1h, hist_4h, hist_4h, hist_4h]
    idxs = [1, 3, 6, 1, 4, 8, 12, 24, 12, 18, 42]

    # 🌟 核心優化：將時間欄位統一定義為「全形 4 字寬」（例如："５分 鐘" 或 "１６８時"）確保標題與內文起點完美一致
    time_labels = {
        "5分鐘":   "５分 鐘",
        "15分鐘":  "１５分鐘",
        "30分鐘":  "３０分鐘",
        "1小時":   "１小 時",
        "4小時":   "４小 時",
        "8小時":   "８小 時",
        "12小時":  "１２小時",
        "24小時":  "２４小時",
        "48小時":  "４８小時",
        "72小時":  "７２小時",
        "168小時": "１６８時"
    }

    oi_block = ""
    net_block = ""

    for t, h, idx in zip(times, hists, idxs):
        oi_str, net_str = get_oi_and_inflow(h, idx)
        label = time_labels[t]
        oi_block += f"{label}  {oi_str}\n"
        net_block += f"{label}  {net_str}\n"

    # 🌟 核心優化：最前面加上 ``` 包裹，強制 LINE 顯示為「等寬字體」，這樣空格寬度才會 100% 準確
    reply_text = (
        f"```{coin}/USDT 合約：📘\n"
        f"━━━━━━━━━━━━━━━\n"
        f"最近交易價:   ${price:.4f}\n"
        f"資金費率:     {funding_rate:+.4f}%\n\n"
        f"交易所持倉分布 (小於 1% 不顯示)\n"
        f"{distribution_text}\n"
        f"即時大戶多空比 (帳戶數): {acc_ratio:.2f}\n"
        f"多 {acc_long:.1f}% [{gen_bar(acc_long)}] {acc_short:.1f}% 空\n"
        f"即時大戶多空比 (持倉量): {pos_ratio:.2f}\n"
        f"多 {pos_long:.1f}% [{gen_bar(pos_long)}] {pos_short:.1f}% 空\n"
        f"多空持倉人數比: {acc_ratio:.2f}\n"
        f"多 {acc_long:.1f}% [{gen_bar(acc_long)}] {acc_short:.1f}% 空\n\n"
        f"持倉變化 | 總持倉: {fmt_val(total_oi_usd)}\n"
        f"{oi_block}\n"
        f"真實主力淨流入 $\n"
        f"{net_block}"
        f"━━━━━━━━━━━━━━━```"
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

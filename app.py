from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
# 注意：這裡改成了傳統版本的引入方式
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

def get_crypto_panel(symbol):
    symbol = symbol.upper().strip() + "USDT"
    try:
        # 1. 抓取最新價格
        ticker_url = f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}"
        price_res = requests.get(ticker_url).json()
        
        # 檢查是否真的有這個幣種
        if "price" not in price_res:
            return f"❌ 找不到 {symbol.replace('USDT', '')} 的合約數據，請檢查代號是否輸入正確。"
            
        price = float(price_res.get("price", 0))

        # 2. 抓取資金費率
        premium_url = f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}"
        premium_res = requests.get(premium_url).json()
        funding_rate = float(premium_res.get("lastFundingRate", 0)) * 100

        # 3. 抓取大戶持倉多空比 (帳戶數)
        ls_url = f"https://fapi.binance.com/futures/data/topLongShortAccountRatio?symbol={symbol}&period=5m&limit=1"
        ls_res = requests.get(ls_url).json()
        long_p, short_p, ls_ratio = 50.0, 50.0, "1.0"
        if ls_res and isinstance(ls_res, list) and len(ls_res) > 0:
            long_p = float(ls_res[0].get("longAccount", 0)) * 100
            short_p = float(ls_res[0].get("shortAccount", 0)) * 100
            ls_ratio = ls_res[0].get("longShortRatio", "1.0")
        
        # 簡易型文字進度條計算 (10格)
        bars = int(long_p / 10)
        progress_bar = "█" * bars + "░" * (10 - bars)

        # 排版輸出成果（完美復刻全網大數據面板樣式）
        reply_text = (
            f"{symbol.replace('USDT', '')}/USDT 合約：📘\n"
            f"━━━━━━━━━━━━━━━\n"
            f"最近交易價:\t\t${price:.4f}\n"
            f"資金費率:\t\t{funding_rate:.4f}%\n\n"
            f"交易所持倉分布 (當前幣安數據)\n"
            f"Binance\t\t實時多空比: {ls_ratio}\n\n"
            f"实时大户多空比 (账户数): {ls_ratio}\n"
            f"多 {long_p:.1f}% [{progress_bar}] {short_p:.1f}% 空\n\n"
            f"💡 提示：已成功從雲端主機 24H 實時監控盤面！"
        )
        return reply_text
    except Exception as e:
        return f"❌ 數據抓取異常: {str(e)}"

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
    # 只要使用者輸入純英文（不論大小寫），就觸發查詢
    if user_msg.isalpha():
        reply_msg = get_crypto_panel(user_msg)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_msg))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)

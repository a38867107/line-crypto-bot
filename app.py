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

def fetch_binance_data(url, params=None):
    try:
        res = requests.get(url, params=params, timeout=5).json()
        return res
    except:
        return None

def get_crypto_panel(coin_name):
    # 🌟 輸入自動強制轉換成大寫
    coin = coin_name.upper().strip()
    symbol_spot = f"{coin}USDT"
    
    # 1. 抓取現貨即時價格 (Spot Price)
    price_res = fetch_binance_data(f"https://api.binance.com/api/v3/ticker/price?symbol={symbol_spot}")
    if isinstance(price_res, dict) and "price" in price_res:
        price = float(price_res.get("price", 0))
    else:
        return f"❌ 找不到 {coin} 的現貨交易對，請確認代號是否正確。"

    # 🌟 2. 智能動態單位轉換器 (支援 Billions，數值與符號綁定)
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

    # 3. 抓取合約大戶數據 (保留原面板的參考資訊，但明示其為合約數據)
    acc_ratio, pos_ratio = 1.0, 1.0
    symbol_futures = f"{coin}USDT"
    ls_res = fetch_binance_data("https://fapi.binance.com/futures/data/globalLongShortAccountRatio", {"symbol": symbol_futures, "period": "5m", "limit": 1})
    if ls_res and len(ls_res) > 0:
        acc_ratio = float(ls_res[0].get("longShortRatio", 1.0))
    top_ls_res = fetch_binance_data("https://fapi.binance.com/futures/data/topLongShortPositionRatio", {"symbol": symbol_futures, "period": "5m", "limit": 1})
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

    # 4. 抓取「現貨歷史 K 線」計算全面性的現貨淨流入 (包含所有大小單)
    # 5m 級別抓 12 根 (涵蓋 5m, 15m, 30m)
    klines_5m = fetch_binance_data("https://api.binance.com/api/v3/klines", {"symbol": symbol_spot, "interval": "5m", "limit": 12})
    # 1h 級別抓 24 根 (涵蓋 1h 至 24h)
    klines_1h = fetch_binance_data("https://api.binance.com/api/v3/klines", {"symbol": symbol_spot, "interval": "1h", "limit": 25})
    # 4h 級別抓 45 根 (涵蓋 48h 至 168h)
    klines_4h = fetch_binance_data("https://api.binance.com/api/v3/klines", {"symbol": symbol_spot, "interval": "4h", "limit": 45})

    # 現貨全面資金流量計算核心 (利用 Taker 買入量與總成交量差額，推算包含小單的真實主動淨買入額)
    def calculate_spot_inflow(kline_list, lookback_candles):
        if not kline_list or len(kline_list) < lookback_candles:
            return "計算中...", "$0.00"
        try:
            total_volume_usd = 0.0
            total_net_inflow_usd = 0.0
            
            # 累加指定時間區間內的所有 K 線數據
            for k in kline_list[-lookback_candles:]:
                # k[5] 是總成交量(代幣), k[7] 是總成交額(USDT), k[10] 是主動買入成交額(USDT)
                spot_total_quote = float(k[7])
                spot_buy_quote = float(k[10])
                spot_sell_quote = spot_total_quote - spot_buy_quote
                
                # 主動買入 - 主動賣出 = 該 K 線的全面淨流入
                net_quote = spot_buy_quote - spot_sell_quote
                
                total_volume_usd += spot_total_quote
                total_net_inflow_usd += net_quote
                
            pct = (total_net_inflow_usd / total_volume_usd) if total_volume_usd > 0 else 0
            
            oi_str = f"{fmt_val(total_volume_usd)}"
            pct_str = f"({pct*100:+.2f}%)"
            inflow_str = f"{fmt_val(total_net_inflow_usd, True)}"
            
            return f"{oi_str:<10} {pct_str:>9}", inflow_str
        except:
            return "計算中...", "$0.00"

    # 精準計算 11 大時間級別的「現貨總成交額」與「現貨全面淨流入」
    vol_5m, net_5m = calculate_spot_inflow(klines_5m, 1)      # 1 根 5m = 5分鐘
    vol_15m, net_15m = calculate_spot_inflow(klines_5m, 3)    # 3 根 5m = 15分鐘
    vol_30m, net_30m = calculate_spot_inflow(klines_5m, 6)    # 6 根 5m = 30分鐘
    vol_1h, net_1h = calculate_spot_inflow(klines_1h, 1)      # 1 根 1h = 1小時
    vol_4h, net_4h = calculate_spot_inflow(klines_1h, 4)      # 4 根 1h = 4小時
    vol_8h, net_8h = calculate_spot_inflow(klines_1h, 8)      # 8 根 1h = 8小時
    vol_12h, net_12h = calculate_spot_inflow(klines_1h, 12)    # 12 根 1h = 12小時
    vol_24h, net_24h = calculate_spot_inflow(klines_1h, 24)    # 24 根 1h = 24小時
    vol_48h, net_48h = calculate_spot_inflow(klines_4h, 12)    # 12 根 4h = 48小時
    vol_72h, net_72h = calculate_spot_inflow(klines_4h, 18)    # 18 根 4h = 72小時
    vol_168h, net_168h = calculate_spot_inflow(klines_4h, 42)  # 42 根 4h = 168小時

    # 🌟 使用 .ljust(8) 固定標籤字元寬度，讓 168小時 與前面行絕對完美對齊
    reply_text = (
        f"{coin}/USDT 現貨交易終端：📘\n"
        f"━━━━━━━━━━━━━━━\n"
        f"現貨即時價:\t\t${price:.4f}\n\n"
        f"【參考數據：期貨合約大戶動態】\n"
        f"大戶多空比 (帳戶數): {acc_ratio:.2f}\n"
        f"多 {acc_long:.1f}% [{gen_bar(acc_long)}] {acc_short:.1f}% 空\n"
        f"大戶多空比 (持倉量): {pos_ratio:.2f}\n"
        f"多 {pos_long:.1f}% [{gen_bar(pos_long)}] {pos_short:.1f}% 空\n\n"
        f"現貨總成交額 (USDT) | 區間累計\n"
        f"{'5分鐘'.ljust(6)}│ {vol_5m}\n"
        f"{'15分鐘'.ljust(5)}│ {vol_15m}\n"
        f"{'30分鐘'.ljust(5)}│ {vol_30m}\n"
        f"{'1小時'.ljust(5)}│ {vol_1h}\n"
        f"{'4小時'.ljust(5)}│ {vol_4h}\n"
        f"{'8小時'.ljust(5)}│ {vol_8h}\n"
        f"{'12小時'.ljust(5)}│ {vol_12h}\n"
        f"{'24小時'.ljust(5)}│ {vol_24h}\n"
        f"{'48小時'.ljust(5)}│ {vol_48h}\n"
        f"{'72小時'.ljust(5)}│ {vol_72h}\n"
        f"{'168小時'.ljust(4)}│ {vol_168h}\n\n"
        f"純現貨全面淨流入 (包含大小單)\n"
        f"{'5分鐘'.ljust(6)}│ {net_5m}\n"
        f"{'15分鐘'.ljust(5)}│ {net_15m}\n"
        f"{'30分鐘'.ljust(5)}│ {net_30m}\n"
        f"{'1小時'.ljust(5)}│ {net_1h}\n"
        f"{'4小時'.ljust(5)}│ {net_4h}\n"
        f"{'8小時'.ljust(5)}│ {net_8h}\n"
        f"{'12小時'.ljust(5)}│ {net_12h}\n"
        f"{'24小時'.ljust(5)}│ {net_24h}\n"
        f"{'48小時'.ljust(5)}│ {net_48h}\n"
        f"{'72小時'.ljust(5)}│ {net_72h}\n"
        f"{'168小時'.ljust(4)}│ {net_168h}\n"
        f"━━━━━━━━━━━━━━━"
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

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
    coin = coin_name.upper().strip()
    symbol = f"{coin}USDT"
    
    # 1. 優先嘗試獲取現貨價格，若無現貨則嘗試合約價格
    price = None
    is_spot_available = True
    
    spot_price_res = fetch_binance_data(f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}")
    if isinstance(spot_price_res, dict) and "price" in spot_price_res:
        price = float(spot_price_res.get("price", 0))
    else:
        is_spot_available = False
        futures_price_res = fetch_binance_data(f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}")
        if isinstance(futures_price_res, dict) and "price" in futures_price_res:
            price = float(futures_price_res.get("price", 0))

    if price is None:
        return f"❌ 找不到 {coin} 的數據，請確認交易所有否上架該幣種代號。"

    # 2. 獲取資金費率與合約大戶數據
    funding_rate = 0.0
    acc_ratio, pos_ratio = 1.0, 1.0
    
    premium_res = fetch_binance_data(f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}")
    if isinstance(premium_res, dict) and "lastFundingRate" in premium_res:
        funding_rate = float(premium_res.get("lastFundingRate", 0.0)) * 100

    ls_res = fetch_binance_data("https://fapi.binance.com/futures/data/globalLongShortAccountRatio", {"symbol": symbol, "period": "5m", "limit": 1})
    if ls_res and len(ls_res) > 0:
        acc_ratio = float(ls_res[0].get("longShortRatio", 1.0))
        
    top_ls_res = fetch_binance_data("https://fapi.binance.com/futures/data/topLongShortPositionRatio", {"symbol": symbol, "period": "5m", "limit": 1})
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

    # 3. 智能數據格式化（固定長度，完美解決對齊問題）
    def fmt_val(val_usd, with_sign=False, width=9):
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

    # 4. 根據現貨可用性切換數據流（現貨 K 線 vs 合約持倉歷史）
    data_type_title = "純現貨全面數據" if is_spot_available else "期貨合約數據"
    flow_title = "純現貨全面淨流入 (含大小單)" if is_spot_available else "合約主力持倉淨流入"
    
    if is_spot_available:
        # 現貨模式：抓取現貨 K 線
        klines_5m = fetch_binance_data("https://api.binance.com/api/v3/klines", {"symbol": symbol, "interval": "5m", "limit": 12})
        klines_1h = fetch_binance_data("https://api.binance.com/api/v3/klines", {"symbol": symbol, "interval": "1h", "limit": 25})
        klines_4h = fetch_binance_data("https://api.binance.com/api/v3/klines", {"symbol": symbol, "interval": "4h", "limit": 45})
        
        def get_spot_data(kline_list, lookback):
            if not kline_list or len(kline_list) < lookback:
                return " 計算中... ", " $0.00 "
            try:
                tot_vol, tot_net = 0.0, 0.0
                for k in kline_list[-lookback:]:
                    spot_total = float(k[7])
                    spot_buy = float(k[10])
                    tot_vol += spot_total
                    tot_net += (spot_buy - (spot_total - spot_buy))
                pct = (tot_net / tot_vol) if tot_vol > 0 else 0
                return f"{fmt_val(tot_vol)} ({pct*100:+.2f}%)", fmt_val(tot_net, True)
            except:
                return " 計算中... ", " $0.00 "
                
        d_5m, n_5m = get_spot_data(klines_5m, 1)
        d_15m, n_15m = get_spot_data(klines_5m, 3)
        d_30m, n_30m = get_spot_data(klines_5m, 6)
        d_1h, n_1h = get_spot_data(klines_1h, 1)
        d_4h, n_4h = get_spot_data(klines_1h, 4)
        d_8h, n_8h = get_spot_data(klines_1h, 8)
        d_12h, n_12h = get_spot_data(klines_1h, 12)
        d_24h, n_24h = get_spot_data(klines_1h, 24)
        d_48h, n_48h = get_spot_data(klines_4h, 12)
        d_72h, n_72h = get_spot_data(klines_4h, 18)
        d_168h, n_168h = get_spot_data(klines_4h, 42)
    else:
        # 合約模式：抓取合約持倉歷史
        hist_5m = fetch_binance_data("https://fapi.binance.com/futures/data/openInterestHist", {"symbol": symbol, "period": "5m", "limit": 15})
        hist_1h = fetch_binance_data("https://fapi.binance.com/futures/data/openInterestHist", {"symbol": symbol, "period": "1h", "limit": 30})
        hist_4h = fetch_binance_data("https://fapi.binance.com/futures/data/openInterestHist", {"symbol": symbol, "period": "4h", "limit": 50})
        
        # 獲取當前合約總持倉量
        oi_usd = 0.0
        oi_res = fetch_binance_data(f"https://fapi.binance.com/fapi/v1/openInterest?symbol={symbol}")
        if isinstance(oi_res, dict) and "openInterest" in oi_res:
            oi_usd = float(oi_res.get("openInterest", 0)) * price
            
        def get_futures_data(hist_data, lookback):
            if not hist_data or len(hist_data) > lookback:
                try:
                    hist_oi = float(hist_data[-1 - lookback].get("sumOpenInterestValue", 0)) * price
                    if hist_oi > 0:
                        pct = (oi_usd / hist_oi) - 1
                        oi_diff = oi_usd - hist_oi
                        bias = (pos_ratio - 1) / (pos_ratio + 1)
                        net_inflow = oi_diff * (0.5 + bias)
                        return f"{fmt_val(hist_oi)} ({pct*100:+.2f}%)", fmt_val(net_inflow, True)
                except:
                    pass
            return " 計算中... ", " $0.00 "
            
        d_5m, n_5m = get_futures_data(hist_5m, 1)
        d_15m, n_15m = get_futures_data(hist_5m, 3)
        d_30m, n_30m = get_futures_data(hist_5m, 6)
        d_1h, n_1h = get_futures_data(hist_1h, 1)
        d_4h, n_4h = get_futures_data(hist_1h, 4)
        d_8h, n_8h = get_futures_data(hist_1h, 8)
        d_12h, n_12h = get_futures_data(hist_1h, 12)
        d_24h, n_24h = get_futures_data(hist_1h, 24)
        d_48h, n_48h = get_futures_data(hist_4h, 12)
        d_72h, n_72h = get_futures_data(hist_4h, 18)
        d_168h, n_168h = get_futures_data(hist_4h, 42)

    # 🌟 使用等寬字元空間微調，確保 5分鐘 與 168小時 在手機端不論數據多長都能完美對齊
    reply_text = (
        f"{coin}/USDT 數據面板 📘\n"
        f"━━━━━━━━━━━━━━━\n"
        f"即時交易價:\t\t${price:.4f}\n"
        f"合約資金費率:\t\t{funding_rate:+.4f}%\n\n"
        f"【期貨合約大戶動態情緒】\n"
        f"即時大戶多空比 (帳戶數): {acc_ratio:.2f}\n"
        f"多 {acc_long:.1f}% [{gen_bar(acc_long)}] {acc_short:.1f}% 空\n"
        f"即時大戶多空比 (持倉量): {pos_ratio:.2f}\n"
        f"多 {pos_long:.1f}% [{gen_bar(pos_long)}] {pos_short:.1f}% 空\n"
        f"多空持倉人數比: {acc_ratio:.2f}\n"
        f"多 {acc_long:.1f}% [{gen_bar(acc_long)}] {acc_short:.1f}% 空\n\n"
        f"【{data_type_title} | 區間累計】\n"
        f"5分鐘   │ {d_5m}\n"
        f"15分鐘  │ {d_15m}\n"
        f"30分鐘  │ {d_30m}\n"
        f"1小時   │ {d_1h}\n"
        f"4小時   │ {d_4h}\n"
        f"8小時   │ {d_8h}\n"
        f"12小時  │ {d_12h}\n"
        f"24小時  │ {d_24h}\n"
        f"48小時  │ {d_48h}\n"
        f"72小時  │ {d_72h}\n"
        f"168小時 │ {d_168h}\n\n"
        f"【{flow_title}】\n"
        f"5分鐘   │ {n_5m}\n"
        f"15分鐘  │ {n_15m}\n"
        f"30分鐘  │ {n_30m}\n"
        f"1小時   │ {n_1h}\n"
        f"4小時   │ {n_4h}\n"
        f"8小時   │ {n_8h}\n"
        f"12小時  │ {n_12h}\n"
        f"24小時  │ {n_24h}\n"
        f"48小時  │ {n_48h}\n"
        f"72小時  │ {n_72h}\n"
        f"168小時 │ {n_168h}\n"
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

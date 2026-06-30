#!/usr/bin/env python3
import os
import sys
import subprocess
import json
from pathlib import Path
import requests
from flask import Flask, request, abort

# 引入 LINE 官方提供的 SDK 元件，用來做安全驗證
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
CHAIN   = "ethereum"
GL_BIN  = "/opt/render/project/src/gl" 
N_DEVS  = 5   

REPOS = [
    "defi-core/yield-optimizer",
    "dao-tooling/governance-sdk",
    "layer2/bridge-protocol",
    "nft-infra/provenance-engine",
    "chain-analytics/onchain-indexer",
]

# ── LINE / Nansen Config ──────────────────────────────────────────────────────
LINE_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET") # ✅ 補上了！
LINE_USER_ID = os.environ.get("LINE_USER_ID")

# 初始化 LINE SDK 套件
line_bot_api = LineBotApi(LINE_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET) # ✅ 使用 SECRET 來初始化安全防護罩

# ── Nansen 核心邏輯 ───────────────────────────────────────────────────────────
api_calls = []

def nansen(subcmd: str) -> dict | None:
    cmd = f"nansen {subcmd}"
    api_calls.append(cmd)
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if r.returncode != 0: return None
    try:
        parsed = json.loads(r.stdout)
        if not parsed.get("success"):
            code = parsed.get("code", "")
            if code == "CREDITS_EXHAUSTED":
                send_to_line("🚨 Nansen API 額度已耗盡！")
                return None
        return parsed
    except json.JSONDecodeError:
        return None

def f(val) -> float:
    try: return float(val or 0)
    except: return 0.0

def get_smart_money_tokens() -> set[str]:
    data = nansen(f"research smart-money holdings --chain {CHAIN} --limit 20 --fields token_symbol,value_usd,holders_count")
    if not data or not data.get("success"): return set()
    rows = data.get("data", {}).get("data", data.get("data", []))
    if isinstance(rows, dict): rows = rows.get("data", [])
    return {r.get("token_symbol", "") for r in rows if r.get("token_symbol")}

def get_smart_money_netflow() -> list[dict]:
    data = nansen(f"research smart-money netflow --chain {CHAIN} --limit 10 --fields token_symbol,net_flow_7d_usd,trader_count")
    if not data or not data.get("success"): return []
    rows = data.get("data", {}).get("data", data.get("data", []))
    if isinstance(rows, dict): rows = rows.get("data", [])
    return sorted(rows, key=lambda r: f(r.get("net_flow_7d_usd")), reverse=True)[:5]

def get_trader_addresses() -> list[dict]:
    seen, traders = set(), []
    for chain in [CHAIN, "base", "solana", "bnb"]:
        if len(traders) >= N_DEVS: break
        data = nansen(f"research smart-money dex-trades --chain {chain} --limit 100 --fields trader_address,trader_address_label,trade_value_usd,token_bought_symbol")
        if not data or not data.get("success"): continue
        rows = data.get("data", {}).get("data", data.get("data", []))
        if isinstance(rows, dict): rows = rows.get("data", [])
        for r in rows:
            addr = r.get("trader_address", "")
            if addr and addr not in seen:
                seen.add(addr)
                traders.append({
                    "address": addr,
                    "label":   r.get("trader_address_label", addr[:10] + "..."),
                    "bought":  r.get("token_bought_symbol", "?"),
                    "chain":   chain,
                })
            if len(traders) >= N_DEVS: break
    return traders

def profile_balance(address: str, sm_tokens: set[str], chain: str = CHAIN) -> tuple[int, float, int]:
    data = nansen(f"research profiler balance --address {address} --chain {chain} --limit 20 --fields token_symbol,value_usd")
    if not data or not data.get("success"): return 0, 0.0, 0
    rows = data.get("data", {}).get("data", data.get("data", []))
    if isinstance(rows, dict): rows = rows.get("data", [])
    total_usd    = sum(f(r.get("value_usd")) for r in rows)
    held_symbols = {r.get("token_symbol", "") for r in rows}
    overlap      = len(held_symbols & sm_tokens)
    if   total_usd > 10_000_000: port_pts = 20
    elif total_usd > 1_000_000:  port_pts = 16
    elif total_usd > 100_000:    port_pts = 10
    elif total_usd > 10_000:     port_pts = 5
    else:                        port_pts = 1
    align_pts = min(overlap * 4, 20)
    return port_pts + align_pts, total_usd, overlap

def profile_transactions(address: str, chain: str = CHAIN) -> tuple[int, int, float]:
    data = nansen(f"research profiler transactions --address {address} --chain {chain} --days 30 --limit 50 --fields volume_usd,source_type")
    if not data or not data.get("success"): return 0, 0, 0.0
    rows = data.get("data", {}).get("data", data.get("data", []))
    if isinstance(rows, dict): rows = rows.get("data", [])
    count  = len(rows)
    volume = sum(f(r.get("volume_usd")) for r in rows)
    if   count > 40: pts = 30
    elif count > 20: pts = 22
    elif count > 10: pts = 14
    elif count > 5:  pts = 8
    else:            pts = max(count, 0) * 1
    return pts, count, volume

def profile_pnl(address: str, chain: str = CHAIN) -> tuple[int, float]:
    data = nansen(f"research profiler pnl-summary --address {address} --chain {chain} --days 30")
    if not data or not data.get("success"): return 0, 0.0
    d   = data.get("data", {})
    if isinstance(d, dict) and "data" in d: d = d.get("data", [{}])[0] if d.get("data") else {}
    if isinstance(d, list): d = d[0] if d else {}
    pnl = f(d.get("realized_pnl_usd"))
    if   pnl > 1_000_000: pts = 30
    elif pnl > 100_000:   pts = 22
    elif pnl > 10_000:    pts = 14
    elif pnl > 1_000:     pts = 7
    elif pnl > 0:         pts = 3
    else:                 pts = 0
    return pts, pnl

def send_to_line(text_message: str):
    """主動推送訊息至 LINE"""
    if not LINE_ACCESS_TOKEN or not LINE_USER_ID:
        print("⚠️ 缺少 LINE 金鑰或 User ID")
        return False
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"
    }
    payload = {
        "to": LINE_USER_ID,
        "messages": [{"type": "text", "text": text_message}]
    }
    res = requests.post(url, headers=headers, json=payload)
    return res.status_code == 200

def run_whale_tracking_logic():
    global api_calls
    api_calls = [] 
    
    line_report = []
    line_report.append("🐳 【Nansen 大戶建倉與聰明錢情報】")
    line_report.append("=========================")

    sm_tokens  = get_smart_money_tokens()
    sm_netflow = get_smart_money_netflow()

    if sm_netflow:
        line_report.append("🔥 聰明錢 7d 淨流入 Top 5 (可能正在建倉):")
        for r in sm_netflow:
            nf = f(r.get("net_flow_7d_usd"))
            tc = r.get("trader_count", "?")
            sym = r.get("token_symbol", "?")
            line_report.append(f" ❖ {sym:<6} | +${nf:,.0f} ({tc}人)")
    line_report.append("")

    traders = get_trader_addresses()
    if not traders:
        return "🚨 無法獲取大戶地址，請檢查 Nansen API 金鑰"

    results = []
    for i, t in enumerate(traders):
        addr  = t["address"]
        label = t["label"]
        chain = t.get("chain", CHAIN)
        
        port_pts, portfolio_usd, overlap = profile_balance(addr, sm_tokens, chain)
        tx_pts, tx_count, volume_usd     = profile_transactions(addr, chain)
        pnl_pts, pnl_usd                 = profile_pnl(addr, chain)
        total = port_pts + tx_pts + pnl_pts

        results.append({
            "label":   label,
            "portfolio_usd": portfolio_usd,
            "tx_count":      tx_count,
            "overlap":       overlap,
            "total":         total,
            "bought":        t["bought"]
        })

    results.sort(key=lambda x: x["total"], reverse=True)

    line_report.append("🏆 聰明大戶積分榜 (資產/活動/PnL總分)")
    line_report.append("-------------------------")
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    for i, r in enumerate(results):
        line_report.append(f"{medals[i]} {r['label']}")
        line_report.append(f"   • 綜合建倉實力: {r['total']}/100 🚀")
        line_report.append(f"   • 錢包總資產: ${r['portfolio_usd']:,.0f}")
        line_report.append(f"   • 近30天交易: {r['tx_count']} 次")
        line_report.append(f"   • 聰明錢重疊幣種: {r['overlap']} 個")
        if r['bought'] != "?":
            line_report.append(f"   • 剛才購入: {r['bought']} ✨")
        line_report.append("")

    line_report.append(f"📊 總計調用 Nansen API: {len(api_calls)} 次")
    return "\n".join(line_report)

# ── Flask 路由 ────────────────────────────────────────────────────────────────

@app.route("/", methods=['GET'])
def index():
    return "LINE Crypto Bot Server is running!", 200

@app.route("/trigger", methods=['GET', 'POST'])
def trigger_report():
    report_content = run_whale_tracking_logic()
    success = send_to_line(report_content)
    if success:
        return "✅ 大戶建倉情報已成功發送到你的 LINE 手機上！", 200
    else:
        return "❌ 傳送失敗，請檢查 Render 上的環境變數設定與 User ID。", 500

@app.route("/webhook", methods=['POST'])
def callback():
    """✅ 補上標準安全驗證邏輯：當用戶傳訊息給官方帳號時會觸發此處"""
    signature = request.headers.get('X-Line-Signature', '')

    # 取得請求內容的文字
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    # 💡 關鍵：這裡會印出完整的收到的訊息，你可以在 Render Logs 裡面看到 userId
    print("🔔 收到 LINE Webhook JSON 數據：", body)

    try:
        # 利用 CHANNEL_SECRET 進行簽章驗證，確保訊息真的來自 LINE 官方
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return 'OK', 200

# 當有人傳送文字訊息給官方帳號時，會自動觸發這個區塊
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    # 這裡可以直接撈到傳訊息的人的 userId
    user_id = event.source.user_id
    user_message = event.message.text
    
    # 順便做個好玩的功能：如果你對官方帳號輸入「查大戶」，它也會自動觸發報告！
    if user_message == "查大戶":
        report_content = run_whale_tracking_logic()
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=report_content)
        )
    else:
        # 如果打其他字，機器人會貼心提醒你你的 User ID 是多少
        reply_text = f"你好！我收到你的訊息了。\n你的專屬 LINE User ID 為：\n\n{user_id}\n\n請把這串代碼複製並填入 Render 的 LINE_USER_ID 環境變數中！"
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text)
        )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

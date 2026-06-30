#!/usr/bin/env python3
"""
gitlawb × Nansen: Smart Developer Intelligence (LINE Bot Edition)
================================================================
"""

import subprocess
import json
import os
import sys
from pathlib import Path
import requests  # 新增：用於發送資料給 LINE

# ── Config ────────────────────────────────────────────────────────────────────

CHAIN   = "ethereum"
GL_BIN  = "/Users/kevin/Projects/gitlawb/target/release/gl" # 注意：Render 環境如果沒有這個執行檔，make_did 會回傳預設值，不影響 Nansen 運作
N_DEVS  = 5   

REPOS = [
    "defi-core/yield-optimizer",
    "dao-tooling/governance-sdk",
    "layer2/bridge-protocol",
    "nft-infra/provenance-engine",
    "chain-analytics/onchain-indexer",
]

# ── LINE Bot Config ──────────────────────────────────────────────────────────
# 請確保在 Render 的 Environment Variables 設定這兩個變數
LINE_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_USER_ID = os.environ.get("LINE_USER_ID") # 你的個人 LINE User ID (或是群組 ID)

# ── Nansen CLI ────────────────────────────────────────────────────────────────

api_calls = []

def nansen(subcmd: str) -> dict | None:
    cmd = f"nansen {subcmd}"
    api_calls.append(cmd)
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if r.returncode != 0:
        return None
    try:
        parsed = json.loads(r.stdout)
        if not parsed.get("success"):
            code = parsed.get("code", "")
            if code == "CREDITS_EXHAUSTED":
                send_to_line("🚨 Nansen API 額度已耗盡！")
                sys.exit(1)
            elif code == "FORBIDDEN":
                return None  
        return parsed
    except json.JSONDecodeError:
        return None

def gl(subcmd: str) -> str:
    # 確保在雲端環境找不到此路徑時不會直接崩潰
    if not os.path.exists(GL_BIN):
        return "did:key:mock_generated_fallback_for_render"
    r = subprocess.run(f"{GL_BIN} {subcmd}", shell=True, capture_output=True, text=True)
    return r.stdout.strip()

def f(val) -> float:
    try: return float(val or 0)
    except: return 0.0

# ── Step 1: Pull smart money context + trader addresses ───────────────────────

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

# ── Step 2: Profile each wallet ───────────────────────────────────────────────

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

def make_did(tag: str) -> str:
    if not os.path.exists(GL_BIN):
        return f"did:key:mock_{tag}_z6Mk"
    dir_path = f"/tmp/gitlawb-poc-{tag}"
    Path(dir_path).mkdir(exist_ok=True)
    gl(f"identity new --dir {dir_path} --force")
    did = gl(f"identity show --dir {dir_path}")
    return did if did.startswith("did:key:") else "did:key:[generated]"

# ── LINE Notification Function ────────────────────────────────────────────────

def send_to_line(text_message: str):
    """將文字訊息透過 LINE Messaging API 推送給指定用戶"""
    if not LINE_ACCESS_TOKEN or not LINE_USER_ID:
        print("⚠️ 缺少 LINE 金鑰或 User ID，僅在本地 Print：")
        print(text_message)
        return

    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"
    }
    payload = {
        "to": LINE_USER_ID,
        "messages": [
            {
                "type": "text",
                "text": text_message
            }
        ]
    }
    try:
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code == 200:
            print("✅ 數據成功同步至手機 LINE！")
        else:
            print(f"❌ LINE 傳送失敗: {response.status_code}, {response.text}")
    except Exception as e:
        print(f"❌ 網路異常無法連接 LINE API: {e}")

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # 用一個 list 來收集所有要發進 LINE 的文字行
    line_report = []
    
    line_report.append("🐳 【Nansen 大戶建倉與聰明錢情報】")
    line_report.append("=========================")

    # 1. Smart money context
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

    # 2. Get trader addresses
    traders = get_trader_addresses()
    if not traders:
        send_to_line("🚨 無法獲取大戶地址，請檢查 Nansen API 金鑰")
        sys.exit(1)

    # 3. Profile each trader
    results = []
    for i, t in enumerate(traders):
        addr  = t["address"]
        label = t["label"]
        repo  = REPOS[i % len(REPOS)]
        short = addr[:6] + "..." + addr[-4:]

        chain = t.get("chain", CHAIN)
        port_pts, portfolio_usd, overlap = profile_balance(addr, sm_tokens, chain)
        tx_pts, tx_count, volume_usd     = profile_transactions(addr, chain)
        pnl_pts, pnl_usd                 = profile_pnl(addr, chain)
        total = port_pts + tx_pts + pnl_pts

        results.append({
            "label":   label,
            "short":   short,
            "bought":  t["bought"],
            "portfolio_usd": portfolio_usd,
            "tx_count":      tx_count,
            "overlap":       overlap,
            "total":         total,
        })

    results.sort(key=lambda x: x["total"], reverse=True)

    # 4. Format Leaderboard for LINE
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
    
    # 將所有行組合，並透過 LINE Bot 推送
    full_message = "\n".join(line_report)
    send_to_line(full_message)

if __name__ == "__main__":
    if not os.environ.get("NANSEN_API_KEY"):
        print("ERROR: set NANSEN_API_KEY first")
        sys.exit(1)
    main()

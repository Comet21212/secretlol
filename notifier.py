"""
Roblox Limited Price Notifier - Smarter Version A
Accurate 30% tax, 1st vs 2nd seller, RAP attempt, 30-min reports
"""

import os
import time
import math
import random
import requests
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

# ====================== CONFIG ======================
WEBHOOK_URL = "https://discord.com/api/webhooks/1537206323429769247/DDanKqiYTeAUDMQjOt0UvDCV5UD4lEjtnrCGs9OtXJtqsuZn8YkMObq6xug12KK0J7pl"

# Target = 0 → max_price
ITEMS: Dict[int, int] = {
    9255011:     4300,   # Antlers Silver Lim
    10159617728: 6000,   # 8 Bit Tabby Cat
    1082932:     2500,   # Traffic Cone
    14463095:    5000,   # Pinstripe Fedora
    1609390589:  3000,   # Blue Traffic Cone
    16477149823: 5675,   # Golden Clockwork Headphones
    1609402609:  1100,   # Black Iron Branches
    17408283:    800,    # Outrageous Builders Club Hat
}

CHECK_INTERVAL = 45
ALERT_COOLDOWN = 300
UPTIME_INTERVAL = 45 * 60
PRICE_REPORT_INTERVAL = 30 * 60          # every 30 minutes

# Real 2026 marketplace fee for limited flips (seller keeps 70%)
SELLER_KEEP = 0.70
# ====================================================

ROBLOSECURITY = os.getenv("ROBLOSECURITY", "").strip()

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
})
if ROBLOSECURITY:
    session.cookies.set(".ROBLOSECURITY", ROBLOSECURITY, domain=".roblox.com")

last_alerted: Dict[int, float] = {}
last_uptime = 0.0
next_price_report = time.time() + 60          # first report ~1 min after start
start_time = time.time()
csrf_token: Optional[str] = None

def net_after_tax(sell_price: int) -> int:
    """Robux the seller actually receives (70%)."""
    return math.floor(sell_price * SELLER_KEEP)

def listing_for_target(desired_net: int) -> int:
    """Minimum listing price to receive exactly desired_net after tax."""
    return math.ceil(desired_net / SELLER_KEEP)

def refresh_csrf() -> Optional[str]:
    global csrf_token
    try:
        r = session.post("https://auth.roblox.com/v2/logout", timeout=10)
        token = r.headers.get("x-csrf-token")
        if token:
            csrf_token = token
            session.headers["X-CSRF-TOKEN"] = token
            return token
    except Exception:
        pass
    return None

def get_catalog_info(asset_id: int) -> Optional[dict]:
    url = f"https://catalog.roblox.com/v1/catalog/items/{asset_id}/details?itemType=Asset"
    try:
        resp = session.get(url, timeout=12)
        resp.raise_for_status()
        data = resp.json()
        lowest = data.get("lowestResalePrice") or data.get("lowestPrice")
        if lowest is None:
            return None
        return {
            "name": data.get("name", f"Asset {asset_id}"),
            "lowest": int(lowest),
            "asset_id": asset_id,
            "url": f"https://www.roblox.com/catalog/{asset_id}/",
            "collectible_item_id": data.get("collectibleItemId"),
            "product_id": data.get("productId"),
        }
    except Exception as e:
        print(f"  ❌ Catalog {asset_id}: {e}")
        return None

def get_resellers(asset_id: int) -> Tuple[Optional[int], Optional[int]]:
    """Return (1st price, 2nd price)."""
    if not ROBLOSECURITY:
        return None, None
    url = f"https://economy.roblox.com/v1/assets/{asset_id}/resellers?limit=10"
    try:
        if not csrf_token:
            refresh_csrf()
        resp = session.get(url, timeout=12)
        if resp.status_code == 403:
            refresh_csrf()
            resp = session.get(url, timeout=12)
        if resp.status_code != 200:
            return None, None
        data = resp.json().get("data", [])
        if not data:
            return None, None
        p1 = data[0].get("price")
        p2 = data[1].get("price") if len(data) > 1 else None
        return (int(p1) if p1 else None, int(p2) if p2 else None)
    except Exception as e:
        print(f"  ⚠️ Resellers {asset_id}: {e}")
        return None, None

def get_rap(asset_id: int, collectible_id: Optional[str] = None) -> Optional[int]:
    """Try to get recent average price (RAP / usual value)."""
    # Legacy endpoint
    try:
        r = session.get(f"https://economy.roblox.com/v1/assets/{asset_id}/resale-data", timeout=10)
        if r.status_code == 200:
            rap = r.json().get("recentAveragePrice")
            if rap:
                return int(rap)
    except Exception:
        pass

    # Newer collectible endpoint (if we have the ID)
    if collectible_id:
        try:
            r = session.get(
                f"https://apis.roblox.com/marketplace-sales/v1/item/{collectible_id}/resale-data",
                timeout=10
            )
            if r.status_code == 200:
                data = r.json()
                rap = data.get("recentAveragePrice") or data.get("averagePrice")
                if rap:
                    return int(rap)
        except Exception:
            pass
    return None

def get_item_info(asset_id: int) -> Optional[dict]:
    info = get_catalog_info(asset_id)
    if not info:
        return None

    p1, p2 = get_resellers(asset_id)
    if p1 is not None:
        info["lowest"] = p1
    info["second"] = p2

    rap = get_rap(asset_id, info.get("collectible_item_id"))
    info["rap"] = rap
    return info

def send_webhook(content: str = None, embeds: list = None) -> bool:
    payload = {}
    if content:
        payload["content"] = content
    if embeds:
        payload["embeds"] = embeds
    try:
        r = session.post(WEBHOOK_URL, json=payload, timeout=10)
        return r.status_code in (200, 204)
    except Exception as e:
        print(f"❌ Webhook: {e}")
        return False

def send_startup():
    status = "✅ Cookie loaded" if ROBLOSECURITY else "⚠️ No cookie – public prices only"
    send_webhook(content=f"**monitor started**\n{status}")
    print(status)

def build_profit_text(lowest: int, second: Optional[int], rap: Optional[int], max_price: int) -> str:
    """Create the profit + risk section."""
    # Suggested sell price priority: 2nd seller → RAP → 92% of max target
    if second and second > lowest:
        suggested = second
        source = "2nd seller"
    elif rap and rap > lowest:
        suggested = rap
        source = "RAP / usual"
    else:
        suggested = max(int(max_price * 0.92), lowest + 50)
        source = "target range"

    net = net_after_tax(suggested)
    profit = net - lowest
    gap = (second - lowest) if second else 0

    lines = [
        f"**Buy (1st):** `{lowest:,}` R$",
    ]
    if second:
        lines.append(f"**2nd seller:** `{second:,}` R$  (gap `{gap:,}`)")
    if rap:
        lines.append(f"**Usual / RAP:** `~{rap:,}` R$")
    lines.append(f"**Suggested sell:** `~{suggested:,}` R$ ({source})")
    lines.append(f"**You would receive after 30% tax:** `{net:,}` R$")
    lines.append(f"**Possible profit:** `{'+' if profit >= 0 else ''}{profit:,}` R$")

    # Risk / opportunity notes
    notes = []
    if gap >= 400:
        notes.append("🔥 Large gap between 1st & 2nd – strong flip potential")
    if rap and lowest < rap * 0.85:
        notes.append("📉 Well below usual value – attractive entry")
    if profit < 100:
        notes.append("⚠️ Thin profit after tax – higher risk")
    if lowest <= max_price * 0.7:
        notes.append("✅ Significantly under your max target")

    if notes:
        lines.append("")
        lines.extend(notes)

    return "\n".join(lines)

def send_alert(info: dict, max_price: int):
    body = build_profit_text(
        info["lowest"],
        info.get("second"),
        info.get("rap"),
        max_price
    )
    embed = {
        "title": f"🚨 PRICE ALERT: {info['name']}",
        "description": body + f"\n\n[Open on Roblox]({info['url']})",
        "color": 0xFF0000,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "30% tax applied • Manual buy only"},
    }
    send_webhook(content="@everyone **DEAL FOUND!**", embeds=[embed])
    print(f"🚨 {info['name']} @ {info['lowest']:,}")

def send_uptime():
    secs = int(time.time() - start_time)
    h, m = secs // 3600, (secs % 3600) // 60
    embed = {
        "title": "🟢 Monitor Uptime",
        "description": f"Still running.\n**Uptime:** {h}h {m}m",
        "color": 0x57F287,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    send_webhook(embeds=[embed])

def send_price_report():
    print("\n📊 30-min price report...")
    lines = []
    for asset_id, max_p in ITEMS.items():
        info = get_item_info(asset_id)
        if not info:
            lines.append(f"**ID {asset_id}**: No data")
            continue

        lowest = info["lowest"]
        second = info.get("second")
        rap = info.get("rap")
        status = "✅ UNDER" if lowest <= max_p else ""

        extra = ""
        if second:
            gap = second - lowest
            extra += f" → 2nd `{second:,}` (gap {gap:,})"
        if rap:
            extra += f" | RAP `~{rap:,}`"

        # Quick profit preview if under target
        profit_note = ""
        if lowest <= max_p and second and second > lowest:
            net = net_after_tax(second)
            profit = net - lowest
            profit_note = f" | possible `+{profit:,}` after tax"

        lines.append(f"**{info['name']}**: `{lowest:,}`{extra}{profit_note} {status}")
        time.sleep(0.9)

    embed = {
        "title": "📈 Limited Prices (every 30 min)",
        "description": "\n".join(lines) or "No data",
        "color": 0x5865F2,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "Shows 1st → 2nd seller + possible profit after 30% tax"},
    }
    send_webhook(embeds=[embed])
    print("📊 Report sent\n")

def main():
    global last_uptime, next_price_report

    print("=" * 55)
    print("🚀 SMARTER NOTIFIER – Version A")
    print("=" * 55)

    if ROBLOSECURITY:
        refresh_csrf()
        print("Cookie + CSRF ready")
    else:
        print("⚠️ No ROBLOSECURITY – limited data")

    send_startup()

    print("\n🔍 Startup scan:")
    for asset_id, max_p in ITEMS.items():
        info = get_item_info(asset_id)
        if info:
            extra = ""
            if info.get("second"):
                extra += f" | 2nd {info['second']:,}"
            if info.get("rap"):
                extra += f" | RAP {info['rap']:,}"
            status = " ← UNDER" if info["lowest"] <= max_p else ""
            print(f"  ✅ {info['name']}: {info['lowest']:,}{extra}{status}")
        time.sleep(0.8)

    print("\n✅ Entering main loop\n")

    while True:
        now = time.time()

        if now - last_uptime >= UPTIME_INTERVAL:
            send_uptime()
            last_uptime = now

        if now >= next_price_report:
            send_price_report()
            next_price_report = now + PRICE_REPORT_INTERVAL

        print(f"--- Check @ {datetime.now().strftime('%H:%M:%S')} ---")
        for asset_id, max_p in ITEMS.items():
            info = get_item_info(asset_id)
            if not info:
                continue

            lowest = info["lowest"]
            if lowest <= max_p:
                last = last_alerted.get(asset_id, 0)
                if now - last >= ALERT_COOLDOWN:
                    send_alert(info, max_p)
                    last_alerted[asset_id] = now
                else:
                    rem = int(ALERT_COOLDOWN - (now - last))
                    print(f"  ⏳ {info['name']} under target (cd {rem}s)")
            else:
                extra = ""
                if info.get("second"):
                    extra += f" | 2nd {info['second']:,}"
                print(f"  • {info['name']}: {lowest:,}{extra} (max {max_p:,})")

            time.sleep(1.1)

        print(f"--- Sleep {CHECK_INTERVAL}s ---\n")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()

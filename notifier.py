"""
Roblox Limited Price Notifier - Version A (Cookie + Profit)
"""

import os
import time
import random
import requests
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

# ====================== CONFIG ======================
WEBHOOK_URL = "https://discord.com/api/webhooks/1537206323429769247/DDanKqiYTeAUDMQjOt0UvDCV5UD4lEjtnrCGs9OtXJtqsuZn8YkMObq6xug12KK0J7pl"

# Target = 0 to max_price
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
PRICE_REPORT_MIN = 60 * 60
PRICE_REPORT_MAX = 120 * 60

RESELL_KEEP_PERCENT = 0.70   # rough estimate after Roblox fee
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
next_price_report = time.time() + random.randint(PRICE_REPORT_MIN, PRICE_REPORT_MAX)
start_time = time.time()
csrf_token: Optional[str] = None

def refresh_csrf() -> Optional[str]:
    """Get a fresh x-csrf-token using the cookie."""
    global csrf_token
    try:
        r = session.post("https://auth.roblox.com/v2/logout", timeout=10)
        token = r.headers.get("x-csrf-token")
        if token:
            csrf_token = token
            session.headers["X-CSRF-TOKEN"] = token
            return token
    except Exception as e:
        print(f"CSRF refresh failed: {e}")
    return None

def get_catalog_info(asset_id: int) -> Optional[dict]:
    url = f"https://catalog.roblox.com/v1/catalog/items/{asset_id}/details?itemType=Asset"
    try:
        resp = session.get(url, timeout=12)
        resp.raise_for_status()
        data = resp.json()
        lowest = data.get("lowestResalePrice") or data.get("lowestPrice")
        name = data.get("name", f"Asset {asset_id}")
        if lowest is None:
            return None
        return {
            "name": name,
            "lowest": int(lowest),
            "asset_id": asset_id,
            "url": f"https://www.roblox.com/catalog/{asset_id}/",
            "product_id": data.get("productId"),
            "collectible_item_id": data.get("collectibleItemId"),
        }
    except Exception as e:
        print(f"  ❌ Catalog {asset_id}: {e}")
        return None

def get_resellers(asset_id: int) -> Tuple[Optional[int], Optional[int]]:
    """
    Try to get 1st and 2nd seller prices.
    Returns (price1, price2) or (None, None) on failure.
    """
    if not ROBLOSECURITY:
        return None, None

    # Legacy reseller endpoint
    url = f"https://economy.roblox.com/v1/assets/{asset_id}/resellers?limit=10&cursor="
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

def get_item_info(asset_id: int) -> Optional[dict]:
    info = get_catalog_info(asset_id)
    if not info:
        return None

    p1, p2 = get_resellers(asset_id)
    if p1 is not None:
        info["lowest"] = p1          # prefer live reseller price
        info["second"] = p2
    else:
        info["second"] = None

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
        print(f"❌ Webhook error: {e}")
        return False

def send_startup():
    cookie_status = "✅ Cookie loaded" if ROBLOSECURITY else "⚠️ No cookie (using public prices only)"
    success = send_webhook(content=f"**monitor started**\n{cookie_status}")
    print("✅ Startup message sent" if success else "❌ Startup webhook failed")
    print(cookie_status)

def send_alert(info: dict, max_price: int):
    lowest = info["lowest"]
    second = info.get("second")

    # Suggested resell & profit
    if second and second > lowest:
        suggested = second
        gap = second - lowest
    else:
        suggested = max(int(max_price * 0.92), lowest + 50)
        gap = suggested - lowest

    net_profit = int(gap * RESELL_KEEP_PERCENT)

    gap_note = ""
    if gap >= 400:
        gap_note = "\n🔥 **Big gap between 1st & 2nd seller**"

    second_line = f"**2nd seller:** `{second:,}` Robux\n" if second else ""

    embed = {
        "title": f"🚨 PRICE ALERT: {info['name']}",
        "description": (
            f"**Buy at (1st seller):** `{lowest:,}` Robux\n"
            f"{second_line}"
            f"**Suggested resell:** `~{suggested:,}` Robux\n"
            f"**Est. net profit:** `+{net_profit:,}` Robux{gap_note}\n\n"
            f"[Open on Roblox]({info['url']})"
        ),
        "color": 0xFF0000,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "Version A • Manual buy only"},
    }
    send_webhook(content="@everyone **DEAL FOUND!**", embeds=[embed])
    print(f"🚨 ALERT → {info['name']} @ {lowest:,} | gap {gap:,} | est profit {net_profit:,}")

def send_uptime():
    uptime_seconds = int(time.time() - start_time)
    hours = uptime_seconds // 3600
    minutes = (uptime_seconds % 3600) // 60
    embed = {
        "title": "🟢 Monitor Uptime",
        "description": f"Still running.\n**Uptime:** {hours}h {minutes}m",
        "color": 0x57F287,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    send_webhook(embeds=[embed])
    print("📡 Uptime sent")

def send_price_report():
    print("\n📊 Price report...")
    lines = []
    for asset_id, max_p in ITEMS.items():
        info = get_item_info(asset_id)
        if info:
            second = f" → 2nd `{info['second']:,}`" if info.get("second") else ""
            status = "✅ UNDER" if info["lowest"] <= max_p else ""
            lines.append(f"**{info['name']}**: `{info['lowest']:,}`{second} (max {max_p:,}) {status}")
        else:
            lines.append(f"**ID {asset_id}**: No data")
        time.sleep(0.9)

    embed = {
        "title": "📈 Current Limited Prices",
        "description": "\n".join(lines) or "No data",
        "color": 0x5865F2,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    send_webhook(embeds=[embed])
    print("📊 Report sent\n")

def main():
    global last_uptime, next_price_report

    print("=" * 50)
    print("🚀 STARTING NOTIFIER – Version A")
    print("=" * 50)

    if ROBLOSECURITY:
        refresh_csrf()
        print("Cookie detected, CSRF refreshed")
    else:
        print("⚠️ ROBLOSECURITY env var is empty – running without cookie")

    send_startup()

    print("\n🔍 Startup check of all items:")
    for asset_id, max_p in ITEMS.items():
        info = get_item_info(asset_id)
        if info:
            second = f" | 2nd: {info['second']:,}" if info.get("second") else ""
            status = " ← UNDER TARGET" if info["lowest"] <= max_p else ""
            print(f"  ✅ {info['name']}: {info['lowest']:,}{second}{status}")
        time.sleep(0.8)

    print("\n✅ Startup complete. Main loop...\n")

    while True:
        now = time.time()

        if now - last_uptime >= UPTIME_INTERVAL:
            send_uptime()
            last_uptime = now

        if now >= next_price_report:
            send_price_report()
            next_price_report = now + random.randint(PRICE_REPORT_MIN, PRICE_REPORT_MAX)

        print(f"--- Checking @ {datetime.now().strftime('%H:%M:%S')} ---")
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
                    remaining = int(ALERT_COOLDOWN - (now - last))
                    print(f"  ⏳ {info['name']} under target (cooldown {remaining}s)")
            else:
                second = f" | 2nd {info['second']:,}" if info.get("second") else ""
                print(f"  • {info['name']}: {lowest:,}{second} (max {max_p:,})")

            time.sleep(1.1)

        print(f"--- Cycle done, sleep {CHECK_INTERVAL}s ---\n")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()

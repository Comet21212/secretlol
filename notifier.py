"""
Roblox Limited Price Notifier
Updated with new items + uptime + periodic price reports
"""

import time
import random
import requests
from datetime import datetime, timezone
from typing import Dict, Tuple, Optional

# ====================== CONFIG ======================
WEBHOOK_URL = "https://discord.com/api/webhooks/1537206323429769247/DDanKqiYTeAUDMQjOt0UvDCV5UD4lEjtnrCGs9OtXJtqsuZn8YkMObq6xug12KK0J7pl"

ITEMS: Dict[int, Tuple[int, int]] = {
    # Previous items
    9255011:     (3500, 4300),   # Antlers Silver Lim
    10159617728: (5000, 6000),   # 8 Bit Tabby Cat
    1082932:     (1500, 2500),   # Traffic Cone
    14463095:    (4000, 5000),   # Pinstripe Fedora

    # New items
    1609390589:  (2000, 3000),   # Blue Traffic Cone
    16477149823: (4500, 5675),   # Golden Clockwork Headphones
    1609402609:  (1000, 1100),   # Black Iron Branches
    17408283:    (700, 800),     # Outrageous Builders Club Hat
}

CHECK_INTERVAL = 45          # seconds between price checks
ALERT_COOLDOWN = 300         # 5 min cooldown per item for alerts
UPTIME_INTERVAL = 45 * 60    # 45 minutes
PRICE_REPORT_MIN = 60 * 60   # 1 hour
PRICE_REPORT_MAX = 120 * 60  # 2 hours
# ====================================================

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
})

last_alerted: Dict[int, float] = {}
last_uptime = 0.0
next_price_report = time.time() + random.randint(PRICE_REPORT_MIN, PRICE_REPORT_MAX)
start_time = time.time()

def get_item_info(asset_id: int) -> Optional[dict]:
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
        }
    except Exception as e:
        print(f"[{asset_id}] Error: {e}")
        return None

def send_webhook(content: str = None, embeds: list = None):
    payload = {}
    if content:
        payload["content"] = content
    if embeds:
        payload["embeds"] = embeds

    try:
        r = session.post(WEBHOOK_URL, json=payload, timeout=10)
        if r.status_code not in (200, 204):
            print(f"❌ Webhook error {r.status_code}: {r.text}")
    except Exception as e:
        print(f"❌ Failed to send webhook: {e}")

def send_alert(info: dict, min_price: int, max_price: int):
    embed = {
        "title": f"🚨 PRICE ALERT: {info['name']}",
        "description": (
            f"**Current lowest:** `{info['lowest']:,}` Robux\n"
            f"**Your range:** `{min_price:,}` – `{max_price:,}` Robux\n\n"
            f"[Open on Roblox]({info['url']})"
        ),
        "color": 0xFF0000,  # red for urgency
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "Roblox Limited Notifier"},
    }

    # @everyone ping for real deals
    send_webhook(
        content="@everyone **DEAL FOUND!**",
        embeds=[embed]
    )
    print(f"✅ ALERT sent → {info['name']} @ {info['lowest']}")

def send_uptime():
    uptime_seconds = int(time.time() - start_time)
    hours = uptime_seconds // 3600
    minutes = (uptime_seconds % 3600) // 60

    embed = {
        "title": "🟢 Monitor Uptime",
        "description": f"Notifier is still running.\n**Uptime:** {hours}h {minutes}m",
        "color": 0x57F287,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    send_webhook(embeds=[embed])
    print("📡 Uptime message sent")

def send_price_report():
    print("📊 Generating price report...")
    lines = []
    for asset_id, (min_p, max_p) in ITEMS.items():
        info = get_item_info(asset_id)
        if info:
            status = "✅ IN RANGE" if min_p <= info["lowest"] <= max_p else "—"
            lines.append(f"**{info['name']}**: `{info['lowest']:,}` (want {min_p:,}–{max_p:,}) {status}")
        else:
            lines.append(f"**Asset {asset_id}**: Could not fetch")
        time.sleep(1.0)

    embed = {
        "title": "📈 Current Limited Prices",
        "description": "\n".join(lines),
        "color": 0x5865F2,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "Periodic price report"},
    }
    send_webhook(embeds=[embed])
    print("📊 Price report sent")

def main():
    global last_uptime, next_price_report

    print("🚀 Roblox Limited Notifier started")
    print(f"Watching {len(ITEMS)} items\n")

    while True:
        now = time.time()

        # --- Uptime check (every 45 min) ---
        if now - last_uptime >= UPTIME_INTERVAL:
            send_uptime()
            last_uptime = now

        # --- Random price report (every 1-2 hours) ---
        if now >= next_price_report:
            send_price_report()
            next_price_report = now + random.randint(PRICE_REPORT_MIN, PRICE_REPORT_MAX)

        # --- Main price checking (highest priority) ---
        for asset_id, (min_p, max_p) in ITEMS.items():
            info = get_item_info(asset_id)
            if not info:
                continue

            lowest = info["lowest"]

            if min_p <= lowest <= max_p:
                last = last_alerted.get(asset_id, 0)
                if now - last >= ALERT_COOLDOWN:
                    send_alert(info, min_p, max_p)
                    last_alerted[asset_id] = now
                else:
                    remaining = int(ALERT_COOLDOWN - (now - last))
                    print(f"[{info['name']}] In range ({lowest}) but cooldown ({remaining}s)")
            else:
                print(f"[{info['name']}] {lowest:,}  (want {min_p:,}–{max_p:,})")

            time.sleep(1.1)

        print(f"--- Cycle done, sleeping {CHECK_INTERVAL}s ---\n")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()

"""
Roblox Limited Price Notifier
Hardcoded version – ready to run
"""

import time
import requests
from datetime import datetime, timezone
from typing import Dict, Tuple, Optional

# ====================== YOUR CONFIG ======================
WEBHOOK_URL = "https://discord.com/api/webhooks/1537206323429769247/DDanKqiYTeAUDMQjOt0UvDCV5UD4lEjtnrCGs9OtXJtqsuZn8YkMObq6xug12KK0J7pl"

# asset_id: (min_price, max_price)
ITEMS: Dict[int, Tuple[int, int]] = {
    9255011:     (3500, 4300),   # Antlers Silver Lim
    10159617728: (5000, 6000),   # 8 Bit Tabby Cat
    1082932:     (1500, 2500),   # Traffic Cone
    14463095:    (4000, 5000),   # Pinstripe Fedora
}

CHECK_INTERVAL = 45      # seconds between full checks
ALERT_COOLDOWN = 300     # seconds before the same item can alert again
# =========================================================

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
})

last_alerted: Dict[int, float] = {}

def get_item_info(asset_id: int) -> Optional[dict]:
    url = f"https://catalog.roblox.com/v1/catalog/items/{asset_id}/details?itemType=Asset"
    try:
        resp = session.get(url, timeout=12)
        resp.raise_for_status()
        data = resp.json()

        lowest = data.get("lowestResalePrice") or data.get("lowestPrice")
        name = data.get("name", f"Asset {asset_id}")

        if lowest is None:
            print(f"[{asset_id}] No reseller price available")
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

def send_alert(info: dict, min_price: int, max_price: int):
    embed = {
        "title": f"🔔 Price Alert: {info['name']}",
        "description": (
            f"**Current lowest:** `{info['lowest']:,}` Robux\n"
            f"**Your range:** `{min_price:,}` – `{max_price:,}` Robux\n\n"
            f"[Open on Roblox]({info['url']})"
        ),
        "color": 0x57F287,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "Roblox Limited Notifier"},
    }

    payload = {
        "content": f"**Deal found!** {info['name']} is at **{info['lowest']:,}**",
        "embeds": [embed],
    }

    try:
        r = session.post(WEBHOOK_URL, json=payload, timeout=10)
        if r.status_code in (200, 204):
            print(f"✅ Alert sent → {info['name']} @ {info['lowest']}")
        else:
            print(f"❌ Webhook error {r.status_code}: {r.text}")
    except Exception as e:
        print(f"❌ Failed to send webhook: {e}")

def main():
    print("🚀 Roblox Limited Notifier started")
    print(f"Watching {len(ITEMS)} items | Interval: {CHECK_INTERVAL}s\n")

    while True:
        for asset_id, (min_p, max_p) in ITEMS.items():
            info = get_item_info(asset_id)
            if not info:
                continue

            lowest = info["lowest"]
            now = time.time()

            if min_p <= lowest <= max_p:
                last = last_alerted.get(asset_id, 0)
                if now - last >= ALERT_COOLDOWN:
                    send_alert(info, min_p, max_p)
                    last_alerted[asset_id] = now
                else:
                    remaining = int(ALERT_COOLDOWN - (now - last))
                    print(f"[{info['name']}] In range ({lowest}) but cooldown ({remaining}s left)")
            else:
                print(f"[{info['name']}] {lowest:,}  (want {min_p:,}–{max_p:,})")

            time.sleep(1.2)

        print(f"--- Cycle complete, sleeping {CHECK_INTERVAL}s ---\n")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()

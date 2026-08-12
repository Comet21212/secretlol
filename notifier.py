import requests
import time
from datetime import datetime, timezone

WEBHOOK_URL = "https://discord.com/api/webhooks/1537206323429769247/DDanKqiYTeAUDMQjOt0UvDCV5UD4lEjtnrCGs9OtXJtqsuZn8YkMObq6xug12KK0J7pl"

ITEMS = {
    1609390589:  (2000, 3000),   # Blue Traffic Cone
    16477149823: (4500, 5675),   # Golden Clockwork Headphones
    1609402609:  (1000, 1100),   # Black Iron Branches
    17408283:    (700, 800),     # Outrageous Builders Club Hat
}

def send_webhook(content):
    try:
        r = requests.post(WEBHOOK_URL, json={"content": content}, timeout=10)
        print(f"Webhook status: {r.status_code}")
        return r.status_code in (200, 204)
    except Exception as e:
        print(f"Webhook error: {e}")
        return False

print("=== SCRIPT STARTED ===")
send_webhook("**monitor started**")

print("\nChecking the 4 new items:")
for asset_id, (min_p, max_p) in ITEMS.items():
    url = f"https://catalog.roblox.com/v1/catalog/items/{asset_id}/details?itemType=Asset"
    try:
        resp = requests.get(url, timeout=12)
        data = resp.json()
        name = data.get("name", "Unknown")
        lowest = data.get("lowestResalePrice") or data.get("lowestPrice")
        print(f"{asset_id} | {name} → {lowest}")
    except Exception as e:
        print(f"{asset_id} → ERROR: {e}")
    time.sleep(1)

print("\n=== TEST FINISHED ===")
# Keep the process alive so Railway doesn't kill it immediately
while True:
    time.sleep(60)

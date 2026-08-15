"""
Roblox Limited Notifier – CSRF-hardened collectible + legacy buy
"""

import os
import time
import math
import json
import uuid
import asyncio
import requests
from pathlib import Path
from datetime import datetime, timezone, date
from typing import Dict, Optional, Tuple, List

import discord
from discord import app_commands
from discord.ext import commands, tasks

# ====================== CONFIG ======================
WEBHOOK_URL = "https://discord.com/api/webhooks/1537206323429769247/DDanKqiYTeAUDMQjOt0UvDCV5UD4lEjtnrCGs9OtXJtqsuZn8YkMObq6xug12KK0J7pl"

DEFAULT_ITEMS: Dict[int, int] = {
    9255011:         4300,
    10159617728:     6000,
    1082932:         2500,
    14463095:        5000,
    1609390589:      3000,
    16477149823:     5675,
    1609402609:      1100,
    17408283:         800,
    123375593579461:  140,
    6803401743:       150,
    4773588762:       120,
    87983592197138:  8500,
    13241836994:     6000,   # Verdant Crown
}

CHECK_INTERVAL = 50
AUTO_BUY_INTERVAL = 5
ALERT_COOLDOWN = 300
PRICE_REPORT_INTERVAL = 15 * 60
UPTIME_INTERVAL = 45 * 60
SELLER_KEEP = 0.70
REQUEST_DELAY = 1.5
DAILY_ROBUX_CAP = int(os.getenv("DAILY_ROBUX_CAP", "15000"))
MIN_AUTO_PROFIT = 0
# ====================================================

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
ROBLOSECURITY = os.getenv("ROBLOSECURITY", "").strip()
ALERT_CHANNEL_ID = os.getenv("ALERT_CHANNEL_ID", "").strip()

ITEMS_FILE = Path("items.json")
PURCHASE_FILE = Path("purchase_targets.json")
SPEND_FILE = Path("daily_spend.json")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
})
if ROBLOSECURITY:
    session.cookies.set(".ROBLOSECURITY", ROBLOSECURITY, domain=".roblox.com")

last_alerted: Dict[int, float] = {}
start_time = time.time()
csrf_token: Optional[str] = None
last_uptime_sent = 0.0
AUTO_BUY_PAUSED = False
ROBLOX_USERNAME = "Unknown"
ROBLOX_USER_ID: Optional[int] = None

# ---------- Persistence ----------

def load_items() -> Dict[int, int]:
    if ITEMS_FILE.exists():
        try:
            return {int(k): int(v) for k, v in json.loads(ITEMS_FILE.read_text()).items()}
        except Exception as e:
            print("load items:", e)
    return DEFAULT_ITEMS.copy()

def save_items():
    try:
        ITEMS_FILE.write_text(json.dumps({str(k): v for k, v in ITEMS.items()}, indent=2))
    except Exception as e:
        print("save items:", e)

def load_purchase_targets() -> Dict[int, int]:
    if PURCHASE_FILE.exists():
        try:
            return {int(k): int(v) for k, v in json.loads(PURCHASE_FILE.read_text()).items()}
        except Exception as e:
            print("load purchase:", e)
    return {}

def save_purchase_targets():
    try:
        PURCHASE_FILE.write_text(json.dumps({str(k): v for k, v in PURCHASE_TARGETS.items()}, indent=2))
    except Exception as e:
        print("save purchase:", e)

def load_daily_spend() -> dict:
    today = str(date.today())
    if SPEND_FILE.exists():
        try:
            data = json.loads(SPEND_FILE.read_text())
            if data.get("day") == today:
                return data
        except Exception:
            pass
    return {"day": today, "spent": 0}

def save_daily_spend(data: dict):
    try:
        SPEND_FILE.write_text(json.dumps(data, indent=2))
    except Exception as e:
        print("save spend:", e)

def get_spent_today() -> int:
    return int(load_daily_spend().get("spent", 0))

def add_spent(amount: int):
    data = load_daily_spend()
    data["spent"] = int(data.get("spent", 0)) + amount
    save_daily_spend(data)

ITEMS: Dict[int, int] = load_items()
PURCHASE_TARGETS: Dict[int, int] = load_purchase_targets()

# ---------- Auth / CSRF ----------

def fetch_authenticated_user() -> str:
    global ROBLOX_USERNAME, ROBLOX_USER_ID
    if not ROBLOSECURITY:
        ROBLOX_USERNAME, ROBLOX_USER_ID = "Unknown", None
        return ROBLOX_USERNAME
    try:
        r = session.get("https://users.roblox.com/v1/users/authenticated", timeout=10)
        if r.status_code == 200:
            data = r.json()
            ROBLOX_USERNAME = data.get("name") or data.get("displayName") or "Unknown"
            ROBLOX_USER_ID = data.get("id")
            return ROBLOX_USERNAME
    except Exception as e:
        print("auth user:", e)
    ROBLOX_USERNAME, ROBLOX_USER_ID = "Unknown", None
    return ROBLOX_USERNAME

def mention_user() -> str:
    return f"**@{ROBLOX_USERNAME}**" if ROBLOX_USERNAME != "Unknown" else "**Unknown**"

def refresh_csrf() -> Optional[str]:
    global csrf_token
    try:
        r = session.post("https://auth.roblox.com/v2/logout", timeout=12)
        token = r.headers.get("x-csrf-token")
        if token:
            csrf_token = token
            session.headers["X-CSRF-TOKEN"] = token
            return token
    except Exception as e:
        print("CSRF:", e)
    return None

def apply_csrf_from_response(r: requests.Response) -> Optional[str]:
    """Roblox often returns a fresh token on 403."""
    global csrf_token
    token = r.headers.get("x-csrf-token") or r.headers.get("X-CSRF-TOKEN")
    if token:
        csrf_token = token
        session.headers["X-CSRF-TOKEN"] = token
        return token
    return None

def net_after_tax(price: int) -> int:
    return math.floor(price * SELLER_KEEP)

def break_even_list_price(buy: int) -> int:
    return math.ceil(buy / SELLER_KEEP)

# ---------- Catalog / economy ----------

def _get_json(url: str, retries: int = 2) -> Optional[dict]:
    for attempt in range(retries + 1):
        try:
            r = session.get(url, timeout=12)
            if r.status_code == 429:
                time.sleep(2 + attempt * 2)
                continue
            if r.status_code != 200:
                return None
            return r.json()
        except Exception:
            time.sleep(1)
    return None

def get_catalog_info(asset_id: int) -> Optional[dict]:
    data = _get_json(
        f"https://catalog.roblox.com/v1/catalog/items/{asset_id}/details?itemType=Asset"
    )
    if not data:
        return None
    lowest = data.get("lowestResalePrice") or data.get("lowestPrice")
    return {
        "name": data.get("name") or f"Asset {asset_id}",
        "lowest": int(lowest) if lowest is not None else None,
        "asset_id": asset_id,
        "product_id": data.get("productId"),
        "collectible_item_id": data.get("collectibleItemId"),
        "url": f"https://www.roblox.com/catalog/{asset_id}/",
    }

def get_economy_details(asset_id: int) -> Optional[dict]:
    return _get_json(f"https://economy.roblox.com/v2/assets/{asset_id}/details")

def get_rap(asset_id: int) -> Optional[int]:
    data = _get_json(f"https://economy.roblox.com/v1/assets/{asset_id}/resale-data")
    if data and data.get("recentAveragePrice"):
        return int(data["recentAveragePrice"])
    return None

def resolve_ids(asset_id: int) -> dict:
    out = {
        "name": f"ID {asset_id}",
        "product_id": None,
        "collectible_item_id": None,
        "url": f"https://www.roblox.com/catalog/{asset_id}/",
    }
    cat = get_catalog_info(asset_id)
    if cat:
        out["name"] = cat.get("name") or out["name"]
        out["product_id"] = cat.get("product_id")
        out["collectible_item_id"] = cat.get("collectible_item_id")

    if not out["product_id"] or not out["collectible_item_id"]:
        eco = get_economy_details(asset_id)
        if eco:
            out["name"] = eco.get("Name") or eco.get("name") or out["name"]
            out["product_id"] = out["product_id"] or eco.get("ProductId")
            out["collectible_item_id"] = (
                out["collectible_item_id"]
                or eco.get("CollectibleItemId")
                or eco.get("collectibleItemId")
            )
    return out

# ---------- Resellers ----------

def _parse_legacy_resellers(data: list) -> List[dict]:
    rows = []
    for row in data or []:
        price = row.get("price")
        uaid = row.get("userAssetId") or row.get("userAssetID")
        seller = row.get("seller") or {}
        seller_id = seller.get("id") if isinstance(seller, dict) else row.get("sellerId")
        if price is None:
            continue
        rows.append({
            "price": int(price),
            "user_asset_id": uaid,
            "seller_id": seller_id,
            "collectible_product_id": None,
            "source": "legacy",
        })
    return rows

def _parse_collectible_resellers(data: list) -> List[dict]:
    rows = []
    for row in data or []:
        price = row.get("price")
        cpid = (
            row.get("collectibleProductId")
            or row.get("collectibleItemInstanceId")
            or row.get("productId")
        )
        seller = row.get("seller") or {}
        seller_id = seller.get("sellerId") or seller.get("id") or row.get("sellerId")
        uaid = row.get("userAssetId")
        if price is None:
            continue
        rows.append({
            "price": int(price),
            "user_asset_id": uaid,
            "seller_id": seller_id,
            "collectible_product_id": cpid,
            "source": "collectible",
        })
    return rows

def get_resellers_full(asset_id: int, collectible_item_id: Optional[str] = None) -> List[dict]:
    if not ROBLOSECURITY:
        return []

    # Legacy
    try:
        if not csrf_token:
            refresh_csrf()
        url = f"https://economy.roblox.com/v1/assets/{asset_id}/resellers?limit=10"
        r = session.get(url, timeout=12)
        if r.status_code == 403:
            apply_csrf_from_response(r)
            refresh_csrf()
            r = session.get(url, timeout=12)
        if r.status_code == 200:
            rows = _parse_legacy_resellers(r.json().get("data", []))
            if rows:
                return rows
    except Exception as e:
        print(f"legacy resellers {asset_id}:", e)

    # Collectible
    cid = collectible_item_id
    if not cid:
        cid = resolve_ids(asset_id).get("collectible_item_id")

    if cid:
        try:
            if not csrf_token:
                refresh_csrf()
            url = f"https://apis.roblox.com/marketplace-sales/v1/item/{cid}/resellers?limit=30"
            r = session.get(url, timeout=12)
            if r.status_code == 403:
                apply_csrf_from_response(r)
                refresh_csrf()
                r = session.get(url, timeout=12)
            if r.status_code == 200:
                payload = r.json()
                data = payload.get("data") or payload.get("resellers") or []
                rows = _parse_collectible_resellers(data if isinstance(data, list) else [])
                if rows:
                    return rows
        except Exception as e:
            print(f"collectible resellers {asset_id}:", e)

    return []

def get_resellers_retry(asset_id: int, collectible_item_id: Optional[str] = None, tries: int = 3) -> List[dict]:
    for i in range(tries):
        refresh_csrf()
        rows = get_resellers_full(asset_id, collectible_item_id)
        if rows:
            return rows
        time.sleep(0.6 + i * 0.8)
    return []

def get_item_info(asset_id: int) -> Optional[dict]:
    ids = resolve_ids(asset_id)
    name = ids["name"]
    lowest = None
    second = None

    cat = get_catalog_info(asset_id)
    if cat:
        name = cat.get("name") or name
        lowest = cat.get("lowest")
        ids["product_id"] = ids["product_id"] or cat.get("product_id")
        ids["collectible_item_id"] = ids["collectible_item_id"] or cat.get("collectible_item_id")

    resellers = get_resellers_full(asset_id, ids.get("collectible_item_id"))
    if resellers:
        lowest = resellers[0]["price"]
        if len(resellers) > 1:
            second = resellers[1]["price"]

    if lowest is None:
        time.sleep(1.0)
        resellers = get_resellers_retry(asset_id, ids.get("collectible_item_id"), tries=2)
        if resellers:
            lowest = resellers[0]["price"]
            if len(resellers) > 1:
                second = resellers[1]["price"]

    if lowest is None:
        return None

    return {
        "name": name,
        "lowest": int(lowest),
        "second": second,
        "rap": get_rap(asset_id),
        "asset_id": asset_id,
        "product_id": ids.get("product_id"),
        "collectible_item_id": ids.get("collectible_item_id"),
        "url": ids["url"],
    }

def suggest_sell(lowest: int, second: Optional[int], rap: Optional[int], max_price: int) -> Tuple[int, str]:
    be = break_even_list_price(lowest)
    candidates: List[Tuple[int, str]] = []
    if second and second > lowest:
        candidates.append((int(second), "2nd seller"))
    if rap and rap > lowest:
        candidates.append((int(rap), "RAP"))
    if candidates:
        suggested, source = max(candidates, key=lambda x: x[0])
        if suggested < be:
            return be, f"{source} → raised to break-even"
        return suggested, source
    return max(be, max_price), "break-even / your max"

def build_profit_text(lowest: int, second: Optional[int], rap: Optional[int], max_price: int) -> str:
    suggested, source = suggest_sell(lowest, second, rap, max_price)
    net = net_after_tax(suggested)
    profit = net - lowest
    gap = (second - lowest) if second else 0
    be = break_even_list_price(lowest)

    lines = [f"**Buy (1st):** `{lowest:,}` R$"]
    if second:
        lines.append(f"**2nd seller:** `{second:,}` R$ (gap `{gap:,}`)")
    if rap:
        lines.append(f"**Usual / RAP:** `~{rap:,}` R$")
    lines.append(f"**Break-even list (after tax):** `~{be:,}` R$")
    lines.append(f"**Suggested sell:** `~{suggested:,}` ({source})")
    lines.append(f"**After 30% tax you get:** `{net:,}` R$")
    lines.append(f"**Possible profit:** `{'+' if profit >= 0 else ''}{profit:,}` R$")

    notes = []
    if gap >= 400:
        notes.append("🔥 Large 1st→2nd gap")
    if rap and lowest < rap * 0.85:
        notes.append("📉 Below RAP")
    if profit < 100:
        notes.append("⚠️ Thin / negative after tax — risky flip")
    if notes:
        lines.append("")
        lines.extend(notes)
    return "\n".join(lines)

# ---------- Purchase (CSRF hardened) ----------

def attempt_purchase(asset_id: int, locked_max: int) -> Tuple[bool, str, int]:
    global csrf_token
    if not ROBLOSECURITY:
        return False, "No ROBLOSECURITY cookie.", 0

    refresh_csrf()
    ids = resolve_ids(asset_id)
    resellers = get_resellers_retry(asset_id, ids.get("collectible_item_id"), tries=3)
    if not resellers:
        return False, "Could not fetch current resellers (legacy + collectible).", 0

    offer = resellers[0]
    price = int(offer["price"])
    if price > locked_max:
        return False, f"Price rose. Locked `{locked_max:,}` → now `{price:,}`.", 0

    spent = get_spent_today()
    if spent + price > DAILY_ROBUX_CAP:
        return False, f"Daily cap. Spent `{spent:,}` / `{DAILY_ROBUX_CAP:,}`. Buy `{price:,}`.", 0

    rap = get_rap(asset_id)
    second = resellers[1]["price"] if len(resellers) > 1 else None
    suggested, _ = suggest_sell(price, second, rap, locked_max)
    projected = net_after_tax(suggested) - price
    if MIN_AUTO_PROFIT and projected < MIN_AUTO_PROFIT:
        return False, (
            f"Profit gate: projected `{projected:,}` < min `{MIN_AUTO_PROFIT:,}` "
            f"(buy `{price:,}`, suggest `{suggested:,}`)."
        ), 0

    def post_with_csrf_retry(url: str, body: dict) -> requests.Response:
        """POST; on 403 grab x-csrf-token from response and retry once."""
        refresh_csrf()
        headers = {
            "X-CSRF-TOKEN": csrf_token or "",
            "Content-Type": "application/json",
        }
        r = session.post(url, json=body, headers=headers, timeout=15)
        if r.status_code == 403:
            new_token = apply_csrf_from_response(r)
            if not new_token:
                refresh_csrf()
                new_token = csrf_token
            headers["X-CSRF-TOKEN"] = new_token or ""
            r = session.post(url, json=body, headers=headers, timeout=15)
        return r

    collectible_err = "Collectible path not attempted"

    # Path A: collectible
    cid = ids.get("collectible_item_id")
    cpid = offer.get("collectible_product_id")
    if cid and cpid and ROBLOX_USER_ID:
        body = {
            "collectibleItemId": cid,
            "expectedCurrency": 1,
            "expectedPrice": price,
            "expectedPurchaserId": ROBLOX_USER_ID,
            "expectedPurchaserType": "User",
            "expectedSellerId": offer.get("seller_id"),
            "expectedSellerType": "User",
            "idempotencyKey": str(uuid.uuid4()),
            "collectibleProductId": cpid,
        }
        url = f"https://apis.roblox.com/marketplace-sales/v1/item/{cid}/purchase-item"
        try:
            r = post_with_csrf_retry(url, body)
            if r.status_code in (200, 201):
                add_spent(price)
                return True, f"Collectible purchase accepted at `{price:,}` R$.", price
            try:
                err = r.json()
                msg = err.get("message") or err.get("errorMessage") or str(err)[:220]
            except Exception:
                msg = r.text[:220]
            collectible_err = f"Collectible path HTTP {r.status_code}: {msg}"
        except Exception as e:
            collectible_err = f"Collectible path error: {e}"
    else:
        collectible_err = "No collectible ids for this listing"

    # Path B: legacy
    product_id = ids.get("product_id") or asset_id
    uaid = offer.get("user_asset_id")
    if not uaid:
        return False, (
            f"{collectible_err}. Legacy path needs userAssetId (missing). "
            f"Price was `{price:,}`."
        ), 0

    payload = {
        "expectedCurrency": 1,
        "expectedPrice": price,
        "expectedSellerId": offer.get("seller_id"),
        "userAssetId": uaid,
    }
    url = f"https://economy.roblox.com/v1/purchases/products/{product_id}"
    try:
        r = post_with_csrf_retry(url, payload)
        if r.status_code in (200, 201):
            add_spent(price)
            return True, f"Legacy purchase accepted at `{price:,}` R$.", price
        try:
            err = r.json()
            msg = err.get("message") or err.get("errors", [{}])[0].get("message") or str(err)[:220]
        except Exception:
            msg = r.text[:220]
        return False, f"{collectible_err} | Legacy HTTP {r.status_code}: {msg}", 0
    except Exception as e:
        return False, f"{collectible_err} | Legacy error: {e}", 0

# ---------- Discord UI ----------

class BuyView(discord.ui.View):
    def __init__(self, asset_id: int, locked_price: int, item_name: str):
        super().__init__(timeout=3600)
        self.asset_id = asset_id
        self.locked_price = locked_price
        self.item_name = item_name

    @discord.ui.button(label="Buy", style=discord.ButtonStyle.success, emoji="🛒")
    async def buy_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        success, message, _ = await asyncio.to_thread(
            attempt_purchase, self.asset_id, self.locked_price
        )
        embed = discord.Embed(
            title="✅ Purchase Attempted" if success else "❌ Purchase Refused / Failed",
            description=f"**{self.item_name}**\n{message}",
            color=0x57F287 if success else 0xED4245,
        )
        if success:
            button.disabled = True
            try:
                await interaction.message.edit(view=self)
            except Exception:
                pass
        await interaction.followup.send(embed=embed, ephemeral=True)

async def send_to_channel_or_webhook(
    content: str = None, embed: discord.Embed = None, view: discord.ui.View = None
):
    if ALERT_CHANNEL_ID:
        channel = bot.get_channel(int(ALERT_CHANNEL_ID))
        if channel:
            await channel.send(content=content, embed=embed, view=view)
            return
    payload = {}
    if content:
        payload["content"] = content
    if embed:
        payload["embeds"] = [embed.to_dict()]
    try:
        await asyncio.to_thread(lambda: requests.post(WEBHOOK_URL, json=payload, timeout=10))
    except Exception as e:
        print("Send failed:", e)

async def send_alert(info: dict, max_price: int):
    lowest = info["lowest"]
    body = build_profit_text(lowest, info.get("second"), info.get("rap"), max_price)
    embed = discord.Embed(
        title="🚨 DEAL SPOTTED",
        description=(
            f"{mention_user()} spotted a deal on **{info['name']}**\n\n"
            f"{body}\n\n[Open on Roblox]({info['url']})"
        ),
        color=0xFF0000,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text=f"Locked buy: {lowest:,} R$")
    view = BuyView(info["asset_id"], lowest, info["name"])
    await send_to_channel_or_webhook(content="@everyone **DEAL FOUND!**", embed=embed, view=view)

async def try_auto_buy(info: dict, max_price: int):
    if AUTO_BUY_PAUSED:
        return
    name = info["name"]
    locked = info["lowest"]
    success, message, _ = await asyncio.to_thread(attempt_purchase, info["asset_id"], locked)

    if success:
        embed = discord.Embed(
            title="🛒 AUTO-BUY FIRED",
            description=(
                f"{mention_user()}'s auto-buy hit **{name}**\n{message}\n"
                f"Max `{max_price:,}` | Today `{get_spent_today():,}` / `{DAILY_ROBUX_CAP:,}`\n\n"
                f"[Open]({info['url']})"
            ),
            color=0x57F287,
            timestamp=datetime.now(timezone.utc),
        )
        await send_to_channel_or_webhook(content="@everyone **AUTO-BUY EXECUTED**", embed=embed)
    else:
        body = build_profit_text(locked, info.get("second"), info.get("rap"), max_price)
        embed = discord.Embed(
            title="❌ AUTO-BUY FAILED",
            description=(
                f"{mention_user()} — auto-buy failed on **{name}**\n{message}\n\n"
                f"{body}\n\n[Open]({info['url']})"
            ),
            color=0xED4245,
            timestamp=datetime.now(timezone.utc),
        )
        view = BuyView(info["asset_id"], locked, name)
        await send_to_channel_or_webhook(
            content="@everyone **AUTO-BUY FAILED – manual Buy available**",
            embed=embed,
            view=view,
        )

# ---------- Tasks ----------

@tasks.loop(seconds=CHECK_INTERVAL)
async def price_monitor():
    global last_uptime_sent
    now = time.time()
    if now - last_uptime_sent >= UPTIME_INTERVAL:
        uptime = int(now - start_time)
        h, m = uptime // 3600, (uptime % 3600) // 60
        embed = discord.Embed(
            title="🟢 Still live",
            description=(
                f"{mention_user()} watching **{len(ITEMS)}** limiteds\n"
                f"**Uptime:** {h}h {m}m | Auto-buy: {'PAUSED' if AUTO_BUY_PAUSED else 'ON'}\n"
                f"**Today:** `{get_spent_today():,}` / `{DAILY_ROBUX_CAP:,}`"
            ),
            color=0x57F287,
        )
        await send_to_channel_or_webhook(embed=embed)
        last_uptime_sent = now

    for asset_id, max_p in list(ITEMS.items()):
        if asset_id in PURCHASE_TARGETS:
            await asyncio.sleep(0.2)
            continue
        info = await asyncio.to_thread(get_item_info, asset_id)
        if not info:
            await asyncio.sleep(REQUEST_DELAY)
            continue
        if info["lowest"] <= max_p:
            last = last_alerted.get(asset_id, 0)
            if now - last >= ALERT_COOLDOWN:
                await send_alert(info, max_p)
                last_alerted[asset_id] = now
        await asyncio.sleep(REQUEST_DELAY)

@tasks.loop(seconds=AUTO_BUY_INTERVAL)
async def auto_buy_monitor():
    if AUTO_BUY_PAUSED or not PURCHASE_TARGETS:
        return
    now = time.time()
    for asset_id, max_p in list(PURCHASE_TARGETS.items()):
        if now - last_alerted.get(asset_id, 0) < ALERT_COOLDOWN:
            continue
        info = await asyncio.to_thread(get_item_info, asset_id)
        if not info:
            await asyncio.sleep(0.3)
            continue
        if info["lowest"] <= max_p:
            last_alerted[asset_id] = now
            await try_auto_buy(info, max_p)
        await asyncio.sleep(0.4)

@tasks.loop(seconds=PRICE_REPORT_INTERVAL)
async def price_report_task():
    lines = []
    for asset_id, max_p in list(ITEMS.items()):
        info = await asyncio.to_thread(get_item_info, asset_id)
        if not info:
            lines.append(f"**ID {asset_id}**: No data")
            await asyncio.sleep(REQUEST_DELAY)
            continue
        lowest, second = info["lowest"], info.get("second")
        status = "✅ UNDER" if lowest <= max_p else ""
        tag = " 🛒" if asset_id in PURCHASE_TARGETS else ""
        extra = f" → 2nd `{second:,}`" if second else ""
        lines.append(f"**{info['name']}**{tag}: `{lowest:,}`{extra} {status}")
        await asyncio.sleep(REQUEST_DELAY)
    embed = discord.Embed(
        title=f"📈 Live prices — {mention_user()}",
        description="\n".join(lines) or "No items",
        color=0x5865F2,
        timestamp=datetime.now(timezone.utc),
    )
    await send_to_channel_or_webhook(embed=embed)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    try:
        print(f"Synced {len(await bot.tree.sync())} commands")
    except Exception as e:
        print("Sync error:", e)

    if ROBLOSECURITY:
        await asyncio.to_thread(refresh_csrf)
        await asyncio.to_thread(fetch_authenticated_user)
        print(f"Roblox user: {ROBLOX_USERNAME} ({ROBLOX_USER_ID})")
    else:
        print("⚠️ No ROBLOSECURITY")

    for t in (price_monitor, price_report_task, auto_buy_monitor):
        if not t.is_running():
            t.start()

    await send_to_channel_or_webhook(
        content=(
            f"👀 {mention_user()} is now **live** — watching **{len(ITEMS)}** limiteds\n"
            f"CSRF retry enabled | Auto-buy every `{AUTO_BUY_INTERVAL}s`"
        )
    )

# ---------- Commands ----------

@bot.tree.command(name="monitorall", description="Force full price report")
async def monitorall(interaction: discord.Interaction):
    await interaction.response.defer()
    await interaction.followup.send(f"🔍 {mention_user()} requested a full check…")
    await price_report_task()

@bot.tree.command(name="targetitem", description="Watch – alert + Buy button only")
@app_commands.describe(asset_id="Asset ID", max_price="Max price")
async def targetitem(interaction: discord.Interaction, asset_id: int, max_price: int):
    await interaction.response.defer()
    ITEMS[asset_id] = max_price
    PURCHASE_TARGETS.pop(asset_id, None)
    save_items()
    save_purchase_targets()
    await interaction.followup.send(
        f"👀 {mention_user()} is now watching **{asset_id}** (max `{max_price:,}`) — alert only"
    )

@bot.tree.command(name="targetpurchase", description="Watch + AUTO-BUY when ≤ max")
@app_commands.describe(asset_id="Asset ID", max_price="Max auto-buy price")
async def targetpurchase(interaction: discord.Interaction, asset_id: int, max_price: int):
    await interaction.response.defer()
    ITEMS[asset_id] = max_price
    PURCHASE_TARGETS[asset_id] = max_price
    save_items()
    save_purchase_targets()
    await interaction.followup.send(
        f"🛒 {mention_user()} armed auto-buy on **{asset_id}** ≤ `{max_price:,}`"
    )

@bot.tree.command(name="pause", description="Pause auto-buy")
async def pause_cmd(interaction: discord.Interaction):
    global AUTO_BUY_PAUSED
    await interaction.response.defer()
    AUTO_BUY_PAUSED = True
    await interaction.followup.send(f"⏸️ {mention_user()} paused auto-buy.")

@bot.tree.command(name="resume", description="Resume auto-buy")
async def resume_cmd(interaction: discord.Interaction):
    global AUTO_BUY_PAUSED
    await interaction.response.defer()
    AUTO_BUY_PAUSED = False
    await interaction.followup.send(f"▶️ {mention_user()} resumed auto-buy.")

@bot.tree.command(name="showlist", description="Show watch list")
async def showlist(interaction: discord.Interaction):
    await interaction.response.defer()
    lines = [
        f"**{aid}** → max `{price:,}`" + (" 🛒 AUTO" if aid in PURCHASE_TARGETS else "")
        for aid, price in ITEMS.items()
    ]
    embed = discord.Embed(
        title=f"👀 {ROBLOX_USERNAME}'s watch list",
        description="\n".join(lines) or "Empty",
        color=0x5865F2,
    )
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="showprofitables", description="Gaps + after-tax profit")
async def showprofitables(interaction: discord.Interaction):
    await interaction.response.defer()
    lines = []
    for asset_id, max_p in list(ITEMS.items()):
        info = await asyncio.to_thread(get_item_info, asset_id)
        await asyncio.sleep(REQUEST_DELAY)
        if not info:
            continue
        lowest, second, rap = info["lowest"], info.get("second"), info.get("rap")
        under = lowest <= max_p
        gap = (second - lowest) if second else 0
        if not under and gap < 150:
            continue
        body = build_profit_text(lowest, second, rap, max_p)
        auto = " 🛒" if asset_id in PURCHASE_TARGETS else ""
        lines.append(f"**{info['name']}**{auto} (`{asset_id}`)\n{body}\n")
    if not lines:
        await interaction.followup.send("No strong gaps / under-target items.")
    else:
        embed = discord.Embed(
            title=f"💰 {ROBLOX_USERNAME}'s opportunities",
            description="\n".join(lines),
            color=0x57F287,
            timestamp=datetime.now(timezone.utc),
        )
        await interaction.followup.send(embed=embed)

@bot.tree.command(name="remove", description="Stop watching an item")
@app_commands.describe(asset_id="Asset ID")
async def remove_item(interaction: discord.Interaction, asset_id: int):
    await interaction.response.defer()
    if asset_id in ITEMS:
        ITEMS.pop(asset_id, None)
        PURCHASE_TARGETS.pop(asset_id, None)
        save_items()
        save_purchase_targets()
        await interaction.followup.send(f"🗑️ {mention_user()} stopped watching **{asset_id}**.")
    else:
        await interaction.followup.send("Not in list.", ephemeral=True)

@bot.tree.command(name="status", description="Status")
async def status(interaction: discord.Interaction):
    await interaction.response.defer()
    uptime = int(time.time() - start_time)
    h, m = uptime // 3600, (uptime % 3600) // 60
    embed = discord.Embed(
        title="🟢 Live status",
        description=(
            f"**Roblox:** {mention_user()}\n"
            f"**Uptime:** {h}h {m}m\n"
            f"**Watching:** {len(ITEMS)} | Auto targets: {len(PURCHASE_TARGETS)}\n"
            f"**Auto-buy:** {'⏸️ PAUSED' if AUTO_BUY_PAUSED else '▶️ ON'}\n"
            f"**Today spent:** `{get_spent_today():,}` / `{DAILY_ROBUX_CAP:,}`\n"
            f"**Cookie:** {'Yes' if ROBLOSECURITY else 'No'}"
        ),
        color=0x57F287,
    )
    await interaction.followup.send(embed=embed)

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("❌ DISCORD_TOKEN missing")
    else:
        bot.run(DISCORD_TOKEN)

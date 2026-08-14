"""
Roblox Limited Notifier – Final (today)
Live-watch messages + username from cookie
/targetitem = alert + Buy button
/targetpurchase = fast auto-buy
/pause /resume + daily Robux cap
"""

import os
import time
import math
import json
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
    9255011:         4300,   # Silverthorn Antlers
    10159617728:     6000,   # 8-Bit Tabby Cat
    1082932:         2500,   # Traffic Cone
    14463095:        5000,   # Pinstripe Fedora
    1609390589:      3000,   # Blue Traffic Cone
    16477149823:     5675,   # Golden Clockwork Headphones
    1609402609:      1100,   # Black Iron Branches
    17408283:         800,   # Outrageous Builders Club Hard Hat
    123375593579461:  140,   # Innovation Awards Ribbon
    6803401743:       150,   # Gucci Glasses Crystal
    4773588762:       120,   # Despacitegg
    87983592197138:  8500,   # Lord of Buxeration
    13241836994:     6000,   # Verdant Crown
}

CHECK_INTERVAL = 50
AUTO_BUY_INTERVAL = 4
ALERT_COOLDOWN = 300
PRICE_REPORT_INTERVAL = 15 * 60
UPTIME_INTERVAL = 45 * 60
SELLER_KEEP = 0.70
REQUEST_DELAY = 1.5
DAILY_ROBUX_CAP = int(os.getenv("DAILY_ROBUX_CAP", "15000"))
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
            data = json.loads(ITEMS_FILE.read_text())
            return {int(k): int(v) for k, v in data.items()}
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
            data = json.loads(PURCHASE_FILE.read_text())
            return {int(k): int(v) for k, v in data.items()}
        except Exception as e:
            print("load purchase:", e)
    return {}

def save_purchase_targets():
    try:
        PURCHASE_FILE.write_text(
            json.dumps({str(k): v for k, v in PURCHASE_TARGETS.items()}, indent=2)
        )
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

# ---------- Roblox auth / helpers ----------

def fetch_authenticated_user() -> str:
    """Resolve username from .ROBLOSECURITY."""
    global ROBLOX_USERNAME, ROBLOX_USER_ID
    if not ROBLOSECURITY:
        ROBLOX_USERNAME = "Unknown"
        ROBLOX_USER_ID = None
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
    ROBLOX_USERNAME = "Unknown"
    ROBLOX_USER_ID = None
    return ROBLOX_USERNAME

def mention_user() -> str:
    return f"**@{ROBLOX_USERNAME}**" if ROBLOX_USERNAME != "Unknown" else "**Unknown**"

def net_after_tax(price: int) -> int:
    return math.floor(price * SELLER_KEEP)

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

def get_resellers_full(asset_id: int) -> List[dict]:
    if not ROBLOSECURITY:
        return []
    try:
        if not csrf_token:
            refresh_csrf()
        url = f"https://economy.roblox.com/v1/assets/{asset_id}/resellers?limit=10"
        r = session.get(url, timeout=12)
        if r.status_code == 403:
            refresh_csrf()
            r = session.get(url, timeout=12)
        if r.status_code == 429:
            time.sleep(3)
            return []
        if r.status_code != 200:
            return []
        return r.json().get("data", []) or []
    except Exception as e:
        print(f"resellers {asset_id}:", e)
        return []

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
        "url": f"https://www.roblox.com/catalog/{asset_id}/",
    }

def get_economy_name(asset_id: int) -> Optional[str]:
    data = _get_json(f"https://economy.roblox.com/v2/assets/{asset_id}/details")
    if data:
        return data.get("Name") or data.get("name")
    return None

def get_rap(asset_id: int) -> Optional[int]:
    data = _get_json(f"https://economy.roblox.com/v1/assets/{asset_id}/resale-data")
    if data and data.get("recentAveragePrice"):
        return int(data["recentAveragePrice"])
    return None

def get_item_info(asset_id: int) -> Optional[dict]:
    name = None
    lowest = None
    second = None
    url = f"https://www.roblox.com/catalog/{asset_id}/"

    cat = get_catalog_info(asset_id)
    if cat:
        name = cat.get("name")
        lowest = cat.get("lowest")

    resellers = get_resellers_full(asset_id)
    if resellers:
        try:
            if resellers[0].get("price") is not None:
                lowest = int(resellers[0]["price"])
            if len(resellers) > 1 and resellers[1].get("price") is not None:
                second = int(resellers[1]["price"])
        except Exception:
            pass

    if lowest is None:
        time.sleep(1.2)
        resellers = get_resellers_full(asset_id)
        if resellers and resellers[0].get("price") is not None:
            lowest = int(resellers[0]["price"])
            if len(resellers) > 1 and resellers[1].get("price") is not None:
                second = int(resellers[1]["price"])
        if lowest is None:
            cat = get_catalog_info(asset_id)
            if cat and cat.get("lowest") is not None:
                lowest = cat["lowest"]
                name = name or cat.get("name")

    if lowest is None:
        return None

    if not name:
        name = get_economy_name(asset_id) or f"ID {asset_id}"

    return {
        "name": name,
        "lowest": int(lowest),
        "second": second,
        "rap": get_rap(asset_id),
        "asset_id": asset_id,
        "url": url,
    }

def build_profit_text(lowest: int, second: Optional[int], rap: Optional[int], max_price: int) -> str:
    if second and second > lowest:
        suggested, source = second, "2nd seller"
    elif rap and rap > lowest:
        suggested, source = rap, "RAP"
    else:
        suggested, source = max(int(max_price * 0.92), lowest + 50), "target"

    net = net_after_tax(suggested)
    profit = net - lowest
    gap = (second - lowest) if second else 0

    lines = [f"**Buy (1st):** `{lowest:,}` R$"]
    if second:
        lines.append(f"**2nd seller:** `{second:,}` R$ (gap `{gap:,}`)")
    if rap:
        lines.append(f"**Usual / RAP:** `~{rap:,}` R$")
    lines.append(f"**Suggested sell:** `~{suggested:,}` ({source})")
    lines.append(f"**After 30% tax you get:** `{net:,}` R$")
    lines.append(f"**Possible profit:** `{'+' if profit >= 0 else ''}{profit:,}` R$")

    notes = []
    if gap >= 400:
        notes.append("🔥 Large gap – strong flip potential")
    if rap and lowest < rap * 0.85:
        notes.append("📉 Below usual value")
    if profit < 100:
        notes.append("⚠️ Thin profit after tax")
    if notes:
        lines.append("")
        lines.extend(notes)
    return "\n".join(lines)

def attempt_purchase(asset_id: int, locked_max: int) -> Tuple[bool, str, int]:
    if not ROBLOSECURITY:
        return False, "No ROBLOSECURITY cookie loaded.", 0

    refresh_csrf()
    resellers = get_resellers_full(asset_id)
    if not resellers:
        return False, "Could not fetch current resellers.", 0

    lowest = resellers[0]
    price = int(lowest.get("price", 0))
    user_asset_id = lowest.get("userAssetId") or lowest.get("userAssetID")
    seller_id = None
    if isinstance(lowest.get("seller"), dict):
        seller_id = lowest["seller"].get("id")
    else:
        seller_id = lowest.get("sellerId")

    if price > locked_max:
        return False, f"Price rose. Locked `{locked_max:,}` → now `{price:,}`.", 0

    spent = get_spent_today()
    if spent + price > DAILY_ROBUX_CAP:
        return False, (
            f"Daily cap hit. Spent `{spent:,}` / `{DAILY_ROBUX_CAP:,}`. Buy is `{price:,}`."
        ), 0

    if not user_asset_id:
        return False, f"Lowest `{price:,}` but missing userAssetId.", 0

    payload = {
        "expectedCurrency": 1,
        "expectedPrice": price,
        "expectedSellerId": seller_id,
        "userAssetId": user_asset_id,
    }
    headers = {"X-CSRF-TOKEN": csrf_token or "", "Content-Type": "application/json"}

    try:
        url = f"https://economy.roblox.com/v1/purchases/products/{asset_id}"
        r = session.post(url, json=payload, headers=headers, timeout=15)
        if r.status_code == 403:
            refresh_csrf()
            headers["X-CSRF-TOKEN"] = csrf_token or ""
            r = session.post(url, json=payload, headers=headers, timeout=15)

        if r.status_code in (200, 201):
            add_spent(price)
            return True, f"Purchase accepted at `{price:,}` R$.", price
        try:
            err = r.json()
            msg = err.get("message") or err.get("errors", [{}])[0].get("message") or str(err)
        except Exception:
            msg = r.text[:200]
        return False, f"Purchase failed (HTTP {r.status_code}): {msg}", 0
    except Exception as e:
        return False, f"Purchase error: {e}", 0

# ---------- Buy button ----------

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
        color = 0x57F287 if success else 0xED4245
        title = "✅ Purchase Attempted" if success else "❌ Purchase Refused / Failed"
        embed = discord.Embed(
            title=title,
            description=f"**{self.item_name}**\n{message}",
            color=color,
        )
        if success:
            button.disabled = True
            try:
                await interaction.message.edit(view=self)
            except Exception:
                pass
        await interaction.followup.send(embed=embed, ephemeral=True)

# ---------- Send helpers ----------

async def send_to_channel_or_webhook(
    content: str = None,
    embed: discord.Embed = None,
    view: discord.ui.View = None,
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
        title=f"🚨 DEAL SPOTTED",
        description=(
            f"{mention_user()} spotted a deal on **{info['name']}**\n\n"
            f"{body}\n\n[Open on Roblox]({info['url']})"
        ),
        color=0xFF0000,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text=f"Locked buy price: {lowest:,} R$")
    view = BuyView(info["asset_id"], lowest, info["name"])
    await send_to_channel_or_webhook(
        content="@everyone **DEAL FOUND!**",
        embed=embed,
        view=view,
    )

async def try_auto_buy(info: dict, max_price: int):
    if AUTO_BUY_PAUSED:
        return

    asset_id = info["asset_id"]
    name = info["name"]
    locked = info["lowest"]

    success, message, _ = await asyncio.to_thread(attempt_purchase, asset_id, locked)

    if success:
        embed = discord.Embed(
            title="🛒 AUTO-BUY FIRED",
            description=(
                f"{mention_user()}'s auto-buy hit **{name}**\n"
                f"{message}\n"
                f"Max `{max_price:,}` | Today `{get_spent_today():,}` / `{DAILY_ROBUX_CAP:,}`\n\n"
                f"[Open]({info['url']})"
            ),
            color=0x57F287,
            timestamp=datetime.now(timezone.utc),
        )
        await send_to_channel_or_webhook(content="@everyone **AUTO-BUY EXECUTED**", embed=embed)
        print(f"🛒 AUTO-BUY OK: {name} @ {locked:,}")
    else:
        body = build_profit_text(locked, info.get("second"), info.get("rap"), max_price)
        embed = discord.Embed(
            title="❌ AUTO-BUY FAILED",
            description=(
                f"{mention_user()} — auto-buy failed on **{name}**\n"
                f"{message}\n\n{body}\n\n[Open]({info['url']})"
            ),
            color=0xED4245,
            timestamp=datetime.now(timezone.utc),
        )
        view = BuyView(asset_id, locked, name)
        await send_to_channel_or_webhook(
            content="@everyone **AUTO-BUY FAILED – manual Buy available**",
            embed=embed,
            view=view,
        )
        print(f"🛒 AUTO-BUY FAIL: {name} — {message}")

# ---------- Background tasks ----------

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
                f"{mention_user()} is still watching **{len(ITEMS)}** limiteds\n"
                f"**Uptime:** {h}h {m}m\n"
                f"**Auto-buy:** {'PAUSED' if AUTO_BUY_PAUSED else 'ON'}\n"
                f"**Today spent:** `{get_spent_today():,}` / `{DAILY_ROBUX_CAP:,}`"
            ),
            color=0x57F287,
        )
        await send_to_channel_or_webhook(embed=embed)
        last_uptime_sent = now

    print(f"--- Check @ {datetime.now().strftime('%H:%M:%S')} ---")
    for asset_id, max_p in list(ITEMS.items()):
        if asset_id in PURCHASE_TARGETS:
            await asyncio.sleep(0.2)
            continue

        info = await asyncio.to_thread(get_item_info, asset_id)
        if not info:
            print(f"  • {asset_id}: no data")
            await asyncio.sleep(REQUEST_DELAY)
            continue

        lowest = info["lowest"]
        if lowest <= max_p:
            last = last_alerted.get(asset_id, 0)
            if now - last >= ALERT_COOLDOWN:
                await send_alert(info, max_p)
                last_alerted[asset_id] = now
                print(f"🚨 {info['name']} @ {lowest:,}")
        else:
            print(f"  • {info['name']}: {lowest:,} (max {max_p:,})")
        await asyncio.sleep(REQUEST_DELAY)

@tasks.loop(seconds=AUTO_BUY_INTERVAL)
async def auto_buy_monitor():
    if AUTO_BUY_PAUSED or not PURCHASE_TARGETS:
        return

    now = time.time()
    for asset_id, max_p in list(PURCHASE_TARGETS.items()):
        last = last_alerted.get(asset_id, 0)
        if now - last < ALERT_COOLDOWN:
            continue

        info = await asyncio.to_thread(get_item_info, asset_id)
        if not info:
            await asyncio.sleep(0.3)
            continue

        if info["lowest"] <= max_p:
            last_alerted[asset_id] = now
            await try_auto_buy(info, max_p)
            print(f"🛒 FAST hit: {info['name']} @ {info['lowest']:,}")

        await asyncio.sleep(0.35)

@tasks.loop(seconds=PRICE_REPORT_INTERVAL)
async def price_report_task():
    lines = []
    for asset_id, max_p in list(ITEMS.items()):
        info = await asyncio.to_thread(get_item_info, asset_id)
        if not info:
            lines.append(f"**ID {asset_id}**: No data")
            await asyncio.sleep(REQUEST_DELAY)
            continue
        lowest = info["lowest"]
        second = info.get("second")
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

# ---------- Events ----------

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands")
    except Exception as e:
        print("Sync error:", e)

    if ROBLOSECURITY:
        await asyncio.to_thread(refresh_csrf)
        await asyncio.to_thread(fetch_authenticated_user)
        print(f"Roblox user: {ROBLOX_USERNAME} ({ROBLOX_USER_ID})")
    else:
        print("⚠️ No ROBLOSECURITY")

    if not price_monitor.is_running():
        price_monitor.start()
    if not price_report_task.is_running():
        price_report_task.start()
    if not auto_buy_monitor.is_running():
        auto_buy_monitor.start()

    await send_to_channel_or_webhook(
        content=(
            f"👀 {mention_user()} is now **live** — watching **{len(ITEMS)}** limiteds\n"
            f"Auto-buy loop: `{AUTO_BUY_INTERVAL}s` | Daily cap: `{DAILY_ROBUX_CAP:,}` R$"
        )
    )

# ---------- Slash commands ----------

@bot.tree.command(name="monitorall", description="Force full price report now")
async def monitorall(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        await interaction.followup.send(f"🔍 {mention_user()} requested a full check…")
        await price_report_task()
    except Exception as e:
        await interaction.followup.send(f"Error: `{e}`", ephemeral=True)

@bot.tree.command(name="targetitem", description="Watch item – alert + Buy button only")
@app_commands.describe(asset_id="Asset ID", max_price="Max price")
async def targetitem(interaction: discord.Interaction, asset_id: int, max_price: int):
    await interaction.response.defer()
    try:
        ITEMS[asset_id] = max_price
        if asset_id in PURCHASE_TARGETS:
            del PURCHASE_TARGETS[asset_id]
            save_purchase_targets()
        save_items()
        await interaction.followup.send(
            f"👀 {mention_user()} is now watching **{asset_id}** (max `{max_price:,}`)\n"
            f"Alert + Buy button only — no auto-buy."
        )
    except Exception as e:
        await interaction.followup.send(f"Error: `{e}`", ephemeral=True)

@bot.tree.command(name="targetpurchase", description="Watch + AUTO-BUY when price ≤ max")
@app_commands.describe(asset_id="Asset ID", max_price="Max price for auto-buy")
async def targetpurchase(interaction: discord.Interaction, asset_id: int, max_price: int):
    await interaction.response.defer()
    try:
        ITEMS[asset_id] = max_price
        PURCHASE_TARGETS[asset_id] = max_price
        save_items()
        save_purchase_targets()
        await interaction.followup.send(
            f"🛒 {mention_user()} armed **auto-buy** on **{asset_id}** ≤ `{max_price:,}` R$\n"
            f"Fast loop every `{AUTO_BUY_INTERVAL}s` | Cap `{DAILY_ROBUX_CAP:,}`/day"
        )
    except Exception as e:
        await interaction.followup.send(f"Error: `{e}`", ephemeral=True)

@bot.tree.command(name="pause", description="Pause all auto-buy")
async def pause_cmd(interaction: discord.Interaction):
    global AUTO_BUY_PAUSED
    await interaction.response.defer()
    AUTO_BUY_PAUSED = True
    await interaction.followup.send(f"⏸️ {mention_user()} paused auto-buy. Alerts still run.")

@bot.tree.command(name="resume", description="Resume auto-buy")
async def resume_cmd(interaction: discord.Interaction):
    global AUTO_BUY_PAUSED
    await interaction.response.defer()
    AUTO_BUY_PAUSED = False
    await interaction.followup.send(f"▶️ {mention_user()} resumed auto-buy.")

@bot.tree.command(name="showlist", description="Show monitored items")
async def showlist(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        if not ITEMS:
            await interaction.followup.send("No items.", ephemeral=True)
            return
        lines = [
            f"**{aid}** → max `{price:,}`" + (" 🛒 AUTO" if aid in PURCHASE_TARGETS else "")
            for aid, price in ITEMS.items()
        ]
        embed = discord.Embed(
            title=f"👀 {ROBLOX_USERNAME}'s watch list",
            description="\n".join(lines),
            color=0x5865F2,
        )
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"Error: `{e}`", ephemeral=True)

@bot.tree.command(name="showprofitables", description="Gaps + possible profit")
async def showprofitables(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
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
            if second and second > lowest:
                suggested, source = second, "2nd"
            elif rap and rap > lowest:
                suggested, source = rap, "RAP"
            else:
                suggested = None
            block = ""
            if suggested:
                net = net_after_tax(suggested)
                profit = net - lowest
                block = (
                    f"\nBuy `{lowest:,}` → ~`{suggested:,}` ({source}) → "
                    f"after tax `{net:,}` (**{'+' if profit >= 0 else ''}{profit:,}**)"
                )
            auto = " 🛒" if asset_id in PURCHASE_TARGETS else ""
            lines.append(
                f"**{info['name']}**{auto} (`{asset_id}`)\n"
                f"1st `{lowest:,}` | 2nd `{second or '—'}` | Max `{max_p:,}` | "
                f"{'UNDER' if under else 'Gap'}{block}\n"
            )
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
    except Exception as e:
        await interaction.followup.send(f"Error: `{e}`", ephemeral=True)

@bot.tree.command(name="remove", description="Stop monitoring an item")
@app_commands.describe(asset_id="Asset ID")
async def remove_item(interaction: discord.Interaction, asset_id: int):
    await interaction.response.defer()
    try:
        removed = asset_id in ITEMS
        ITEMS.pop(asset_id, None)
        PURCHASE_TARGETS.pop(asset_id, None)
        if removed:
            save_items()
            save_purchase_targets()
            await interaction.followup.send(
                f"🗑️ {mention_user()} stopped watching **{asset_id}**."
            )
        else:
            await interaction.followup.send("Not in list.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"Error: `{e}`", ephemeral=True)

@bot.tree.command(name="status", description="Bot status + live user")
async def status(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        uptime = int(time.time() - start_time)
        h, m = uptime // 3600, (uptime % 3600) // 60
        embed = discord.Embed(
            title="🟢 Live status",
            description=(
                f"**Roblox:** {mention_user()}\n"
                f"**Uptime:** {h}h {m}m\n"
                f"**Watching:** {len(ITEMS)} items\n"
                f"**Auto-buy targets:** {len(PURCHASE_TARGETS)}\n"
                f"**Auto-buy:** {'⏸️ PAUSED' if AUTO_BUY_PAUSED else '▶️ ON'}\n"
                f"**Today spent:** `{get_spent_today():,}` / `{DAILY_ROBUX_CAP:,}` R$\n"
                f"**Cookie:** {'Yes' if ROBLOSECURITY else 'No'}"
            ),
            color=0x57F287,
        )
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"Error: `{e}`", ephemeral=True)

# ---------- Run ----------

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("❌ DISCORD_TOKEN missing")
    else:
        bot.run(DISCORD_TOKEN)

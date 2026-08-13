"""
Roblox Limited Notifier – Discord Bot (fixed interactions)
- defer() on all commands (fixes 10062 Unknown interaction)
- asyncio.to_thread for Roblox API (non-blocking)
"""

import os
import time
import math
import json
import asyncio
import requests
from pathlib import Path
from datetime import datetime, timezone
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
}

CHECK_INTERVAL = 50
ALERT_COOLDOWN = 300
PRICE_REPORT_INTERVAL = 15 * 60
UPTIME_INTERVAL = 45 * 60
SELLER_KEEP = 0.70
REQUEST_DELAY = 1.4
# ====================================================

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
ROBLOSECURITY = os.getenv("ROBLOSECURITY", "").strip()
ALERT_CHANNEL_ID = os.getenv("ALERT_CHANNEL_ID", "").strip()

ITEMS_FILE = Path("items.json")

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
PURCHASE_TARGETS: Dict[int, int] = {}

# ---------- Persistence ----------

def load_items() -> Dict[int, int]:
    if ITEMS_FILE.exists():
        try:
            data = json.loads(ITEMS_FILE.read_text())
            return {int(k): int(v) for k, v in data.items()}
        except Exception as e:
            print("Failed to load items.json:", e)
    return DEFAULT_ITEMS.copy()

def save_items():
    try:
        ITEMS_FILE.write_text(json.dumps({str(k): v for k, v in ITEMS.items()}, indent=2))
    except Exception as e:
        print("Failed to save items.json:", e)

ITEMS: Dict[int, int] = load_items()

# ---------- Roblox helpers (sync – call via asyncio.to_thread) ----------

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
        print("CSRF refresh failed:", e)
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
            print(f"Rate limited on resellers {asset_id}")
            time.sleep(3)
            return []
        if r.status_code != 200:
            return []
        return r.json().get("data", []) or []
    except Exception as e:
        print(f"Resellers error {asset_id}: {e}")
        return []

def get_catalog_info(asset_id: int) -> Optional[dict]:
    try:
        r = session.get(
            f"https://catalog.roblox.com/v1/catalog/items/{asset_id}/details?itemType=Asset",
            timeout=12,
        )
        if r.status_code == 429:
            print(f"Rate limited on catalog {asset_id}")
            time.sleep(3)
            return None
        if r.status_code != 200:
            return None
        data = r.json()
        lowest = data.get("lowestResalePrice") or data.get("lowestPrice")
        name = data.get("name") or f"Asset {asset_id}"
        return {
            "name": name,
            "lowest": int(lowest) if lowest is not None else None,
            "asset_id": asset_id,
            "url": f"https://www.roblox.com/catalog/{asset_id}/",
            "collectible_item_id": data.get("collectibleItemId"),
            "product_id": data.get("productId"),
        }
    except Exception as e:
        print(f"Catalog error {asset_id}: {e}")
        return None

def get_economy_name(asset_id: int) -> Optional[str]:
    try:
        r = session.get(f"https://economy.roblox.com/v2/assets/{asset_id}/details", timeout=10)
        if r.status_code == 200:
            data = r.json()
            return data.get("Name") or data.get("name")
    except Exception:
        pass
    return None

def get_rap(asset_id: int) -> Optional[int]:
    try:
        r = session.get(f"https://economy.roblox.com/v1/assets/{asset_id}/resale-data", timeout=10)
        if r.status_code == 200:
            rap = r.json().get("recentAveragePrice")
            if rap:
                return int(rap)
    except Exception:
        pass
    return None

def get_item_info(asset_id: int) -> Optional[dict]:
    name = None
    lowest = None
    second = None
    collectible_id = None
    url = f"https://www.roblox.com/catalog/{asset_id}/"

    cat = get_catalog_info(asset_id)
    if cat:
        name = cat.get("name")
        lowest = cat.get("lowest")
        collectible_id = cat.get("collectible_item_id")

    resellers = get_resellers_full(asset_id)
    if resellers:
        try:
            p1 = resellers[0].get("price")
            if p1 is not None:
                lowest = int(p1)
            if len(resellers) > 1 and resellers[1].get("price") is not None:
                second = int(resellers[1]["price"])
        except Exception:
            pass

    if lowest is None:
        return None

    if not name:
        name = get_economy_name(asset_id) or f"ID {asset_id}"

    rap = get_rap(asset_id)

    return {
        "name": name,
        "lowest": int(lowest),
        "second": second,
        "rap": rap,
        "asset_id": asset_id,
        "url": url,
        "collectible_item_id": collectible_id,
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

def attempt_purchase(asset_id: int, locked_max: int) -> Tuple[bool, str]:
    if not ROBLOSECURITY:
        return False, "No ROBLOSECURITY cookie loaded."

    refresh_csrf()
    resellers = get_resellers_full(asset_id)
    if not resellers:
        return False, "Could not fetch current resellers."

    lowest = resellers[0]
    price = int(lowest.get("price", 0))
    user_asset_id = lowest.get("userAssetId") or lowest.get("userAssetID")
    seller_id = None
    if isinstance(lowest.get("seller"), dict):
        seller_id = lowest["seller"].get("id")
    else:
        seller_id = lowest.get("sellerId")

    if price > locked_max:
        return False, (
            f"Price increased. Locked at `{locked_max:,}` but current lowest is `{price:,}`. "
            "Purchase refused."
        )

    if not user_asset_id:
        return False, (
            f"Current lowest is `{price:,}` (≤ locked `{locked_max:,}`) "
            "but missing userAssetId. Cannot purchase safely."
        )

    payload = {
        "expectedCurrency": 1,
        "expectedPrice": price,
        "expectedSellerId": seller_id,
        "userAssetId": user_asset_id,
    }
    headers = {
        "X-CSRF-TOKEN": csrf_token or "",
        "Content-Type": "application/json",
    }

    try:
        url = f"https://economy.roblox.com/v1/purchases/products/{asset_id}"
        r = session.post(url, json=payload, headers=headers, timeout=15)
        if r.status_code == 403:
            refresh_csrf()
            headers["X-CSRF-TOKEN"] = csrf_token or ""
            r = session.post(url, json=payload, headers=headers, timeout=15)

        if r.status_code in (200, 201):
            return True, f"Purchase request accepted at `{price:,}` R$."
        try:
            err = r.json()
            msg = err.get("message") or err.get("errors", [{}])[0].get("message") or str(err)
        except Exception:
            msg = r.text[:200]
        return False, f"Purchase failed (HTTP {r.status_code}): {msg}"
    except Exception as e:
        return False, f"Purchase request error: {e}"

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
        success, message = await asyncio.to_thread(
            attempt_purchase, self.asset_id, self.locked_price
        )

        if success:
            embed = discord.Embed(
                title="✅ Purchase Attempted",
                description=f"**{self.item_name}**\n{message}\n\nCheck inventory / transactions.",
                color=0x57F287,
            )
            button.disabled = True
            try:
                await interaction.message.edit(view=self)
            except Exception:
                pass
        else:
            embed = discord.Embed(
                title="❌ Purchase Refused / Failed",
                description=f"**{self.item_name}**\n{message}",
                color=0xED4245,
            )
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
        await asyncio.to_thread(
            lambda: requests.post(WEBHOOK_URL, json=payload, timeout=10)
        )
    except Exception as e:
        print("Send failed:", e)

async def send_alert(info: dict, max_price: int):
    lowest = info["lowest"]
    body = build_profit_text(lowest, info.get("second"), info.get("rap"), max_price)
    embed = discord.Embed(
        title=f"🚨 PRICE ALERT: {info['name']}",
        description=body + f"\n\n[Open on Roblox]({info['url']})",
        color=0xFF0000,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text=f"Locked buy price: {lowest:,} R$ • Will refuse if price rises")
    view = BuyView(asset_id=info["asset_id"], locked_price=lowest, item_name=info["name"])
    await send_to_channel_or_webhook(
        content="@everyone **DEAL FOUND!**",
        embed=embed,
        view=view,
    )

# ---------- Background tasks ----------

@tasks.loop(seconds=CHECK_INTERVAL)
async def price_monitor():
    global last_uptime_sent
    now = time.time()

    if now - last_uptime_sent >= UPTIME_INTERVAL:
        uptime = int(now - start_time)
        h, m = uptime // 3600, (uptime % 3600) // 60
        embed = discord.Embed(
            title="🟢 Monitor Uptime",
            description=f"Still running.\n**Uptime:** {h}h {m}m\n**Items:** {len(ITEMS)}",
            color=0x57F287,
        )
        await send_to_channel_or_webhook(embed=embed)
        last_uptime_sent = now

    print(f"--- Check @ {datetime.now().strftime('%H:%M:%S')} ---")
    for asset_id, max_p in list(ITEMS.items()):
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
                print(f"  ⏳ {info['name']} under target (cooldown)")
        else:
            print(f"  • {info['name']}: {lowest:,} (max {max_p:,})")
        await asyncio.sleep(REQUEST_DELAY)

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
        extra = f" → 2nd `{second:,}`" if second else ""
        profit_note = ""
        if lowest <= max_p and second and second > lowest:
            profit = net_after_tax(second) - lowest
            profit_note = f" | +{profit:,} after tax"
        lines.append(f"**{info['name']}**: `{lowest:,}`{extra}{profit_note} {status}")
        await asyncio.sleep(REQUEST_DELAY)

    embed = discord.Embed(
        title="📈 Limited Prices (every 15 min)",
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
        print(f"Synced {len(synced)} slash commands")
    except Exception as e:
        print("Command sync error:", e)

    if ROBLOSECURITY:
        await asyncio.to_thread(refresh_csrf)
        print("Roblox cookie loaded")
    else:
        print("⚠️ No ROBLOSECURITY – many prices + purchases will fail")

    if not ALERT_CHANNEL_ID:
        print("⚠️ ALERT_CHANNEL_ID not set – Buy buttons need a channel")

    if not price_monitor.is_running():
        price_monitor.start()
    if not price_report_task.is_running():
        price_report_task.start()

    await send_to_channel_or_webhook(
        content="**monitor started** ✅ Bot online (fixed interactions)"
    )

# ---------- Slash commands (all defer first) ----------

@bot.tree.command(name="monitorall", description="Force an immediate full price check + report")
async def monitorall(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        await interaction.followup.send("🔍 Running full check now…")
        await price_report_task()
    except Exception as e:
        print(f"/monitorall error: {e}")
        await interaction.followup.send(f"Error: `{e}`", ephemeral=True)

@bot.tree.command(name="targetitem", description="Add or update an item to monitor")
@app_commands.describe(asset_id="Roblox asset ID", max_price="Max price you are willing to pay")
async def targetitem(interaction: discord.Interaction, asset_id: int, max_price: int):
    await interaction.response.defer()
    try:
        ITEMS[asset_id] = max_price
        save_items()
        await interaction.followup.send(
            f"✅ Now monitoring **{asset_id}** with max `{max_price:,}` R$\n"
            f"Alert fires when lowest seller is ≤ `{max_price:,}`."
        )
    except Exception as e:
        print(f"/targetitem error: {e}")
        await interaction.followup.send(f"Error: `{e}`", ephemeral=True)

@bot.tree.command(name="showlist", description="Show all currently monitored items")
async def showlist(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        if not ITEMS:
            await interaction.followup.send("No items are being monitored.", ephemeral=True)
            return
        lines = []
        for aid, price in ITEMS.items():
            tag = " 🛒" if aid in PURCHASE_TARGETS else ""
            lines.append(f"**{aid}** → max `{price:,}` R${tag}")
        embed = discord.Embed(
            title="Currently Monitoring",
            description="\n".join(lines),
            color=0x5865F2,
        )
        await interaction.followup.send(embed=embed)
    except Exception as e:
        print(f"/showlist error: {e}")
        await interaction.followup.send(f"Error: `{e}`", ephemeral=True)

@bot.tree.command(name="showprofitables", description="Show 1st→2nd seller gaps + possible profit")
async def showprofitables(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        lines = []
        for asset_id, max_p in list(ITEMS.items()):
            info = await asyncio.to_thread(get_item_info, asset_id)
            await asyncio.sleep(REQUEST_DELAY)
            if not info:
                continue

            lowest = info["lowest"]
            second = info.get("second")
            rap = info.get("rap")
            under = lowest <= max_p
            gap = (second - lowest) if second else 0

            if not under and gap < 150:
                continue

            if second and second > lowest:
                suggested, source = second, "2nd seller"
            elif rap and rap > lowest:
                suggested, source = rap, "RAP"
            else:
                suggested, source = None, None

            profit_block = ""
            if suggested:
                net = net_after_tax(suggested)
                profit = net - lowest
                profit_block = (
                    f"\nBuy `{lowest:,}` → sell ~`{suggested:,}` ({source})"
                    f"\nAfter 30% tax: `{net:,}` → **possible `{'+' if profit >= 0 else ''}{profit:,}` R$**"
                )
                if gap >= 300:
                    profit_block += "\n🔥 **Large 1st→2nd gap**"

            status = "✅ UNDER TARGET" if under else "Gap opportunity"
            lines.append(
                f"**{info['name']}** (`{asset_id}`)\n"
                f"1st `{lowest:,}` | 2nd `{second if second else '—'}` | Max `{max_p:,}` | {status}"
                f"{profit_block}\n"
            )

        if not lines:
            await interaction.followup.send("No under-target items or strong gaps right now.")
        else:
            embed = discord.Embed(
                title="💰 Profitable / Gap Opportunities",
                description="\n".join(lines),
                color=0x57F287,
                timestamp=datetime.now(timezone.utc),
            )
            embed.set_footer(text="Profit uses 30% tax (you keep 70%)")
            await interaction.followup.send(embed=embed)
    except Exception as e:
        print(f"/showprofitables error: {e}")
        await interaction.followup.send(f"Error: `{e}`", ephemeral=True)

@bot.tree.command(name="targetpurchase", description="Mark an item as a strong buy target")
@app_commands.describe(asset_id="Roblox asset ID", max_price="Max price you are willing to pay")
async def targetpurchase(interaction: discord.Interaction, asset_id: int, max_price: int):
    await interaction.response.defer()
    try:
        ITEMS[asset_id] = max_price
        PURCHASE_TARGETS[asset_id] = max_price
        save_items()
        await interaction.followup.send(
            f"🛒 **Purchase target set**\nAsset `{asset_id}` → max `{max_price:,}` R$"
        )
    except Exception as e:
        print(f"/targetpurchase error: {e}")
        await interaction.followup.send(f"Error: `{e}`", ephemeral=True)

@bot.tree.command(name="remove", description="Stop monitoring an item")
@app_commands.describe(asset_id="Asset ID to remove")
async def remove_item(interaction: discord.Interaction, asset_id: int):
    await interaction.response.defer()
    try:
        removed = asset_id in ITEMS
        if asset_id in ITEMS:
            del ITEMS[asset_id]
        if asset_id in PURCHASE_TARGETS:
            del PURCHASE_TARGETS[asset_id]
        if removed:
            save_items()
            await interaction.followup.send(f"🗑️ Removed **{asset_id}**.")
        else:
            await interaction.followup.send("That asset ID was not being monitored.", ephemeral=True)
    except Exception as e:
        print(f"/remove error: {e}")
        await interaction.followup.send(f"Error: `{e}`", ephemeral=True)

@bot.tree.command(name="status", description="Show bot status and uptime")
async def status(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        uptime = int(time.time() - start_time)
        h, m = uptime // 3600, (uptime % 3600) // 60
        embed = discord.Embed(
            title="🟢 Bot Status",
            description=(
                f"**Uptime:** {h}h {m}m\n"
                f"**Items monitored:** {len(ITEMS)}\n"
                f"**Purchase targets:** {len(PURCHASE_TARGETS)}\n"
                f"**Cookie loaded:** {'Yes' if ROBLOSECURITY else 'No'}\n"
                f"**Alert channel:** {'Set' if ALERT_CHANNEL_ID else 'Not set'}"
            ),
            color=0x57F287,
        )
        await interaction.followup.send(embed=embed)
    except Exception as e:
        print(f"/status error: {e}")
        await interaction.followup.send(f"Error: `{e}`", ephemeral=True)

# ---------- Run ----------

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("❌ DISCORD_TOKEN is missing!")
    else:
        bot.run(DISCORD_TOKEN)

"""
Roblox Limited Notifier – Discord Bot
Green Buy button on alerts • Price-lock safety • Slash commands
"""

import os
import time
import math
import asyncio
import json
import requests
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands, tasks

# ====================== CONFIG ======================
WEBHOOK_URL = "https://discord.com/api/webhooks/1537206323429769247/DDanKqiYTeAUDMQjOt0UvDCV5UD4lEjtnrCGs9OtXJtqsuZn8YkMObq6xug12KK0J7pl"

ITEMS: Dict[int, int] = {
    9255011:     4300,
    10159617728: 6000,
    1082932:     2500,
    14463095:    5000,
    1609390589:  3000,
    16477149823: 5675,
    1609402609:  1100,
    17408283:    800,
}

PURCHASE_TARGETS: Dict[int, int] = {}

CHECK_INTERVAL = 45
ALERT_COOLDOWN = 300
PRICE_REPORT_INTERVAL = 15 * 60
UPTIME_INTERVAL = 45 * 60
SELLER_KEEP = 0.70
# ====================================================

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
ROBLOSECURITY = os.getenv("ROBLOSECURITY", "").strip()
ALERT_CHANNEL_ID = os.getenv("ALERT_CHANNEL_ID", "").strip()

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

# ---------- Helpers ----------

def net_after_tax(price: int) -> int:
    return math.floor(price * SELLER_KEEP)

def refresh_csrf() -> Optional[str]:
    global csrf_token
    try:
        r = session.post("https://auth.roblox.com/v2/logout", timeout=10)
        token = r.headers.get("x-csrf-token")
        if token:
            csrf_token = token
            session.headers["X-CSRF-TOKEN"] = token
            return token
    except Exception as e:
        print("CSRF refresh failed:", e)
    return None

def get_catalog_info(asset_id: int) -> Optional[dict]:
    try:
        r = session.get(
            f"https://catalog.roblox.com/v1/catalog/items/{asset_id}/details?itemType=Asset",
            timeout=12
        )
        r.raise_for_status()
        data = r.json()
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
        print(f"Catalog error {asset_id}: {e}")
        return None

def get_resellers_full(asset_id: int) -> list:
    """Return list of reseller dicts (price, userAssetId, seller, etc.)."""
    if not ROBLOSECURITY:
        return []
    try:
        if not csrf_token:
            refresh_csrf()
        r = session.get(
            f"https://economy.roblox.com/v1/assets/{asset_id}/resellers?limit=10",
            timeout=12
        )
        if r.status_code == 403:
            refresh_csrf()
            r = session.get(
                f"https://economy.roblox.com/v1/assets/{asset_id}/resellers?limit=10",
                timeout=12
            )
        if r.status_code != 200:
            return []
        return r.json().get("data", [])
    except Exception as e:
        print(f"Resellers error {asset_id}: {e}")
        return []

def get_resellers(asset_id: int) -> Tuple[Optional[int], Optional[int]]:
    data = get_resellers_full(asset_id)
    if not data:
        return None, None
    p1 = data[0].get("price")
    p2 = data[1].get("price") if len(data) > 1 else None
    return (int(p1) if p1 else None, int(p2) if p2 else None)

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
    info = get_catalog_info(asset_id)
    if not info:
        return None
    p1, p2 = get_resellers(asset_id)
    if p1 is not None:
        info["lowest"] = p1
    info["second"] = p2
    info["rap"] = get_rap(asset_id)
    return info

def build_profit_text(lowest: int, second: Optional[int], rap: Optional[int], max_price: int) -> str:
    if second and second > lowest:
        suggested = second
        source = "2nd seller"
    elif rap and rap > lowest:
        suggested = rap
        source = "RAP"
    else:
        suggested = max(int(max_price * 0.92), lowest + 50)
        source = "target"

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
    """
    Re-check lowest seller. Only buy if price <= locked_max.
    Returns (success, message).
    """
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
        return False, f"Price increased. Was locked at `{locked_max:,}` but current lowest is `{price:,}`. Purchase refused."

    if not user_asset_id:
        return False, f"Current lowest is `{price:,}` (≤ locked `{locked_max:,}`) but missing userAssetId. Cannot purchase safely."

    # Attempt purchase via economy purchase endpoint
    # This is the classic limited resale purchase path
    payload = {
        "expectedCurrency": 1,          # 1 = Robux
        "expectedPrice": price,
        "expectedSellerId": seller_id,
        "userAssetId": user_asset_id,
    }

    headers = {
        "X-CSRF-TOKEN": csrf_token or "",
        "Content-Type": "application/json",
    }

    try:
        # Primary endpoint used by many limited purchase tools
        url = f"https://economy.roblox.com/v1/purchases/products/{asset_id}"
        r = session.post(url, json=payload, headers=headers, timeout=15)

        if r.status_code == 403:
            refresh_csrf()
            headers["X-CSRF-TOKEN"] = csrf_token or ""
            r = session.post(url, json=payload, headers=headers, timeout=15)

        if r.status_code in (200, 201):
            return True, f"Purchase request accepted at `{price:,}` R$."
        else:
            # Try to extract error message
            try:
                err = r.json()
                msg = err.get("message") or err.get("errors", [{}])[0].get("message") or str(err)
            except Exception:
                msg = r.text[:200]
            return False, f"Purchase failed (HTTP {r.status_code}): {msg}"
    except Exception as e:
        return False, f"Purchase request error: {e}"

# ---------- Buy Button View ----------

class BuyView(discord.ui.View):
    def __init__(self, asset_id: int, locked_price: int, item_name: str):
        super().__init__(timeout=3600)  # button works for 1 hour
        self.asset_id = asset_id
        self.locked_price = locked_price
        self.item_name = item_name

    @discord.ui.button(label="Buy", style=discord.ButtonStyle.success, emoji="🛒")
    async def buy_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        success, message = attempt_purchase(self.asset_id, self.locked_price)

        if success:
            embed = discord.Embed(
                title="✅ Purchase Attempted",
                description=f"**{self.item_name}**\n{message}\n\nCheck your Roblox inventory / transaction history.",
                color=0x57F287
            )
            # Disable the button after use
            button.disabled = True
            await interaction.message.edit(view=self)
        else:
            embed = discord.Embed(
                title="❌ Purchase Refused / Failed",
                description=f"**{self.item_name}**\n{message}",
                color=0xED4245
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

# ---------- Send helpers ----------

async def send_to_channel_or_webhook(content: str = None, embed: discord.Embed = None, view: discord.ui.View = None):
    if ALERT_CHANNEL_ID:
        channel = bot.get_channel(int(ALERT_CHANNEL_ID))
        if channel:
            await channel.send(content=content, embed=embed, view=view)
            return

    # Webhook fallback (no buttons possible)
    payload = {}
    if content:
        payload["content"] = content
    if embed:
        payload["embeds"] = [embed.to_dict()]
    try:
        requests.post(WEBHOOK_URL, json=payload, timeout=10)
    except Exception as e:
        print("Send failed:", e)

async def send_alert(info: dict, max_price: int):
    lowest = info["lowest"]
    body = build_profit_text(lowest, info.get("second"), info.get("rap"), max_price)

    embed = discord.Embed(
        title=f"🚨 PRICE ALERT: {info['name']}",
        description=body + f"\n\n[Open on Roblox]({info['url']})",
        color=0xFF0000,
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(text=f"Locked buy price: {lowest:,} R$ • Will refuse if price rises")

    view = BuyView(asset_id=info["asset_id"], locked_price=lowest, item_name=info["name"])

    await send_to_channel_or_webhook(
        content="@everyone **DEAL FOUND!**",
        embed=embed,
        view=view
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
            color=0x57F287
        )
        await send_to_channel_or_webhook(embed=embed)
        last_uptime_sent = now

    print(f"--- Check @ {datetime.now().strftime('%H:%M:%S')} ---")
    for asset_id, max_p in list(ITEMS.items()):
        info = get_item_info(asset_id)
        if not info:
            continue
        lowest = info["lowest"]
        if lowest <= max_p:
            last = last_alerted.get(asset_id, 0)
            if now - last >= ALERT_COOLDOWN:
                await send_alert(info, max_p)
                last_alerted[asset_id] = now
                print(f"🚨 {info['name']} @ {lowest:,}")
        await asyncio.sleep(1.0)

@tasks.loop(seconds=PRICE_REPORT_INTERVAL)
async def price_report_task():
    lines = []
    for asset_id, max_p in list(ITEMS.items()):
        info = get_item_info(asset_id)
        if not info:
            lines.append(f"**ID {asset_id}**: No data")
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
        await asyncio.sleep(0.8)

    embed = discord.Embed(
        title="📈 Limited Prices (every 15 min)",
        description="\n".join(lines) or "No items",
        color=0x5865F2,
        timestamp=datetime.now(timezone.utc)
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
        refresh_csrf()
        print("Roblox cookie loaded")
    else:
        print("⚠️ No ROBLOSECURITY – purchases will fail")

    if not ALERT_CHANNEL_ID:
        print("⚠️ ALERT_CHANNEL_ID not set – buttons may not appear (webhook fallback)")

    if not price_monitor.is_running():
        price_monitor.start()
    if not price_report_task.is_running():
        price_report_task.start()

    await send_to_channel_or_webhook(content="**monitor started** ✅ Bot online with Buy button support")

# ---------- Slash Commands ----------

@bot.tree.command(name="monitorall", description="Force an immediate full price check + report")
async def monitorall(interaction: discord.Interaction):
    await interaction.response.send_message("🔍 Running full check now…")
    await price_report_task()

@bot.tree.command(name="targetitem", description="Add or update an item to monitor")
@app_commands.describe(asset_id="Roblox asset ID", max_price="Max price you are willing to pay")
async def targetitem(interaction: discord.Interaction, asset_id: int, max_price: int):
    ITEMS[asset_id] = max_price
    await interaction.response.send_message(f"✅ Now monitoring **{asset_id}** with max `{max_price:,}` R$")

@bot.tree.command(name="showlist", description="Show all currently monitored items")
async def showlist(interaction: discord.Interaction):
    if not ITEMS:
        await interaction.response.send_message("No items are being monitored.", ephemeral=True)
        return
    lines = []
    for aid, price in ITEMS.items():
        tag = " 🛒" if aid in PURCHASE_TARGETS else ""
        lines.append(f"**{aid}** → max `{price:,}` R${tag}")
    embed = discord.Embed(title="Currently Monitoring", description="\n".join(lines), color=0x5865F2)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="showprofitables", description="Show items currently under target + possible profit")
async def showprofitables(interaction: discord.Interaction):
    await interaction.response.defer()
    lines = []
    for asset_id, max_p in list(ITEMS.items()):
        info = get_item_info(asset_id)
        if not info or info["lowest"] > max_p:
            continue
        body = build_profit_text(info["lowest"], info.get("second"), info.get("rap"), max_p)
        lines.append(f"**{info['name']}** (ID `{asset_id}`)\n{body}\n")
    if not lines:
        await interaction.followup.send("No items are currently under your targets.")
    else:
        embed = discord.Embed(title="💰 Currently Profitable", description="\n".join(lines), color=0x57F287)
        await interaction.followup.send(embed=embed)

@bot.tree.command(name="targetpurchase", description="Mark an item as a strong buy target")
@app_commands.describe(asset_id="Roblox asset ID", max_price="Max price you are willing to pay")
async def targetpurchase(interaction: discord.Interaction, asset_id: int, max_price: int):
    ITEMS[asset_id] = max_price
    PURCHASE_TARGETS[asset_id] = max_price
    await interaction.response.send_message(
        f"🛒 **Purchase target set**\nAsset `{asset_id}` → max `{max_price:,}` R$"
    )

@bot.tree.command(name="remove", description="Stop monitoring an item")
@app_commands.describe(asset_id="Asset ID to remove")
async def remove_item(interaction: discord.Interaction, asset_id: int):
    removed = False
    if asset_id in ITEMS:
        del ITEMS[asset_id]
        removed = True
    if asset_id in PURCHASE_TARGETS:
        del PURCHASE_TARGETS[asset_id]
    if removed:
        await interaction.response.send_message(f"🗑️ Removed **{asset_id}** from monitoring.")
    else:
        await interaction.response.send_message("That asset ID was not being monitored.", ephemeral=True)

@bot.tree.command(name="status", description="Show bot status and uptime")
async def status(interaction: discord.Interaction):
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
        color=0x57F287
    )
    await interaction.response.send_message(embed=embed)

# ---------- Run ----------

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("❌ DISCORD_TOKEN is missing!")
    else:
        bot.run(DISCORD_TOKEN)

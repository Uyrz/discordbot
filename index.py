import subprocess
import sys

# ---------------------------
# Function to auto-install a package
# ---------------------------
def install(package):
    subprocess.check_call([sys.executable, "-m", "pip", "install", package])

# ---------------------------
# List of required packages
# ---------------------------
required_packages = [
    "discord.py",
    "python-dotenv",
    "gspread",
    "oauth2client",
    "flask",
]

for pkg in required_packages:
    try:
        __import__(pkg.replace("-", "_"))
    except ImportError:
        print(f"📦 Installing {pkg} ...")
        install(pkg)


import os
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

import discord
from discord.ext import commands
from discord.ui import Button, View, Modal, TextInput, Select

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from myserver import server_on  # Flask server

# ---------------------------
# Load env
# ---------------------------
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
TIMEZONE = os.getenv("TIMEZONE", "Asia/Bangkok")

# ---------------------------
# Google Sheets setup
# ---------------------------
def gspread_client():
    creds = ServiceAccountCredentials.from_json_keyfile_name(
        GOOGLE_SERVICE_ACCOUNT_FILE,
        [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
    )
    return gspread.authorize(creds)

def append_sheet_row(user, clock_action, timestamp_iso):
    try:
        gc = gspread_client()
        sh = gc.open_by_key(GOOGLE_SHEET_ID)
        ws = sh.sheet1
        ws.append_row([user, clock_action, timestamp_iso])
    except Exception as e:
        print("Error appending to sheet:", e)

# ---------------------------
# Bot init
# ---------------------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# ---------------------------
# Predefined data
# ---------------------------
EMPLOYEES = {
    "Alice": "https://example.com/cards/alice.png",
    "Bob": "https://example.com/cards/bob.png",
    "Charlie": "https://example.com/cards/charlie.png",
    "Davika": "cards/davika.png",  # local file example
}

# ---------------------------
# Arrow Game
# ---------------------------
ARROWS = ["⬅️", "⬆️", "➡️", "⬇️"]

class ArrowGame(View):
    def __init__(self, sequence, timeout=15):
        super().__init__(timeout=timeout)
        self.sequence = sequence
        self.user_inputs = {}
        self.result = {}
        for arrow in ARROWS:
            self.add_item(ArrowButton(arrow, self))

    async def on_timeout(self):
        for user_id, inputs in self.user_inputs.items():
            if len(inputs) < len(self.sequence):
                self.result[user_id] = False

class ArrowButton(Button):
    def __init__(self, label, game_view):
        super().__init__(label=label, style=discord.ButtonStyle.secondary)
        self.game_view = game_view

    async def callback(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        if user_id not in self.game_view.user_inputs:
            self.game_view.user_inputs[user_id] = []
        self.game_view.user_inputs[user_id].append(self.label)
        await interaction.response.defer()
        if len(self.game_view.user_inputs[user_id]) == len(self.game_view.sequence):
            self.game_view.result[user_id] = self.game_view.user_inputs[user_id] == self.game_view.sequence

@bot.command(name="game")
@commands.has_permissions(administrator=True)
async def startgame(ctx):
    sequence = [discord.utils.get([*ARROWS], lambda x: x) for _ in range(4)]
    view = ArrowGame(sequence)
    embed = discord.Embed(
        description="🎯 จงกดปุ่มตามลำดับนี้ให้ถูกต้องภายในเวลา 15 วินาที:\n\n" + " ".join(sequence),
        color=discord.Color.blue()
    )
    embed.set_author(name="Mini Arrow Game")
    await ctx.send(embed=embed, view=view)
    await view.wait()
    if not view.result:
        await ctx.send("⏰ ไม่มีใครกดครบ หมดเวลา!")
        return
    winners = [f"<@{uid}>" for uid, res in view.result.items() if res]
    losers = [f"<@{uid}>" for uid, res in view.result.items() if not res]
    if winners:
        await ctx.send("✅ คนที่ตอบถูกคือ: " + ", ".join(winners))
    if losers:
        await ctx.send("❌ คนที่ตอบผิดคือ: " + ", ".join(losers))

# ---------------------------
# Clock In/Out System
# ---------------------------
class NameModal(Modal, title="ลงชื่อเข้างาน/ออกงาน"):
    user_name = TextInput(label="กรุณากรอกชื่อของคุณ", placeholder="เช่น: สมชาย")

    def __init__(self, parent_view):
        super().__init__()
        self.parent_view = parent_view

    async def on_submit(self, interaction: discord.Interaction):
        self.parent_view.user_name = str(self.user_name)
        await interaction.response.edit_message(
            content=f"✅ คุณกรอกชื่อเป็น `{self.parent_view.user_name}`\nเลือก **ลงชื่อเข้างาน / ลงชื่อออกงาน**",
            view=self.parent_view.clock_choice_view()
        )

class ClockView(View):
    def __init__(self, ctx):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.user_name = None
        self.clock_type = None
        self.selected_employee = None

    @discord.ui.button(label="กรอกชื่อผู้ใช้", style=discord.ButtonStyle.primary)
    async def enter_name(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(NameModal(self))

    def clock_choice_view(self):
        view = View(timeout=120)

        async def clockin_callback(inter: discord.Interaction):
            self.clock_type = "ลงชื่อเข้างาน"
            await inter.response.edit_message(
                content=f"📌 คุณเลือก `{self.clock_type}`\nต่อไปเลือกพนักงาน",
                view=self.employee_select_view()
            )

        async def clockout_callback(inter: discord.Interaction):
            self.clock_type = "ลงชื่อออกงาน"
            await inter.response.edit_message(
                content=f"📌 คุณเลือก `{self.clock_type}`\nต่อไปเลือกพนักงาน",
                view=self.employee_select_view()
            )

        btn_in = Button(label="ลงชื่อเข้างาน", style=discord.ButtonStyle.success)
        btn_out = Button(label="ลงชื่อออกงาน", style=discord.ButtonStyle.danger)
        btn_in.callback = clockin_callback
        btn_out.callback = clockout_callback
        view.add_item(btn_in)
        view.add_item(btn_out)
        return view

    def employee_select_view(self):
        view = View(timeout=120)
        options = [discord.SelectOption(label=name, description=f"เลือก {name}") for name in EMPLOYEES.keys()]
        select = Select(placeholder="เลือกพนักงาน", options=options)

        async def select_callback(inter: discord.Interaction):
            self.selected_employee = select.values[0]
            await self.finish(inter)

        select.callback = select_callback
        view.add_item(select)
        return view

    async def finish(self, interaction: discord.Interaction):
        now = datetime.now(ZoneInfo(TIMEZONE))
        time_str = now.strftime("%Y-%m-%d %H:%M:%S %Z")
        time_iso = now.isoformat()

        # Save to Google Sheet
        asyncio.get_running_loop().run_in_executor(
            None, append_sheet_row, self.user_name, self.clock_type, time_iso
        )

        embed = discord.Embed(
            title=f"🕒 {self.clock_type} สำเร็จ",
            description=f"{interaction.user.mention} ลงชื่อ **{self.clock_type}** เรียบร้อย\n"
                        f"👤 ชื่อที่กรอก: `{self.user_name}`\n"
                        f"🧑‍💼 พนักงาน: `{self.selected_employee}`\n"
                        f"⏰ เวลา: {time_str}",
            color=discord.Color.green() if self.clock_type == "ลงชื่อเข้างาน" else discord.Color.red(),
            timestamp=now
        )
        card_url = EMPLOYEES.get(self.selected_employee)
        if card_url:
            embed.set_image(url=card_url)
        await interaction.response.edit_message(content=None, embed=embed, view=None)

@bot.command(name="clock")
async def clock(ctx):
    view = ClockView(ctx)
    await ctx.send("เริ่มการลงชื่อเข้างาน/ออกงาน กดปุ่มด้านล่าง 👇", view=view)

# ---------------------------
# Simple hello command
# ---------------------------
@bot.command(name="hello")
async def hello(ctx):
    await ctx.send("สวัสดีครับ!")

# ---------------------------
# Events
# ---------------------------
@bot.event
async def on_ready():
    print(f'✅ Logged in as {bot.user.name}')

# ---------------------------
# Main entry
# ---------------------------
if __name__ == "__main__":
    server_on()  # Flask server
    bot.run(DISCORD_TOKEN)

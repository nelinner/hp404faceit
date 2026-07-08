import asyncio
import logging
import random
import sqlite3
import os
import tempfile
import traceback
from datetime import datetime, timedelta
from io import BytesIO
from contextlib import suppress

from playwright.async_api import async_playwright
from PIL import Image

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, FSInputFile
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramRetryAfter

# ==================== КОНФИГУРАЦИЯ ====================
BOT_TOKEN = "8254209430:AAE78X4Dli5kutcpFwEXJOXfEslx_GCJjuw"
CHANNEL_USERNAME = "@testhp404bot"
SHOP_BOT = "@hp404shopbot"
CHAT_LINK = "https://t.me/hpfaceitchat"
NEWS_CHANNEL = "@hp404news"
LEADER_USERNAME = "nelinner"
VERIFY_CHANNEL = "https://t.me/+wdNdSgYj86A2M2Uy"
DB_NAME = "faceit.db"

MAPS = ["Dune", "Province", "Sandstone", "Hanami", "Rust", "Prison", "Breeze",
        "Bridge", "Pool", "Cableway", "Pipeline", "Village", "Arena"]

MAP_IMAGES = {
    "Dune": "https://i.ibb.co/qYRzXhvH/dune.png",
    "Province": "https://i.ibb.co/rfm56cRm/province.png",
    "Sandstone": "https://i.ibb.co/5W8tW0D1/sandstone.png",
    "Hanami": "https://i.ibb.co/Y7zNwp6r/hanami.png",
    "Rust": "https://i.ibb.co/gLyjnXQ8/rust.png",
    "Prison": "https://i.ibb.co/QF6ZL1ww/prison.png",
    "Breeze": "https://i.ibb.co/7J66n9dN/breeze.png",
    "Bridge": "https://i.ibb.co/qYRzXhvH/dune.png",
    "Pool": "https://i.ibb.co/rfm56cRm/province.png",
    "Cableway": "https://i.ibb.co/5W8tW0D1/sandstone.png",
    "Pipeline": "https://i.ibb.co/Y7zNwp6r/hanami.png",
    "Village": "https://i.ibb.co/gLyjnXQ8/rust.png",
    "Arena": "https://i.ibb.co/7J66n9dN/breeze.png"
}

# ==================== БАЗА ДАННЫХ ====================
def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            nickname TEXT,
            game_id TEXT,
            elo INTEGER DEFAULT 1000,
            can_create_lobby INTEGER DEFAULT 1,
            premium_until TEXT
        );
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY,
            role TEXT DEFAULT 'admin'
        );
        CREATE TABLE IF NOT EXISTS bans (
            user_id INTEGER PRIMARY KEY,
            reason TEXT,
            banned_until TEXT
        );
        CREATE TABLE IF NOT EXISTS lobbies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            host_id INTEGER,
            format TEXT,
            map TEXT,
            status TEXT DEFAULT 'open',
            message_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            teams_swapped INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS lobby_players (
            lobby_id INTEGER,
            user_id INTEGER,
            team INTEGER DEFAULT 0,
            PRIMARY KEY (lobby_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            type TEXT,
            content TEXT,
            status TEXT DEFAULT 'open',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lobby_id INTEGER,
            host_id INTEGER,
            map TEXT,
            ct_score INTEGER,
            t_score INTEGER,
            teams_swapped INTEGER DEFAULT 0,
            screenshot_id TEXT,
            played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    for col, col_def in [
        ("premium", "INTEGER DEFAULT 0"),
        ("verified", "INTEGER DEFAULT 0"),
        ("frame", "TEXT DEFAULT ''"),
        ("banner", "TEXT DEFAULT ''"),
        ("color_nick", "TEXT DEFAULT ''"),
        ("matches_played", "INTEGER DEFAULT 0"),
        ("kills", "INTEGER DEFAULT 0"),
        ("deaths", "INTEGER DEFAULT 0"),
        ("avatar_path", "TEXT DEFAULT ''"),
        ("avatar_file_id", "TEXT DEFAULT ''")
    ]:
        try:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col} {col_def}")
        except sqlite3.OperationalError:
            pass
    try:
        conn.execute("ALTER TABLE users ADD COLUMN premium_until TEXT")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()

async def db_execute(sql: str, params: tuple = ()):
    def _exec():
        conn = _get_conn()
        conn.execute(sql, params)
        conn.commit()
        conn.close()
    await asyncio.to_thread(_exec)

async def db_fetchone(sql: str, params: tuple = ()) -> dict | None:
    def _fetch():
        conn = _get_conn()
        row = conn.execute(sql, params).fetchone()
        conn.close()
        return dict(row) if row else None
    return await asyncio.to_thread(_fetch)

async def db_fetchall(sql: str, params: tuple = ()) -> list:
    def _fetch():
        conn = _get_conn()
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    return await asyncio.to_thread(_fetch)

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
async def check_subscription(bot: Bot, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(CHANNEL_USERNAME, user_id)
        return member.status not in ['left', 'kicked']
    except:
        return False

async def get_elo_rank(user_id: int) -> tuple:
    row = await db_fetchone("SELECT elo FROM users WHERE user_id = ?", (user_id,))
    if not row:
        return 0, 0
    elo = row['elo']
    cnt = await db_fetchone("SELECT COUNT(*) as cnt FROM users WHERE elo > ?", (elo,))
    return elo, cnt['cnt'] + 1

async def is_admin(user_id: int) -> bool:
    return await db_fetchone("SELECT role FROM admins WHERE user_id = ?", (user_id,)) is not None

async def is_leader(user_id: int) -> bool:
    row = await db_fetchone("SELECT role FROM admins WHERE user_id = ? AND role = 'leader'", (user_id,))
    return row is not None

async def is_banned(user_id: int) -> bool:
    row = await db_fetchone("SELECT banned_until FROM bans WHERE user_id = ?", (user_id,))
    if not row:
        return False
    banned_until = row['banned_until']
    if banned_until == "permanent":
        return True
    try:
        until = datetime.fromisoformat(banned_until)
        if until > datetime.now():
            return True
        else:
            await db_execute("DELETE FROM bans WHERE user_id = ?", (user_id,))
            return False
    except:
        return False

async def can_create_lobby(user_id: int) -> bool:
    row = await db_fetchone("SELECT can_create_lobby FROM users WHERE user_id = ?", (user_id,))
    return row and row['can_create_lobby'] == 1

async def get_admin_ids() -> list:
    rows = await db_fetchall("SELECT user_id FROM admins")
    return [r['user_id'] for r in rows]

async def db_get_account(user_id: int) -> dict | None:
    return await db_fetchone("SELECT nickname, game_id FROM users WHERE user_id=?", (user_id,))

async def db_get_player(user_id: int) -> dict | None:
    return await db_fetchone("SELECT elo, kills, deaths, matches_played, premium, verified, premium_until FROM users WHERE user_id=?", (user_id,))

async def is_premium(user_id: int) -> bool:
    row = await db_fetchone("SELECT premium, premium_until FROM users WHERE user_id=?", (user_id,))
    if not row or row['premium'] != 1:
        return False
    if row['premium_until']:
        try:
            until = datetime.fromisoformat(row['premium_until'])
            if datetime.now() > until:
                await db_execute("UPDATE users SET premium=0, premium_until=NULL WHERE user_id=?", (user_id,))
                return False
        except:
            pass
    return True

async def is_verified(user_id: int) -> bool:
    row = await db_fetchone("SELECT verified FROM users WHERE user_id=?", (user_id,))
    return row and row['verified'] == 1

async def find_user_by_nickname(nickname: str) -> int | None:
    row = await db_fetchone("SELECT user_id FROM users WHERE nickname=?", (nickname,))
    return row['user_id'] if row else None

def get_level(elo: int) -> int:
    return max(1, elo // 200)

# ==================== PLAYWRIGHT РЕНДЕРИНГ ====================
async def html_to_image(html: str) -> Image.Image:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        await page.set_content(html)
        await page.wait_for_timeout(2000)
        screenshot = await page.screenshot(full_page=True)
        await browser.close()
        return Image.open(BytesIO(screenshot)).convert("RGB")

# ==================== HTML-ШАБЛОНЫ ====================
def generate_lobby_html(lobby_data: dict) -> str:
    map_img_url = MAP_IMAGES.get(lobby_data.get('map', 'Rust'), '')
    players_html = ""
    for p in lobby_data.get('players', [])[:10]:
        role_class = "bg-orange-600" if "ADMIN" in p.get('role', '') else "bg-gray-600"
        players_html += f'''
            <div class="flex items-center bg-zinc-900/70 rounded-2xl p-4">
                <div class="w-10 h-10 bg-gray-600 rounded-full flex items-center justify-center mr-4">👤</div>
                <div class="flex-1">
                    <span class="font-bold text-xl">{p['name']}</span>
                    <span class="text-xs {role_class} px-2 py-0.5 rounded ml-2">{p.get('role', 'ИГРОК')}</span>
                    <p class="text-gray-400 text-sm">ID: {p['id']} | ELO: {p.get('elo', '—')}</p>
                </div>
                <div class="w-10 h-10 bg-green-500 rounded-full flex items-center justify-center font-bold">✓</div>
            </div>'''

    progress = lobby_data.get('progress', 0)
    dashoffset = 282 - (282 * progress / 100)

    return f'''<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <script src="https://cdn.tailwindcss.com"></script>
    <style>body{{background:linear-gradient(135deg,#0f0f14,#1a1a22);}}</style>
</head>
<body class="text-white min-h-screen p-6">
    <div class="max-w-6xl mx-auto">
        <div class="flex justify-between items-center mb-6">
            <div class="bg-orange-600 px-8 py-3 rounded-r-2xl font-bold text-xl">ЛОББИ #{lobby_data['number']}</div>
            <div class="text-center">
                <h1 class="text-4xl font-bold">РЕГИСТРАЦИЯ НА МАТЧ</h1>
                <p class="text-orange-500 text-2xl">404HP FACEIT</p>
            </div>
            <div class="bg-orange-600 px-10 py-3 rounded-l-2xl font-bold text-3xl">{lobby_data.get('format','5X5')}</div>
        </div>
        <div class="grid grid-cols-2 gap-6">
            <div class="bg-zinc-900/80 rounded-3xl p-6">
                <img src="{map_img_url}" class="w-full h-64 object-cover rounded-2xl">
                <p class="text-orange-500 font-bold mt-4">КАРТА: {lobby_data.get('map','Rust')}</p>
                <p class="text-lg">Хост: {lobby_data.get('host_name','')}</p>
            </div>
            <div class="bg-zinc-900/80 rounded-3xl p-6 space-y-4">
                <h2 class="text-2xl font-bold">👥 ИГРОКИ ({len(lobby_data.get('players',[]))}/10)</h2>
                {players_html}
            </div>
        </div>
        <div class="mt-8 bg-zinc-900/80 rounded-3xl p-8 flex items-center gap-10">
            <svg class="w-32 h-32 -rotate-90" viewBox="0 0 100 100">
                <circle cx="50" cy="50" r="45" fill="none" stroke="#3f3f46" stroke-width="8"/>
                <circle cx="50" cy="50" r="45" fill="none" stroke="#f97316" stroke-width="8" stroke-dasharray="282" stroke-dashoffset="{dashoffset}"/>
            </svg>
            <div class="text-5xl font-bold">{progress}%</div>
            <div class="text-right ml-auto">
                <div class="text-7xl font-black text-orange-500">404</div>
                <div class="text-4xl -mt-3 text-orange-500">HP</div>
            </div>
        </div>
    </div>
</body>
</html>'''

def generate_profile_html(player: dict, acc: dict) -> str:
    kd = player.get('kills', 0) / max(1, player.get('deaths', 1))
    verified = player.get('verified', 0) == 1
    premium = player.get('premium', 0) == 1
    nick_text = acc['nickname'] + (" ✅" if verified else "")
    status = "PREMIUM" if premium else "STANDARD"
    status_color = "text-yellow-400" if premium else "text-blue-400"

    return f'''<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <script src="https://cdn.tailwindcss.com"></script>
    <style>body{{background:linear-gradient(135deg,#1a1a1f,#0f0f14);}}</style>
</head>
<body class="text-white min-h-screen py-8">
    <div class="max-w-4xl mx-auto px-6">
        <div class="bg-zinc-900/90 rounded-3xl p-8 flex gap-8 border border-gray-700">
            <div class="w-32 h-32 bg-gray-700 rounded-2xl flex items-center justify-center text-5xl">👤</div>
            <div>
                <h1 class="text-5xl font-bold">{nick_text}</h1>
                <p class="text-gray-400">ID: {acc['game_id']} | ELO: {player.get('elo',0)}</p>
                <p class="{status_color} text-xl mt-2 font-bold">{status}</p>
                <div class="mt-4 text-6xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-pink-500 to-blue-500">{kd:.2f}</div>
                <p class="text-gray-400">K/D Ratio | K={player.get('kills',0)} D={player.get('deaths',0)}</p>
            </div>
        </div>
        <div class="mt-6 bg-zinc-900/90 rounded-3xl p-8">
            <h2 class="text-2xl font-bold mb-4">📊 СТАТИСТИКА</h2>
            <div class="grid grid-cols-2 gap-4 text-lg">
                <p>⚔️ K/D: {kd:.2f}</p><p>💀 Убийств: {player.get('kills',0)}</p>
                <p>🛡️ Смертей: {player.get('deaths',0)}</p><p>🎮 Матчей: {player.get('matches_played',0)}</p>
                <p>⭐ Premium: {"Да" if premium else "Нет"}</p><p>✅ Верификация: {"Да" if verified else "Нет"}</p>
            </div>
        </div>
        <p class="text-center text-gray-500 mt-6">404hp FACEIT © 2026</p>
    </div>
</body>
</html>'''

def generate_draft_html(lobby_data: dict) -> str:
    ct_html = "".join(f'<p>👤 {p["name"]} — {p.get("matches",0)} матчей, K/D {p.get("kd",0):.2f}</p>' for p in lobby_data.get('ct_players', []))
    t_html = "".join(f'<p>👤 {p["name"]} — {p.get("matches",0)} матчей, K/D {p.get("kd",0):.2f}</p>' for p in lobby_data.get('t_players', []))
    ct_elo = lobby_data.get('ct_elo', 0)
    t_elo = lobby_data.get('t_elo', 0)
    total = ct_elo + t_elo or 1
    ct_pct = (ct_elo / total) * 100

    return f'''<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <script src="https://cdn.tailwindcss.com"></script>
    <style>body{{background:linear-gradient(145deg,#0a0a0f,#1a1a22);}}</style>
</head>
<body class="text-white min-h-screen py-8 px-4">
    <h1 class="text-center text-4xl font-bold text-orange-500 mb-8">⚔️ ЖЕРЕБЬЁВКА</h1>
    <div class="grid grid-cols-2 gap-8 max-w-5xl mx-auto">
        <div class="bg-blue-900/40 rounded-3xl p-6 border-2 border-blue-500">
            <h2 class="text-2xl font-bold text-blue-400">🔵 Counter-Terrorists</h2>
            <p class="text-lg mt-2">ELO: {ct_elo}</p>
            <div class="mt-4 space-y-2">{ct_html}</div>
        </div>
        <div class="bg-red-900/40 rounded-3xl p-6 border-2 border-red-500">
            <h2 class="text-2xl font-bold text-red-400">🔴 Terrorists</h2>
            <p class="text-lg mt-2">ELO: {t_elo}</p>
            <div class="mt-4 space-y-2">{t_html}</div>
        </div>
    </div>
    <div class="max-w-5xl mx-auto mt-6 bg-zinc-900/80 rounded-3xl p-4">
        <div class="h-4 bg-gray-700 rounded-full overflow-hidden">
            <div class="h-4 bg-gradient-to-r from-blue-500 to-red-500" style="width:{ct_pct}%"></div>
        </div>
        <div class="flex justify-between text-sm mt-2"><span>CT: {ct_elo}</span><span>T: {t_elo}</span></div>
    </div>
</body>
</html>'''

# ================== АСИНХРОННЫЕ ГЕНЕРАТОРЫ ==================
async def generate_lobby_image(lobby: dict, bot: Bot) -> Image.Image:
    players = []
    for uid in lobby.get('registered_players', []):
        acc = await db_get_account(uid)
        player = await db_get_player(uid)
        if acc:
            role = "ADMIN" if await is_admin(uid) else "ИГРОК"
            players.append({"name": acc['nickname'], "id": acc['game_id'], "role": role, "elo": player.get('elo','—') if player else '—'})
    host_acc = await db_get_account(lobby.get('host_id'))
    host_name = host_acc['nickname'] if host_acc else str(lobby.get('host_id'))
    progress = int((len(players) / 10) * 100) if lobby.get('format') == '5x5' else 50
    html = generate_lobby_html({
        "number": lobby['number'], "map": lobby.get('map','Rust'), "format": lobby.get('format','5X5'),
        "players": players, "progress": progress, "host_name": host_name
    })
    return await html_to_image(html)

async def generate_draft_image(lobby: dict, bot: Bot) -> Image.Image:
    teams = lobby.get('teams', {})
    ct_players, t_players = [], []
    ct_elo, t_elo = 0, 0
    for uid, side in teams.items():
        acc = await db_get_account(uid)
        player = await db_get_player(uid)
        if not acc: continue
        pinfo = {"name": acc['nickname'], "matches": player.get('matches_played',0) if player else 0,
                 "kd": player['kills']/max(1,player['deaths']) if player else 0}
        if side == "CT": ct_players.append(pinfo); ct_elo += player.get('elo',0) if player else 0
        else: t_players.append(pinfo); t_elo += player.get('elo',0) if player else 0
    html = generate_draft_html({"ct_players":ct_players, "t_players":t_players, "ct_elo":ct_elo, "t_elo":t_elo})
    return await html_to_image(html)

async def generate_profile_image(player: dict, acc: dict, bot: Bot) -> Image.Image:
    html = generate_profile_html(player, acc)
    return await html_to_image(html)

def save_image_temp(img: Image.Image) -> str:
    fd, path = tempfile.mkstemp(suffix='.png')
    os.close(fd)
    img.save(path, format='PNG')
    return path

# ==================== СОСТОЯНИЯ ====================
class RegStates(StatesGroup):
    waiting_nickname = State()
    waiting_game_id = State()
    confirm = State()

class LobbyStates(StatesGroup):
    choosing_format = State()
    choosing_map = State()
    confirm_creation = State()

class TicketPlayerStates(StatesGroup):
    nick = State()
    description = State()
    from_nick = State()
    photo = State()

class TicketHostStates(StatesGroup):
    host_nick = State()
    lobby_number = State()
    description = State()
    photo = State()
    from_nick = State()

class TicketAdminStates(StatesGroup):
    admin_nick = State()
    description = State()
    from_nick = State()
    photo = State()

class ResultStates(StatesGroup):
    waiting_lobby_id = State()
    waiting_screenshot = State()
    waiting_score = State()
    confirm_swap = State()

class AdminNickInput(StatesGroup): waiting_nickname = State()
class AdminReasonInput(StatesGroup): waiting_reason = State()
class AdminSelectBanDuration(StatesGroup): waiting_selection = State()
class AdminSelectPremiumDuration(StatesGroup): waiting_selection = State()
class AdminTicketReview(StatesGroup): waiting_ticket_id = State()
class AdminTestShuffle(StatesGroup): waiting_lobby_id = State()
class AvatarUpload(StatesGroup): waiting_photo = State()

# ==================== КЛАВИАТУРЫ ====================
def main_keyboard():
    builder = ReplyKeyboardBuilder()
    for b in ["👤 Профиль","🔍 Найти матч","➕ Создать матч","🎮 Мои лобби",
              "🎟 Тикет поддержки","🛒 Магазин",
              "🏆 Топ игроков FACEIT","📰 Новости","💬 Чат проекта",
              "📜 Регламент проекта","🛠 Админ-панель","🖼 Установить аватар"]:
        builder.button(text=b)
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

def back_to_menu():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 В главное меню", callback_data="main_menu")]])

def admin_panel_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1. Выдать премиум", callback_data="admin_give_premium")],
        [InlineKeyboardButton(text="2. Забрать премиум", callback_data="admin_remove_premium")],
        [InlineKeyboardButton(text="3. Выдать верификацию", callback_data="admin_give_verify")],
        [InlineKeyboardButton(text="4. Забрать верификацию", callback_data="admin_remove_verify")],
        [InlineKeyboardButton(text="5. Забанить", callback_data="admin_ban")],
        [InlineKeyboardButton(text="6. Разбанить", callback_data="admin_unban")],
        [InlineKeyboardButton(text="7. Запрет создания лобби", callback_data="admin_lobby_ban")],
        [InlineKeyboardButton(text="8. Разрешить создание лобби", callback_data="admin_lobby_unban")],
        [InlineKeyboardButton(text="9. Тестовая жеребьёвка", callback_data="admin_test_shuffle")],
        [InlineKeyboardButton(text="10. Рассмотреть тикет", callback_data="admin_review_ticket")],
        [InlineKeyboardButton(text="11. Выдать админку", callback_data="admin_add_admin")],
        [InlineKeyboardButton(text="12. Забрать админку", callback_data="admin_remove_admin")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")]
    ])

def ban_duration_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="10 минут", callback_data="ban_10m")],
        [InlineKeyboardButton(text="30 минут", callback_data="ban_30m")],
        [InlineKeyboardButton(text="1 час", callback_data="ban_1h")],
        [InlineKeyboardButton(text="1 день", callback_data="ban_1d")],
        [InlineKeyboardButton(text="1 неделя", callback_data="ban_1w")],
        [InlineKeyboardButton(text="1 месяц", callback_data="ban_1mo")],
        [InlineKeyboardButton(text="1 год", callback_data="ban_1y")],
        [InlineKeyboardButton(text="Навсегда", callback_data="ban_forever")],
        [InlineKeyboardButton(text="🔙 Отмена", callback_data="main_menu")]
    ])

def premium_duration_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1 месяц", callback_data="prem_1mo")],
        [InlineKeyboardButton(text="1 год", callback_data="prem_1y")],
        [InlineKeyboardButton(text="🔙 Отмена", callback_data="main_menu")]
    ])

# ==================== ОБРАБОТЧИКИ ====================
dp = Dispatcher(storage=MemoryStorage())

@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext, bot: Bot):
    user_id = message.from_user.id
    if message.from_user.username and message.from_user.username.lower() == LEADER_USERNAME.lower():
        await db_execute("INSERT OR REPLACE INTO admins VALUES (?, 'leader')", (user_id,))
    if await is_banned(user_id):
        await message.answer("Вы забанены."); return
    if await db_fetchone("SELECT nickname FROM users WHERE user_id=?", (user_id,)):
        await message.answer("✊ Добро пожаловать обратно!", reply_markup=main_keyboard()); return
    if not await check_subscription(bot, user_id):
        await message.answer("Для доступа подпишитесь на @testhp404bot", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Подписаться", url=f"https://t.me/{CHANNEL_USERNAME[1:]}")],
            [InlineKeyboardButton(text="🔎 Проверить", callback_data="check_sub")]
        ]))
    else:
        await start_registration(message, state)

@dp.callback_query(F.data == "check_sub")
async def check_sub_callback(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if await check_subscription(bot, callback.from_user.id):
        await callback.message.delete(); await start_registration(callback.message, state)
    else:
        await callback.answer("❌ Вы не подписаны!", show_alert=True)

async def start_registration(message: Message, state: FSMContext):
    await message.answer("Введи игровой ник:"); await state.set_state(RegStates.waiting_nickname)

@dp.message(RegStates.waiting_nickname)
async def reg_nickname(message: Message, state: FSMContext):
    await state.update_data(nickname=message.text)
    await message.answer("Введи игровой ID (число):"); await state.set_state(RegStates.waiting_game_id)

@dp.message(RegStates.waiting_game_id)
async def reg_game_id(message: Message, state: FSMContext):
    if not message.text.isdigit(): await message.answer("ID должен быть числом."); return
    await state.update_data(game_id=message.text)
    await message.answer(f"Ник: {(await state.get_data())['nickname']}\nID: {message.text}\nПодтвердить?",
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                             [InlineKeyboardButton(text="✅ Да", callback_data="confirm_reg")],
                             [InlineKeyboardButton(text="❌ Нет", callback_data="cancel_reg")]
                         ]))
    await state.set_state(RegStates.confirm)

@dp.callback_query(RegStates.confirm, F.data == "confirm_reg")
async def reg_confirm(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await db_execute("INSERT OR REPLACE INTO users VALUES (?,?,?,1000)", (callback.from_user.id, data['nickname'], data['game_id']))
    await callback.message.delete(); await callback.message.answer("✅ Регистрация завершена!", reply_markup=main_keyboard())
    await state.clear()

@dp.callback_query(F.data == "cancel_reg")
async def reg_cancel(callback: CallbackQuery, state: FSMContext):
    await callback.message.delete(); await callback.message.answer("Отменено."); await state.clear()

# --- ПРОФИЛЬ ---
@dp.message(F.text == "👤 Профиль")
async def profile(message: Message, bot: Bot):
    user_id = message.from_user.id
    acc = await db_get_account(user_id)
    if not acc: await message.answer("Не зарегистрированы. /start"); return
    player = await db_get_player(user_id) or {}
    try:
        img = await generate_profile_image(player, acc, bot)
        path = save_image_temp(img)
        elo, rank = await get_elo_rank(user_id)
        kd = player.get('kills',0)/max(1,player.get('deaths',1))
        premium = await is_premium(user_id)
        verified = await is_verified(user_id)
        await message.answer_photo(FSInputFile(path), caption=f"🪪 {acc['nickname']}\n🔗 ID: {acc['game_id']}\n🔫 K/D: {kd:.2f}\n🏆 Место: #{rank}\n⭐ Premium: {'✅' if premium else '❌'}\n✊ ELO: {elo}\n✅ Верификация: {'✅' if verified else '❌'}")
        os.unlink(path)
    except Exception as e:
        logging.error(f"Ошибка профиля: {e}"); await message.answer("Ошибка создания карточки.")

@dp.message(F.text == "🖼 Установить аватар")
async def set_avatar_start(message: Message, state: FSMContext):
    await message.answer("Отправьте изображение для аватара.")
    await state.set_state(AvatarUpload.waiting_photo)

@dp.message(AvatarUpload.waiting_photo, F.photo)
async def avatar_photo_handler(message: Message, state: FSMContext):
    file_id = message.photo[-1].file_id
    await db_execute("UPDATE users SET avatar_file_id=? WHERE user_id=?", (file_id, message.from_user.id))
    await message.answer("✅ Аватар обновлён!", reply_markup=main_keyboard())
    await state.clear()

@dp.message(AvatarUpload.waiting_photo, ~F.photo)
async def avatar_not_photo(message: Message):
    await message.answer("Пожалуйста, отправьте изображение.")

# --- МОИ ЛОББИ ---
@dp.message(F.text == "🎮 Мои лобби")
async def my_lobbies(message: Message):
    user_id = message.from_user.id
    lobbies = await db_fetchall("SELECT id, format, map, status FROM lobbies WHERE host_id=? ORDER BY created_at DESC", (user_id,))
    if not lobbies:
        await message.answer("У вас нет созданных лобби."); return
    text = "🎮 Ваши лобби:\n"
    builder = InlineKeyboardBuilder()
    for lobby in lobbies:
        text += f"Лобби #{lobby['id']} ({lobby['format']}) {lobby['map']} — {lobby['status']}\n"
        builder.button(text=f"Лобби #{lobby['id']}", callback_data=f"mylobby_{lobby['id']}")
    builder.button(text="🔙 Назад", callback_data="main_menu")
    builder.adjust(1)
    await message.answer(text, reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("mylobby_"))
async def mylobby_action(callback: CallbackQuery):
    lobby_id = int(callback.data.split("_")[1])
    lobby = await db_fetchone("SELECT * FROM lobbies WHERE id=?", (lobby_id,))
    if not lobby or lobby['host_id'] != callback.from_user.id:
        await callback.answer("Это не ваше лобби.", show_alert=True); return
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Отменить лобби", callback_data=f"cancel_lobby_{lobby_id}")],
        [InlineKeyboardButton(text="Зарегистрировать результат", callback_data=f"result_lobby_{lobby_id}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")]
    ])
    await callback.message.edit_text(f"Лобби #{lobby_id} ({lobby['format']}) {lobby['map']} — {lobby['status']}", reply_markup=markup)
    await callback.answer()

@dp.callback_query(F.data.startswith("cancel_lobby_"))
async def cancel_my_lobby(callback: CallbackQuery, bot: Bot):
    lobby_id = int(callback.data.split("_")[2])
    lobby = await db_fetchone("SELECT * FROM lobbies WHERE id=?", (lobby_id,))
    if not lobby or lobby['host_id'] != callback.from_user.id or lobby['status'] != 'open':
        await callback.answer("Недоступно.", show_alert=True); return
    if lobby['message_id']:
        try: await bot.delete_message(chat_id=CHANNEL_USERNAME, message_id=lobby['message_id'])
        except: pass
    await db_execute("DELETE FROM lobby_players WHERE lobby_id=?", (lobby_id,))
    await db_execute("DELETE FROM lobbies WHERE id=?", (lobby_id,))
    await callback.message.edit_text("✅ Лобби отменено.")
    await callback.answer("Лобби удалено.")

@dp.callback_query(F.data.startswith("result_lobby_"))
async def result_from_mylobby(callback: CallbackQuery, state: FSMContext):
    lobby_id = int(callback.data.split("_")[2])
    lobby = await db_fetchone("SELECT * FROM lobbies WHERE id=?", (lobby_id,))
    if not lobby or lobby['host_id'] != callback.from_user.id or lobby['status'] != 'in_progress':
        await callback.answer("Матч не начат или уже завершён.", show_alert=True); return
    await state.update_data(lobby_id=lobby_id, host_id=lobby['host_id'], map_name=lobby['map'])
    await callback.message.answer("Пришлите скриншот результатов:")
    await state.set_state(ResultStates.waiting_screenshot)
    await callback.answer()

# --- ЛОББИ ---
@dp.message(F.text == "➕ Создать матч")
async def create_match(message: Message, state: FSMContext):
    if not await can_create_lobby(message.from_user.id):
        await message.answer("⛔ Вам запрещено создавать лобби."); return
    await message.answer("Выбери формат:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="5x5", callback_data="format_5x5")],
        [InlineKeyboardButton(text="2x2", callback_data="format_2x2")],
        [InlineKeyboardButton(text="1x1", callback_data="format_1x1")]
    ]))
    await state.set_state(LobbyStates.choosing_format)

@dp.callback_query(LobbyStates.choosing_format)
async def format_chosen(callback: CallbackQuery, state: FSMContext):
    await state.update_data(format=callback.data.split("_")[1])
    await callback.message.delete()
    maps = {"5x5": ["Dune","Sandstone","Rust","Province","Hanami","Breeze","Prison"],
            "2x2": ["Dune","Sandstone","Rust","Province","Hanami","Breeze","Prison"],
            "1x1": ["Bridge","Pool","Cableway","Pipeline","Village","Arena"]}
    builder = InlineKeyboardBuilder()
    for m in maps[callback.data.split("_")[1]]: builder.button(text=m, callback_data=f"map_{m}")
    await callback.message.answer("Выбери карту:", reply_markup=builder.adjust(2).as_markup())
    await state.set_state(LobbyStates.choosing_map)

@dp.callback_query(LobbyStates.choosing_map)
async def map_chosen(callback: CallbackQuery, state: FSMContext):
    await state.update_data(map=callback.data.split("_",1)[1])
    await callback.message.delete()
    await callback.message.answer("✅ Настройки сохранены. Нажмите кнопку ниже для создания лобби.",
                                   reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                       [InlineKeyboardButton(text="🚀 Создать лобби", callback_data="create_lobby")],
                                       [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_lobby")]
                                   ]))
    await state.set_state(LobbyStates.confirm_creation)

@dp.callback_query(LobbyStates.confirm_creation, F.data == "create_lobby")
async def lobby_created(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    fmt, map_name = data['format'], data['map']
    host_id = callback.from_user.id
    def _create():
        conn = _get_conn()
        cur = conn.execute("INSERT INTO lobbies (host_id, format, map) VALUES (?,?,?)", (host_id, fmt, map_name))
        lid = cur.lastrowid
        conn.execute("INSERT INTO lobby_players VALUES (?,?,0)", (lid, host_id))
        conn.commit()
        conn.close()
        return lid
    lid = await asyncio.to_thread(_create)
    await update_lobby_image(bot, lid)
    await callback.message.delete()
    await callback.message.answer(f"✅ Лобби #{lid} создано!", reply_markup=main_keyboard())
    await state.clear()

@dp.callback_query(F.data == "cancel_lobby")
async def cancel_lobby(callback: CallbackQuery, state: FSMContext):
    await callback.message.delete(); await callback.message.answer("Создание отменено.", reply_markup=main_keyboard())
    await state.clear()

async def update_lobby_image(bot: Bot, lobby_id: int):
    lobby = await db_fetchone("SELECT message_id, format, map, host_id FROM lobbies WHERE id=?", (lobby_id,))
    if not lobby: return
    msg_id, fmt, map_name, host_id = lobby['message_id'], lobby['format'], lobby['map'], lobby['host_id']
    players = [r['user_id'] for r in await db_fetchall("SELECT user_id FROM lobby_players WHERE lobby_id=?", (lobby_id,))]
    try:
        img = await generate_lobby_image({"number":lobby_id,"format":fmt,"map":map_name,"host_id":host_id,"registered_players":players}, bot)
        if not img: return
        path = save_image_temp(img)
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✊ Присоединиться", callback_data=f"join_{lobby_id}")],
            [InlineKeyboardButton(text="🔙 Выйти", callback_data=f"leave_{lobby_id}")]
        ])
        if msg_id:
            try: await bot.delete_message(chat_id=CHANNEL_USERNAME, message_id=msg_id)
            except: pass
        msg = await bot.send_photo(chat_id=CHANNEL_USERNAME, photo=FSInputFile(path), caption=f"Лобби #{lobby_id} | {fmt} | {map_name}", reply_markup=markup)
        await db_execute("UPDATE lobbies SET message_id=? WHERE id=?", (msg.message_id, lobby_id))
        os.unlink(path)
    except Exception as e:
        logging.error(f"Ошибка публикации: {e}")

@dp.callback_query(F.data.startswith("join_"))
async def join_lobby(callback: CallbackQuery, bot: Bot):
    lid = int(callback.data.split("_")[1])
    uid = callback.from_user.id
    lobby = await db_fetchone("SELECT format, status FROM lobbies WHERE id=?", (lid,))
    if not lobby or lobby['status']!='open': await callback.answer("Лобби закрыто.", show_alert=True); return
    needed = {"5x5":10,"2x2":4,"1x1":2}[lobby['format']]
    if await db_fetchone("SELECT * FROM lobby_players WHERE lobby_id=? AND user_id=?",(lid,uid)):
        await callback.answer("Уже в лобби.", show_alert=True); return
    count = (await db_fetchone("SELECT COUNT(*) as cnt FROM lobby_players WHERE lobby_id=?",(lid,)))['cnt']
    if count >= needed: await callback.answer("Заполнено.", show_alert=True); return
    await db_execute("INSERT INTO lobby_players VALUES (?,?,0)",(lid,uid))
    await update_lobby_image(bot, lid)
    if count+1 == needed:
        host = (await db_fetchone("SELECT host_id FROM lobbies WHERE id=?",(lid,)))['host_id']
        try: await bot.send_message(host, f"Лобби #{lid} заполнено! Жеребьёвка.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Жеребьёвка", callback_data=f"shuffle_{lid}")]]))
        except: pass
    await callback.answer("Присоединился!")

@dp.callback_query(F.data.startswith("leave_"))
async def leave_lobby(callback: CallbackQuery, bot: Bot):
    lid = int(callback.data.split("_")[1])
    await db_execute("DELETE FROM lobby_players WHERE lobby_id=? AND user_id=?",(lid,callback.from_user.id))
    await update_lobby_image(bot, lid)
    await callback.answer("Вышел.")

@dp.callback_query(F.data.startswith("shuffle_"))
async def shuffle_lobby(callback: CallbackQuery, bot: Bot):
    lid = int(callback.data.split("_")[1])
    lobby = await db_fetchone("SELECT host_id, format, map, message_id FROM lobbies WHERE id=?",(lid,))
    if not lobby or lobby['host_id']!=callback.from_user.id: await callback.answer("Только хост.", show_alert=True); return
    players = [r['user_id'] for r in await db_fetchall("SELECT user_id FROM lobby_players WHERE lobby_id=?",(lid,))]
    random.shuffle(players); half = len(players)//2
    for u in players[:half]: await db_execute("UPDATE lobby_players SET team=1 WHERE lobby_id=? AND user_id=?",(lid,u))
    for u in players[half:]: await db_execute("UPDATE lobby_players SET team=2 WHERE lobby_id=? AND user_id=?",(lid,u))
    await db_execute("UPDATE lobbies SET status='in_progress' WHERE id=?",(lid,))
    teams = {u: "CT" for u in players[:half]}; teams.update({u: "T" for u in players[half:]})
    try:
        img = await generate_draft_image({"teams":teams}, bot)
        if img:
            path = save_image_temp(img)
            if lobby['message_id']:
                try: await bot.delete_message(chat_id=CHANNEL_USERNAME, message_id=lobby['message_id'])
                except: pass
            await bot.send_photo(chat_id=CHANNEL_USERNAME, photo=FSInputFile(path), caption=f"⚔️ Жеребьёвка лобби #{lid}")
            os.unlink(path)
    except: pass
    for u in players:
        try: await bot.send_message(u, f"Лобби #{lid}: {'защита' if u in players[:half] else 'атака'}")
        except: pass
    await callback.answer("Жеребьёвка проведена!")

@dp.message(F.text == "🔍 Найти матч")
async def find_match(message: Message):
    lobbies = await db_fetchall("SELECT id, format, map FROM lobbies WHERE status='open' ORDER BY created_at DESC LIMIT 10")
    if not lobbies: await message.answer("Нет открытых лобби."); return
    for lobby in lobbies:
        lid, fmt, mn = lobby['id'], lobby['format'], lobby['map']
        count = (await db_fetchone("SELECT COUNT(*) as cnt FROM lobby_players WHERE lobby_id=?",(lid,)))['cnt']
        needed = {"5x5":10,"2x2":4,"1x1":2}.get(fmt,10)
        await message.answer(f"Лобби #{lid} ({fmt}) {mn}\n{count}/{needed}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✊ Присоединиться", callback_data=f"join_{lid}")],
            [InlineKeyboardButton(text="🔙 Выйти", callback_data=f"leave_{lid}")]
        ]))

# --- РЕЗУЛЬТАТЫ ---
@dp.message(Command("results"))
async def results_start(message: Message, state: FSMContext):
    await message.answer("Введите номер лобби:")
    await state.set_state(ResultStates.waiting_lobby_id)

@dp.message(ResultStates.waiting_lobby_id)
async def results_lobby_id(message: Message, state: FSMContext):
    if not message.text.isdigit(): await message.answer("Номер лобби должен быть числом."); return
    lid = int(message.text)
    lobby = await db_fetchone("SELECT * FROM lobbies WHERE id=?", (lid,))
    if not lobby: await message.answer("Лобби не найдено."); return
    if lobby['status'] != 'in_progress': await message.answer("Матч не начат или завершён."); return
    await state.update_data(lobby_id=lid, host_id=lobby['host_id'], map_name=lobby['map'])
    await message.answer("Пришлите скриншот результатов:")
    await state.set_state(ResultStates.waiting_screenshot)

@dp.message(ResultStates.waiting_screenshot, F.photo)
async def results_screenshot(message: Message, state: FSMContext):
    await state.update_data(screenshot=message.photo[-1].file_id)
    await message.answer("📊 Введите счёт матча в формате:\nCT T\nПример: 16 14")
    await state.set_state(ResultStates.waiting_score)

@dp.message(ResultStates.waiting_score)
async def results_score(message: Message, state: FSMContext, bot: Bot):
    parts = message.text.strip().split()
    if len(parts) < 2: await message.answer("Неверный формат. Введите: CT T"); return
    try:
        ct_score = int(parts[0]); t_score = int(parts[1])
    except: await message.answer("Счёт должен быть числом."); return
    await state.update_data(ct_score=ct_score, t_score=t_score)
    await message.answer("🔄 Команды поменялись сторонами?",
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                             [InlineKeyboardButton(text="✅ Да, поменялись", callback_data="swap_yes")],
                             [InlineKeyboardButton(text="❌ Нет", callback_data="swap_no")]
                         ]))
    await state.set_state(ResultStates.confirm_swap)

@dp.callback_query(ResultStates.confirm_swap, F.data.in_(["swap_yes", "swap_no"]))
async def results_swap(callback: CallbackQuery, state: FSMContext, bot: Bot):
    swapped = callback.data == "swap_yes"
    data = await state.get_data()
    lid, ct_score, t_score, screenshot, host_id, map_name = data['lobby_id'], data['ct_score'], data['t_score'], data['screenshot'], data['host_id'], data['map_name']
    winner = "CT" if ct_score > t_score else "T"
    winner_team = 1 if winner == "CT" else 2
    players = await db_fetchall("SELECT user_id FROM lobby_players WHERE lobby_id=?", (lid,))
    for p in players:
        uid = p['user_id']
        player_team = (await db_fetchone("SELECT team FROM lobby_players WHERE lobby_id=? AND user_id=?", (lid, uid)))['team']
        actual_team = 2 if (player_team == 1 and swapped) or (player_team == 2 and not swapped) else player_team
        if actual_team == winner_team:
            await db_execute("UPDATE users SET elo=elo+25, matches_played=matches_played+1 WHERE user_id=?", (uid,))
        else:
            await db_execute("UPDATE users SET matches_played=matches_played+1 WHERE user_id=?", (uid,))
    ct_list, t_list = [], []
    for p in players:
        uid = p['user_id']; acc = await db_get_account(uid)
        player_team = (await db_fetchone("SELECT team FROM lobby_players WHERE lobby_id=? AND user_id=?", (lid, uid)))['team']
        actual_team = 2 if (player_team == 1 and swapped) or (player_team == 2 and not swapped) else player_team
        elo = (await db_get_player(uid))['elo'] if await db_get_player(uid) else 0
        (ct_list if actual_team == 1 else t_list).append(f"{len(ct_list if actual_team==1 else t_list)+1}. {acc['nickname']} (ELO: {elo})")
    await db_execute("INSERT INTO matches (lobby_id, host_id, map, ct_score, t_score, teams_swapped, screenshot_id) VALUES (?,?,?,?,?,?,?)",
                     (lid, host_id, map_name, ct_score, t_score, int(swapped), screenshot))
    await db_execute("UPDATE lobbies SET status='finished', teams_swapped=? WHERE id=?", (int(swapped), lid))
    host_acc = await db_get_account(host_id)
    host_name = host_acc['nickname'] if host_acc else str(host_id)
    result_text = (f"📊 РЕЗУЛЬТАТ МАТЧА\nЛобби #{lid} | host: {host_name}\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
                   f"🗺 🌴 {map_name}\n\n{'🔄 Команды поменялись сторонами' if swapped else ''}\n\n"
                   f"🔵 CT: {ct_score}\n{chr(10).join(ct_list)}\n\n🔴 T: {t_score}\n{chr(10).join(t_list)}\n\n"
                   f"🏆 Победитель: {winner}\n📸 Скриншот прилагается")
    try: await bot.send_photo(chat_id=CHANNEL_USERNAME, photo=screenshot, caption=result_text)
    except: await bot.send_message(chat_id=CHANNEL_USERNAME, text=result_text)
    await callback.message.delete()
    await callback.message.answer("✅ Результаты сохранены и опубликованы!", reply_markup=main_keyboard())
    await state.clear()

# --- ТИКЕТЫ ---
@dp.message(F.text == "🎟 Тикет поддержки")
async def ticket_menu(message: Message):
    await message.answer("Тикет:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1. Жалоба на игрока", callback_data="ticket_player")],
        [InlineKeyboardButton(text="2. Жалоба на хоста", callback_data="ticket_host")],
        [InlineKeyboardButton(text="3. Жалоба на администрацию", callback_data="ticket_admin")],
        [InlineKeyboardButton(text="4. Вопросы по проекту", callback_data="ticket_faq")],
        [InlineKeyboardButton(text="5. Получить верификацию", callback_data="ticket_verify")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")]
    ]))

@dp.callback_query(F.data == "ticket_player")
async def ticket_player(callback: CallbackQuery, state: FSMContext):
    await callback.message.delete(); await callback.message.answer("Введи ник игрока:"); await state.set_state(TicketPlayerStates.nick)

@dp.message(TicketPlayerStates.nick)
async def player_nick(message: Message, state: FSMContext):
    await state.update_data(target_nick=message.text); await message.answer("Опиши жалобу:"); await state.set_state(TicketPlayerStates.description)

@dp.message(TicketPlayerStates.description)
async def player_desc(message: Message, state: FSMContext):
    await state.update_data(description=message.text); await message.answer("От кого (твой ник):"); await state.set_state(TicketPlayerStates.from_nick)

@dp.message(TicketPlayerStates.from_nick)
async def player_from(message: Message, state: FSMContext):
    await state.update_data(from_nick=message.text); await message.answer("Прикрепи скриншот (или '-')"); await state.set_state(TicketPlayerStates.photo)

@dp.message(TicketPlayerStates.photo)
async def player_photo(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    photo_id = message.photo[-1].file_id if message.photo else "нет"
    content = f"👤 Жалоба на игрока\nНик цели: {data['target_nick']}\nОписание: {data['description']}\nОт: {data['from_nick']}"
    await db_execute("INSERT INTO tickets (user_id, type, content) VALUES (?, 'player', ?)", (message.from_user.id, content))
    for admin_id in await get_admin_ids():
        try:
            if photo_id != "нет": await bot.send_photo(admin_id, photo_id, caption=content)
            else: await bot.send_message(admin_id, content)
        except: pass
    await message.answer("Отправлено.", reply_markup=main_keyboard()); await state.clear()

@dp.callback_query(F.data == "ticket_host")
async def ticket_host(callback: CallbackQuery, state: FSMContext):
    await callback.message.delete(); await callback.message.answer("Введи ник хоста:"); await state.set_state(TicketHostStates.host_nick)

@dp.message(TicketHostStates.host_nick)
async def host_nick(message: Message, state: FSMContext):
    await state.update_data(host_nick=message.text); await message.answer("Номер лобби:"); await state.set_state(TicketHostStates.lobby_number)

@dp.message(TicketHostStates.lobby_number)
async def host_lobby(message: Message, state: FSMContext):
    if not message.text.isdigit(): await message.answer("Число."); return
    await state.update_data(lobby_number=message.text); await message.answer("Описание:"); await state.set_state(TicketHostStates.description)

@dp.message(TicketHostStates.description)
async def host_desc(message: Message, state: FSMContext):
    await state.update_data(description=message.text); await message.answer("Скриншот (или '-'):"); await state.set_state(TicketHostStates.photo)

@dp.message(TicketHostStates.photo)
async def host_photo(message: Message, state: FSMContext):
    photo_id = message.photo[-1].file_id if message.photo else "нет"
    await state.update_data(photo=photo_id); await message.answer("От кого:"); await state.set_state(TicketHostStates.from_nick)

@dp.message(TicketHostStates.from_nick)
async def host_from(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    content = f"🎮 Жалоба на хоста\nНик: {data['host_nick']}\nЛобби: {data['lobby_number']}\nОписание: {data['description']}\nОт: {message.text}"
    await db_execute("INSERT INTO tickets (user_id, type, content) VALUES (?, 'host', ?)", (message.from_user.id, content))
    for admin_id in await get_admin_ids():
        try:
            if data['photo'] != "нет": await bot.send_photo(admin_id, data['photo'], caption=content)
            else: await bot.send_message(admin_id, content)
        except: pass
    await message.answer("Отправлено.", reply_markup=main_keyboard()); await state.clear()

@dp.callback_query(F.data == "ticket_admin")
async def ticket_admin(callback: CallbackQuery, state: FSMContext):
    await callback.message.delete(); await callback.message.answer("Ник админа:"); await state.set_state(TicketAdminStates.admin_nick)

@dp.message(TicketAdminStates.admin_nick)
async def admin_nick(message: Message, state: FSMContext):
    await state.update_data(admin_nick=message.text); await message.answer("Описание:"); await state.set_state(TicketAdminStates.description)

@dp.message(TicketAdminStates.description)
async def admin_desc(message: Message, state: FSMContext):
    await state.update_data(description=message.text); await message.answer("От кого:"); await state.set_state(TicketAdminStates.from_nick)

@dp.message(TicketAdminStates.from_nick)
async def admin_from(message: Message, state: FSMContext):
    await state.update_data(from_nick=message.text); await message.answer("Скриншот:"); await state.set_state(TicketAdminStates.photo)

@dp.message(TicketAdminStates.photo)
async def admin_photo(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    photo_id = message.photo[-1].file_id if message.photo else "нет"
    content = f"👮 Жалоба на админа\nНик: {data['admin_nick']}\nОписание: {data['description']}\nОт: {data['from_nick']}"
    await db_execute("INSERT INTO tickets (user_id, type, content) VALUES (?, 'admin', ?)", (message.from_user.id, content))
    for admin_id in await get_admin_ids():
        if await is_leader(admin_id):
            try:
                if photo_id != "нет": await bot.send_photo(admin_id, photo_id, caption=content)
                else: await bot.send_message(admin_id, content)
            except: pass
    await message.answer("Отправлено.", reply_markup=main_keyboard()); await state.clear()

@dp.callback_query(F.data == "ticket_faq")
async def faq(callback: CallbackQuery):
    await callback.message.edit_text("❓ FAQ:\n1. Как повысить ELO? – Играть.\n2. Верификация – требования в 5 пункте.\n3. Правила – в регламенте.", reply_markup=back_to_menu())

@dp.callback_query(F.data == "ticket_verify")
async def verify(callback: CallbackQuery):
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Канал с информацией", url=VERIFY_CHANNEL)],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")]
    ])
    text = ("Для получения верификации необходимо соответствовать требованиям:\n\n"
            "📺 YouTube: 500+ подписчиков, 800+ просмотров, 2 видео в неделю.\n"
            "📱 TikTok: 500+ сабов, 500+ просмотров, 1 видео в неделю.\n\n"
            "Подробная информация в канале:")
    await callback.message.edit_text(text, reply_markup=markup)

# --- МАГАЗИН ---
@dp.message(F.text == "🛒 Магазин")
async def shop(message: Message):
    await message.answer("Магазин:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎨 Раздел профиля", callback_data="shop_profile")],
        [InlineKeyboardButton(text="🛍 Прочие товары", callback_data="shop_other")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")]
    ]))

@dp.callback_query(F.data == "shop_profile")
async def shop_profile(callback: CallbackQuery):
    await callback.message.edit_text("Товары для профиля:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Анимированная рамка", callback_data="buy_frame")],
        [InlineKeyboardButton(text="Анимированный баннер", callback_data="buy_banner")],
        [InlineKeyboardButton(text="Цветной ник", callback_data="buy_color_nick")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")]
    ]))

@dp.callback_query(F.data.startswith("buy_"))
async def buy_item(callback: CallbackQuery):
    item = callback.data[4:]
    await callback.answer(f"Покупка '{item}' через {SHOP_BOT}.", show_alert=True)
    await callback.message.answer("Регламент: покупайте в @hp404shopbot", reply_markup=back_to_menu())

@dp.callback_query(F.data == "shop_other")
async def shop_other(callback: CallbackQuery):
    await callback.message.edit_text(f"Прочие товары (разбан, анмут, премиум) в {SHOP_BOT}", reply_markup=back_to_menu())

# --- ТОП ---
@dp.message(F.text == "🏆 Топ игроков FACEIT")
async def top_players(message: Message):
    top5 = await db_fetchall("SELECT user_id, nickname, elo FROM users ORDER BY elo DESC LIMIT 5")
    medals = ["🥇","🥈","🥉","🏆","🏆"]
    text = "🏆 Топ FACEIT:\n"
    for i, row in enumerate(top5):
        text += f"{medals[i]} {row['nickname']} : {row['elo']} ELO | TOP {i+1}\n"
    text += "————————————————\n"
    elo, rank = await get_elo_rank(message.from_user.id)
    user = await db_fetchone("SELECT nickname FROM users WHERE user_id=?", (message.from_user.id,))
    if user:
        text += f"🔎 Твоё место: #{rank}\n🪪 {user['nickname']}\n🔫 ELO: {elo}"
    else:
        text += "Ты не зарегистрирован."
    await message.answer(text)

@dp.message(F.text == "📰 Новости")
async def news(message: Message): await message.answer(f"Новости: {NEWS_CHANNEL}")

@dp.message(F.text == "💬 Чат проекта")
async def chat(message: Message): await message.answer(f"Чат: {CHAT_LINK}")

@dp.message(F.text == "📜 Регламент проекта")
async def reglament(message: Message):
    text = ("📜 РЕГЛАМЕНТ «404HP FACEIT»\n\n"
            "1.1 Стороннее ПО – бан.\n1.2 Запрос СС МС – обязателен.\n1.3 Додж скрина – бан 3ч.\n1.4 ПК/ноутбуки – навсегда.\n1.5 Запись экрана – обязательна.\n"
            "2.1 Оскорбления – бан.\n2.2 Жалобы на админов – через тикет.\n2.3 Выход из матча – бан 5ч.\n2.4 Руина – бан.\n2.5 Провокации – бан 1ч.\n2.6 Оскорбление религии – вплоть до навсегда.")
    await message.answer(text)

# --- АДМИН-ПАНЕЛЬ ---
@dp.message(F.text == "🛠 Админ-панель")
async def admin_panel(message: Message):
    if not await is_admin(message.from_user.id): await message.answer("Нет прав."); return
    await message.answer("Админ-панель", reply_markup=admin_panel_keyboard())

async def _ask_nickname_for(message: Message, state: FSMContext, action: str):
    await state.update_data(action=action); await message.answer("Введи ник игрока:"); await state.set_state(AdminNickInput.waiting_nickname)

async def _process_nickname(message: Message, state: FSMContext, bot: Bot):
    action = (await state.get_data()).get('action')
    nickname = message.text.strip()
    user_id = await find_user_by_nickname(nickname)
    if not user_id: await message.answer(f"Игрок с ником '{nickname}' не найден."); await state.clear(); return
    await state.update_data(user_id=user_id)
    if action == "give_premium":
        await message.answer("Выберите срок премиума:", reply_markup=premium_duration_keyboard())
        await state.set_state(AdminSelectPremiumDuration.waiting_selection)
    elif action == "remove_premium":
        await _remove_premium(message, state, bot, user_id)
    elif action == "give_verify":
        await _give_verify(message, state, bot, user_id)
    elif action == "remove_verify":
        await _remove_verify(message, state, bot, user_id)
    elif action == "ban":
        await message.answer("Выберите срок бана:", reply_markup=ban_duration_keyboard())
        await state.set_state(AdminSelectBanDuration.waiting_selection)
    elif action == "unban":
        await _unban(message, state, bot, user_id)
    elif action == "add_admin":
        await _add_admin(message, state, bot, user_id)
    elif action == "remove_admin":
        await _remove_admin(message, state, bot, user_id)
    elif action == "lobby_ban":
        await _lobby_ban(message, state, bot, user_id)
    elif action == "lobby_unban":
        await _lobby_unban(message, state, bot, user_id)

# --- АДМИНСКИЕ ФУНКЦИИ ---
async def _remove_premium(message: Message, state: FSMContext, bot: Bot, user_id: int):
    await db_execute("UPDATE users SET premium=0, premium_until=NULL WHERE user_id=?", (user_id,))
    await message.answer(f"✅ Премиум снят.", reply_markup=main_keyboard())
    try: await bot.send_message(user_id, "💔 Премиум снят.")
    except: pass
    await state.clear()

async def _give_verify(message: Message, state: FSMContext, bot: Bot, user_id: int):
    await db_execute("UPDATE users SET verified=1 WHERE user_id=?", (user_id,))
    await message.answer(f"✅ Верификация выдана.", reply_markup=main_keyboard())
    try: await bot.send_message(user_id, "✅ Вы верифицированы!")
    except: pass
    await state.clear()

async def _remove_verify(message: Message, state: FSMContext, bot: Bot, user_id: int):
    await db_execute("UPDATE users SET verified=0 WHERE user_id=?", (user_id,))
    await message.answer(f"✅ Верификация снята.", reply_markup=main_keyboard())
    try: await bot.send_message(user_id, "❌ Верификация снята.")
    except: pass
    await state.clear()

async def _unban(message: Message, state: FSMContext, bot: Bot, user_id: int):
    await db_execute("DELETE FROM bans WHERE user_id=?", (user_id,))
    await message.answer(f"✅ Разбанен.", reply_markup=main_keyboard())
    try: await bot.send_message(user_id, "🔓 Вы разбанены.")
    except: pass
    await state.clear()

async def _add_admin(message: Message, state: FSMContext, bot: Bot, user_id: int):
    await db_execute("INSERT OR REPLACE INTO admins VALUES (?, 'admin')", (user_id,))
    await message.answer(f"✅ Администратор назначен.", reply_markup=main_keyboard())
    try: await bot.send_message(user_id, "🛡️ Вы стали администратором.")
    except: pass
    await state.clear()

async def _remove_admin(message: Message, state: FSMContext, bot: Bot, user_id: int):
    if user_id == message.from_user.id: await message.answer("Нельзя удалить себя."); await state.clear(); return
    row = await db_fetchone("SELECT role FROM admins WHERE user_id=?", (user_id,))
    if row and row['role'] == 'leader': await message.answer("Нельзя удалить руководителя."); await state.clear(); return
    await db_execute("DELETE FROM admins WHERE user_id=?", (user_id,))
    await message.answer(f"✅ Администратор снят.", reply_markup=main_keyboard())
    try: await bot.send_message(user_id, "⚠️ Вы больше не администратор.")
    except: pass
    await state.clear()

async def _lobby_ban(message: Message, state: FSMContext, bot: Bot, user_id: int):
    await db_execute("UPDATE users SET can_create_lobby=0 WHERE user_id=?", (user_id,))
    await message.answer(f"⛔ Запрещено создавать лобби.", reply_markup=main_keyboard())
    try: await bot.send_message(user_id, "⛔ Вам запретили создавать лобби.")
    except: pass
    await state.clear()

async def _lobby_unban(message: Message, state: FSMContext, bot: Bot, user_id: int):
    await db_execute("UPDATE users SET can_create_lobby=1 WHERE user_id=?", (user_id,))
    await message.answer(f"✅ Разрешено создавать лобби.", reply_markup=main_keyboard())
    try: await bot.send_message(user_id, "✅ Вам снова можно создавать лобби.")
    except: pass
    await state.clear()

# --- CALLBACKS АДМИНКИ ---
@dp.callback_query(F.data == "admin_give_premium")
async def give_premium_cb(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id): return
    await callback.message.delete(); await _ask_nickname_for(callback.message, state, "give_premium")

@dp.callback_query(F.data == "admin_remove_premium")
async def remove_premium_cb(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id): return
    await callback.message.delete(); await _ask_nickname_for(callback.message, state, "remove_premium")

@dp.callback_query(F.data == "admin_give_verify")
async def give_verify_cb(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id): return
    await callback.message.delete(); await _ask_nickname_for(callback.message, state, "give_verify")

@dp.callback_query(F.data == "admin_remove_verify")
async def remove_verify_cb(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id): return
    await callback.message.delete(); await _ask_nickname_for(callback.message, state, "remove_verify")

@dp.callback_query(F.data == "admin_ban")
async def ban_cb(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id): return
    await callback.message.delete(); await _ask_nickname_for(callback.message, state, "ban")

@dp.callback_query(F.data == "admin_unban")
async def unban_cb(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id): return
    await callback.message.delete(); await _ask_nickname_for(callback.message, state, "unban")

@dp.callback_query(F.data == "admin_add_admin")
async def add_admin_cb(callback: CallbackQuery, state: FSMContext):
    if not await is_leader(callback.from_user.id): await callback.answer("Только руководитель.", show_alert=True); return
    await callback.message.delete(); await _ask_nickname_for(callback.message, state, "add_admin")

@dp.callback_query(F.data == "admin_remove_admin")
async def remove_admin_cb(callback: CallbackQuery, state: FSMContext):
    if not await is_leader(callback.from_user.id): await callback.answer("Только руководитель.", show_alert=True); return
    await callback.message.delete(); await _ask_nickname_for(callback.message, state, "remove_admin")

@dp.callback_query(F.data == "admin_lobby_ban")
async def lobby_ban_cb(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id): return
    await callback.message.delete(); await _ask_nickname_for(callback.message, state, "lobby_ban")

@dp.callback_query(F.data == "admin_lobby_unban")
async def lobby_unban_cb(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id): return
    await callback.message.delete(); await _ask_nickname_for(callback.message, state, "lobby_unban")

@dp.message(AdminNickInput.waiting_nickname)
async def admin_nickname_handler(message: Message, state: FSMContext, bot: Bot):
    await _process_nickname(message, state, bot)

# --- ВЫБОР СРОКА БАНА ---
@dp.callback_query(AdminSelectBanDuration.waiting_selection)
async def ban_duration_selected(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = callback.data
    if data == "main_menu": await go_main_menu_cb(callback, state); return
    duration_map = {
        "ban_10m": timedelta(minutes=10), "ban_30m": timedelta(minutes=30),
        "ban_1h": timedelta(hours=1), "ban_1d": timedelta(days=1),
        "ban_1w": timedelta(weeks=1), "ban_1mo": timedelta(days=30),
        "ban_1y": timedelta(days=365), "ban_forever": "permanent"
    }
    duration = duration_map.get(data)
    if not duration: await callback.answer("Неверный выбор."); return
    until_str = "permanent" if duration == "permanent" else (datetime.now() + duration).isoformat()
    await state.update_data(ban_duration=until_str, ban_duration_label=data)
    await callback.message.edit_text("Введите причину бана:")
    await state.set_state(AdminReasonInput.waiting_reason)
    await callback.answer()

@dp.message(AdminReasonInput.waiting_reason)
async def ban_reason_entered(message: Message, state: FSMContext, bot: Bot):
    reason = message.text
    data = await state.get_data()
    user_id, until_str, label = data['user_id'], data['ban_duration'], data.get('ban_duration_label','')
    await db_execute("INSERT OR REPLACE INTO bans (user_id, reason, banned_until) VALUES (?,?,?)", (user_id, reason, until_str))
    admin_acc = await db_get_account(message.from_user.id)
    admin_nick = admin_acc['nickname'] if admin_acc else str(message.from_user.id)
    target_acc = await db_get_account(user_id)
    target_nick = target_acc['nickname'] if target_acc else str(user_id)
    duration_text = {
        "ban_10m":"10 минут","ban_30m":"30 минут","ban_1h":"1 час","ban_1d":"1 день",
        "ban_1w":"1 неделя","ban_1mo":"1 месяц","ban_1y":"1 год","ban_forever":"Навсегда"
    }.get(label, until_str)
    channel_post = (f"❌ Забанен игрок\n————————\n🛡️ Администратор: {admin_nick}\n⛓️ Забанил: {target_nick}\nℹ️ Причина: {reason}\n🕓 Время бана: {duration_text}\n————————\n🎮 404hp FACEIT | @faceit404hpbot")
    try: await bot.send_message(chat_id=CHANNEL_USERNAME, text=channel_post)
    except: pass
    await message.answer(f"✅ Игрок {user_id} забанен до {until_str}.", reply_markup=main_keyboard())
    try: await bot.send_message(user_id, f"🚫 Вы забанены. Причина: {reason}. Срок: {duration_text}")
    except: pass
    await state.clear()

# --- ВЫБОР СРОКА ПРЕМИУМА ---
@dp.callback_query(AdminSelectPremiumDuration.waiting_selection)
async def premium_duration_selected(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = callback.data
    if data == "main_menu": await go_main_menu_cb(callback, state); return
    duration_map = {"prem_1mo": timedelta(days=30), "prem_1y": timedelta(days=365)}
    duration = duration_map.get(data)
    if not duration: await callback.answer("Неверный выбор."); return
    until = datetime.now() + duration
    user_id = (await state.get_data())['user_id']
    await db_execute("UPDATE users SET premium=1, premium_until=? WHERE user_id=?", (until.isoformat(), user_id))
    await callback.message.edit_text(f"✅ Премиум выдан пользователю {user_id} до {until.strftime('%d.%m.%Y')}.")
    try: await bot.send_message(user_id, "🎉 Вам выдан премиум!")
    except: pass
    await state.clear()

# --- ОСТАЛЬНЫЕ АДМИНСКИЕ CALLBACKS (тест, тикеты) ---
@dp.callback_query(F.data == "admin_test_shuffle")
async def test_shuffle_cb(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id): return
    await callback.message.delete(); await callback.message.answer("Введи ID лобби:"); await state.set_state(AdminTestShuffle.waiting_lobby_id)

@dp.message(AdminTestShuffle.waiting_lobby_id)
async def test_shuffle_process(message: Message, state: FSMContext, bot: Bot):
    lid = int(message.text)
    lobby = await db_fetchone("SELECT host_id, format, map FROM lobbies WHERE id=?", (lid,))
    if not lobby: await message.answer("Лобби не найдено."); await state.clear(); return
    players = [r['user_id'] for r in await db_fetchall("SELECT user_id FROM lobby_players WHERE lobby_id=?",(lid,))]
    if len(players) < 2: await message.answer("Мало игроков."); await state.clear(); return
    random.shuffle(players); half = len(players)//2
    for u in players[:half]: await db_execute("UPDATE lobby_players SET team=1 WHERE lobby_id=? AND user_id=?",(lid,u))
    for u in players[half:]: await db_execute("UPDATE lobby_players SET team=2 WHERE lobby_id=? AND user_id=?",(lid,u))
    await db_execute("UPDATE lobbies SET status='in_progress' WHERE id=?",(lid,))
    teams = {u: "CT" for u in players[:half]}; teams.update({u: "T" for u in players[half:]})
    try:
        img = await generate_draft_image({"teams":teams}, bot)
        if img:
            path = save_image_temp(img)
            await message.answer_photo(FSInputFile(path), caption="Тестовая жеребьёвка")
            os.unlink(path)
    except: pass
    await message.answer("✅ Готово.", reply_markup=main_keyboard()); await state.clear()

@dp.callback_query(F.data == "admin_review_ticket")
async def review_tickets_cb(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id): return
    tickets = await db_fetchall("SELECT id, user_id, type, content FROM tickets WHERE status='open' LIMIT 10")
    if not tickets: await callback.message.edit_text("Нет открытых тикетов.", reply_markup=back_to_menu()); return
    text = "📋 Открытые тикеты:\n"
    for t in tickets: text += f"#{t['id']} от {t['user_id']} ({t['type']}): {t['content'][:100]}...\n\n"
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Закрыть тикет", callback_data="admin_close_ticket")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel_back")]
    ]))

@dp.callback_query(F.data == "admin_close_ticket")
async def close_ticket_cb(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Введи номер тикета:"); await state.set_state(AdminTicketReview.waiting_ticket_id)

@dp.message(AdminTicketReview.waiting_ticket_id)
async def close_ticket_process(message: Message, state: FSMContext):
    if not message.text.isdigit(): await message.answer("Число."); return
    await db_execute("UPDATE tickets SET status='closed' WHERE id=?", (int(message.text),))
    await message.answer("✅ Тикет закрыт.", reply_markup=main_keyboard()); await state.clear()

@dp.callback_query(F.data == "admin_panel_back")
async def back_admin_cb(callback: CallbackQuery): await callback.message.edit_text("Админ-панель", reply_markup=admin_panel_keyboard())

@dp.callback_query(F.data == "main_menu")
async def go_main_menu_cb(callback: CallbackQuery, state: FSMContext = None):
    await callback.message.delete()
    await callback.message.answer("Главное меню", reply_markup=main_keyboard())
    if state: await state.clear()

# ==================== ЗАПУСК ====================
async def main():
    init_db()
    bot = Bot(
        token=BOT_TOKEN,
        session=AiohttpSession(),
        request_timeout=30
    )
    await dp.start_polling(bot, polling_timeout=30, handle_as_tasks=False)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())

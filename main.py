import asyncio
import logging
import random
import sqlite3
import os
import tempfile
import traceback
import hashlib
from datetime import datetime, timedelta
from io import BytesIO
from difflib import SequenceMatcher

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
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

def init_db():
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            nickname TEXT UNIQUE,
            game_id TEXT,
            password_hash TEXT,
            is_logged_in INTEGER DEFAULT 0,
            elo_5x5 INTEGER DEFAULT 1000,
            elo_2x2 INTEGER DEFAULT 1000,
            elo_1x1 INTEGER DEFAULT 1000,
            can_create_lobby INTEGER DEFAULT 1,
            premium_until TEXT,
            premium INTEGER DEFAULT 0,
            verified INTEGER DEFAULT 0,
            matches_played INTEGER DEFAULT 0,
            kills INTEGER DEFAULT 0,
            deaths INTEGER DEFAULT 0,
            avatar_file_id TEXT
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
    conn.commit()
    conn.close()

async def run_migrations():
    migrations = [
        "ALTER TABLE users ADD COLUMN is_logged_in INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN premium_until TEXT",
        "ALTER TABLE users ADD COLUMN premium INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN verified INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN matches_played INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN kills INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN deaths INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN avatar_file_id TEXT",
    ]
    for sql in migrations:
        try:
            await db_execute(sql)
        except:
            pass

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

# ==================== ХЕШ ПАРОЛЯ ====================
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
async def is_nickname_similar(new_nick: str, exclude_user_id: int = None) -> bool:
    rows = await db_fetchall("SELECT nickname, user_id FROM users")
    for row in rows:
        if exclude_user_id and row['user_id'] == exclude_user_id:
            continue
        existing = row['nickname'].lower()
        new = new_nick.lower()
        if existing == new:
            return True
        if abs(len(existing) - len(new)) > 3:
            continue
        if SequenceMatcher(None, existing, new).ratio() > 0.8:
            return True
    return False

async def check_subscription(bot: Bot, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(CHANNEL_USERNAME, user_id)
        return member.status not in ['left', 'kicked']
    except:
        return False

async def get_total_elo(user_id: int) -> int:
    row = await db_fetchone("SELECT elo_5x5, elo_2x2, elo_1x1 FROM users WHERE user_id = ?", (user_id,))
    return (row['elo_5x5'] + row['elo_2x2'] + row['elo_1x1']) if row else 0

async def get_elo_rank(user_id: int) -> tuple:
    total = await get_total_elo(user_id)
    row = await db_fetchone("SELECT COUNT(*) as cnt FROM users WHERE (elo_5x5+elo_2x2+elo_1x1) > ?", (total,))
    rank = row['cnt'] + 1 if row else 1
    return total, rank

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
    return await db_fetchone("SELECT nickname, game_id, password_hash, is_logged_in FROM users WHERE user_id=?", (user_id,))

async def db_get_player(user_id: int) -> dict | None:
    return await db_fetchone("SELECT * FROM users WHERE user_id=?", (user_id,))

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

    elo_5 = player.get('elo_5x5', 1000)
    elo_2 = player.get('elo_2x2', 1000)
    elo_1 = player.get('elo_1x1', 1000)

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
                <p class="text-gray-400">ID: {acc['game_id']}</p>
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
            <div class="mt-6">
                <h3 class="text-xl font-bold mb-2">🏆 Рейтинг по режимам</h3>
                <p>5x5: {elo_5} ELO</p>
                <p>2x2: {elo_2} ELO</p>
                <p>1x1: {elo_1} ELO</p>
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
            total_elo = player['elo_5x5']+player['elo_2x2']+player['elo_1x1'] if player else '—'
            players.append({"name": acc['nickname'], "id": acc['game_id'], "role": role, "elo": total_elo})
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
        total_elo = player['elo_5x5']+player['elo_2x2']+player['elo_1x1'] if player else 0
        pinfo = {"name": acc['nickname'], "matches": player.get('matches_played',0) if player else 0,
                 "kd": player['kills']/max(1,player['deaths']) if player else 0}
        if side == "CT":
            ct_players.append(pinfo); ct_elo += total_elo
        else:
            t_players.append(pinfo); t_elo += total_elo
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
class AuthStates(StatesGroup):
    waiting_for_choice = State()
    waiting_for_nickname_reg = State()
    waiting_for_game_id = State()
    waiting_for_password_reg = State()
    waiting_for_login_nick = State()
    waiting_for_login_password = State()

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
              "📜 Регламент проекта","🛠 Админ-панель"]:
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

# ==================== ОБРАБОТЧИКИ АВТОРИЗАЦИИ ====================
@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext, bot: Bot):
    user_id = message.from_user.id
    if await is_banned(user_id):
        await message.answer("Вы забанены.")
        return
    if not await check_subscription(bot, user_id):
        await message.answer("Для доступа подпишитесь на @testhp404bot", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Подписаться", url=f"https://t.me/{CHANNEL_USERNAME[1:]}")],
            [InlineKeyboardButton(text="🔎 Проверить", callback_data="check_sub")]
        ]))
        return

    user = await db_fetchone("SELECT nickname, is_logged_in, password_hash FROM users WHERE user_id = ?", (user_id,))
    if user and user['is_logged_in']:
        await message.answer(f"✊ Добро пожаловать обратно, {user['nickname']}!", reply_markup=main_keyboard())
        return

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🆕 Зарегистрироваться", callback_data="auth_register")],
        [InlineKeyboardButton(text="🔑 Войти", callback_data="auth_login")]
    ])
    await message.answer("Добро пожаловать! Выберите действие:", reply_markup=markup)
    await state.set_state(AuthStates.waiting_for_choice)

@dp.callback_query(AuthStates.waiting_for_choice, F.data == "auth_register")
async def auth_register(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Введите желаемый ник:")
    await state.set_state(AuthStates.waiting_for_nickname_reg)

@dp.callback_query(AuthStates.waiting_for_choice, F.data == "auth_login")
async def auth_login(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Введите ваш ник:")
    await state.set_state(AuthStates.waiting_for_login_nick)

@dp.message(AuthStates.waiting_for_nickname_reg)
async def process_reg_nick(message: Message, state: FSMContext):
    nick = message.text.strip()
    if len(nick) < 3:
        await message.answer("Ник должен быть не менее 3 символов.")
        return
    if await is_nickname_similar(nick):
        await message.answer("Этот ник (или похожий) уже занят.")
        return
    await state.update_data(reg_nick=nick)
    await message.answer("Введите игровой ID (число):")
    await state.set_state(AuthStates.waiting_for_game_id)

@dp.message(AuthStates.waiting_for_game_id)
async def process_reg_game_id(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("ID должен быть числом.")
        return
    game_id = message.text.strip()
    if len(game_id) < 5:
        await message.answer("ID должен содержать минимум 5 цифр.")
        return
    await state.update_data(reg_game_id=game_id)
    await message.answer("Придумайте пароль:")
    await state.set_state(AuthStates.waiting_for_password_reg)

@dp.message(AuthStates.waiting_for_password_reg)
async def process_reg_password(message: Message, state: FSMContext):
    password = message.text.strip()
    if len(password) < 4:
        await message.answer("Пароль должен быть не менее 4 символов.")
        return
    data = await state.get_data()
    nick = data['reg_nick']
    game_id = data['reg_game_id']
    pass_hash = hash_password(password)
    await db_execute(
        "INSERT OR REPLACE INTO users (user_id, nickname, game_id, password_hash, is_logged_in) VALUES (?,?,?,?,1)",
        (message.from_user.id, nick, game_id, pass_hash)
    )
    await message.answer("✅ Регистрация завершена! Вы вошли в аккаунт.", reply_markup=main_keyboard())
    await state.clear()

@dp.message(AuthStates.waiting_for_login_nick)
async def process_login_nick(message: Message, state: FSMContext):
    nick = message.text.strip()
    user = await db_fetchone("SELECT user_id, password_hash FROM users WHERE nickname = ?", (nick,))
    if not user:
        await message.answer("Пользователь с таким ником не найден.")
        return
    if user['user_id'] != message.from_user.id:
        await message.answer("Этот аккаунт принадлежит другому Telegram ID.")
        return
    await state.update_data(login_user_id=user['user_id'], login_hash=user['password_hash'])
    await message.answer("Введите пароль:")
    await state.set_state(AuthStates.waiting_for_login_password)

@dp.message(AuthStates.waiting_for_login_password)
async def process_login_password(message: Message, state: FSMContext):
    data = await state.get_data()
    if data['login_hash'] != hash_password(message.text.strip()):
        await message.answer("Неверный пароль.")
        return
    await db_execute("UPDATE users SET is_logged_in = 1 WHERE user_id = ?", (data['login_user_id'],))
    user = await db_fetchone("SELECT nickname FROM users WHERE user_id = ?", (data['login_user_id'],))
    await message.answer(f"✅ Добро пожаловать, {user['nickname']}!", reply_markup=main_keyboard())
    await state.clear()

@dp.callback_query(F.data == "logout_account")
async def logout_account(callback: CallbackQuery):
    await db_execute("UPDATE users SET is_logged_in = 0 WHERE user_id = ?", (callback.from_user.id,))
    await callback.message.answer("Вы вышли из аккаунта. Для входа используйте /start.")
    await callback.answer()

# --- ПРОФИЛЬ ---
@dp.message(F.text == "👤 Профиль")
async def profile(message: Message, bot: Bot):
    user_id = message.from_user.id
    user = await db_fetchone("SELECT * FROM users WHERE user_id = ? AND is_logged_in = 1", (user_id,))
    if not user:
        await message.answer("Вы не вошли в аккаунт. Используйте /start.")
        return
    acc = {"nickname": user['nickname'], "game_id": user['game_id']}
    player = user
    try:
        img = await generate_profile_image(player, acc, bot)
        path = save_image_temp(img)
        total_elo, rank = await get_elo_rank(user_id)
        kd = player['kills'] / max(1, player['deaths'])
        premium = await is_premium(user_id)
        verified = await is_verified(user_id)
        caption = (f"🪪 {acc['nickname']}\n🔗 ID: {acc['game_id']}\n🔫 K/D: {kd:.2f}\n🏆 Общий рейтинг: #{rank}\n"
                   f"⭐ Premium: {'✅' if premium else '❌'}\n✅ Верификация: {'✅' if verified else '❌'}\n"
                   f"🎮 5x5: {player['elo_5x5']} | 2x2: {player['elo_2x2']} | 1x1: {player['elo_1x1']}")
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🖼 Установить аватар", callback_data="upload_avatar")],
            [InlineKeyboardButton(text="🚪 Выйти из аккаунта", callback_data="logout_account")]
        ])
        await message.answer_photo(FSInputFile(path), caption=caption, reply_markup=markup)
        os.unlink(path)
    except Exception as e:
        logging.error(f"Ошибка профиля: {e}")
        await message.answer("Ошибка создания карточки.")

# --- ЛОББИ ---
@dp.message(F.text == "➕ Создать матч")
async def create_match(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if not await db_fetchone("SELECT is_logged_in FROM users WHERE user_id = ? AND is_logged_in = 1", (user_id,)):
        await message.answer("Вы не вошли в аккаунт. Используйте /start.")
        return
    if not await can_create_lobby(user_id):
        await message.answer("⛔ Вам запрещено создавать лобби.")
        return
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

# --- РЕЗУЛЬТАТЫ (с двойным ELO для премиума) ---
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
    await state.update_data(lobby_id=lid, host_id=lobby['host_id'], map_name=lobby['map'], format=lobby['format'])
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
    lid, ct_score, t_score, screenshot, host_id, map_name, fmt = data['lobby_id'], data['ct_score'], data['t_score'], data['screenshot'], data['host_id'], data['map_name'], data['format']
    winner = "CT" if ct_score > t_score else "T"
    winner_team = 1 if winner == "CT" else 2
    elo_field = f"elo_{fmt}"
    players = await db_fetchall("SELECT user_id FROM lobby_players WHERE lobby_id=?", (lid,))
    for p in players:
        uid = p['user_id']
        player_team = (await db_fetchone("SELECT team FROM lobby_players WHERE lobby_id=? AND user_id=?", (lid, uid)))['team']
        actual_team = 2 if (player_team == 1 and swapped) or (player_team == 2 and not swapped) else player_team
        premium = await is_premium(uid)
        bonus = 50 if premium else 25
        if actual_team == winner_team:
            await db_execute(f"UPDATE users SET {elo_field}=elo_{fmt}+?, matches_played=matches_played+1 WHERE user_id=?", (bonus, uid))
        else:
            await db_execute("UPDATE users SET matches_played=matches_played+1 WHERE user_id=?", (uid,))
    ct_list, t_list = [], []
    for p in players:
        uid = p['user_id']; acc = await db_get_account(uid)
        player_team = (await db_fetchone("SELECT team FROM lobby_players WHERE lobby_id=? AND user_id=?", (lid, uid)))['team']
        actual_team = 2 if (player_team == 1 and swapped) or (player_team == 2 and not swapped) else player_team
        player = await db_get_player(uid)
        elo = player[elo_field] if player else 0
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

# --- ТИКЕТЫ, МАГАЗИН, ТОП, АДМИНКА (идентичны предыдущей версии) ---
# ... (код для тикетов, магазина, топа, админ-панели – как в предыдущем полном ответе, с проверкой is_logged_in)

# ==================== ЗАПУСК ====================
async def main():
    init_db()
    await run_migrations()
    bot = Bot(token=BOT_TOKEN, session=AiohttpSession())
    await dp.start_polling(bot, polling_timeout=30, handle_as_tasks=False)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())

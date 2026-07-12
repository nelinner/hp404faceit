import asyncio
import logging
import random
import sqlite3
import os
import hashlib
from datetime import datetime, timedelta
from difflib import SequenceMatcher

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
BOT_TOKEN = "8254209430:AAHrYF_5KJCA77-4nYpCaleisJckxUtCMLY"
CHANNEL_USERNAME = "@hp404faceit"
BOT_USERNAME = "@hp404bot"
SHOP_BOT = "@hp404shopbot"
CHAT_LINK = "https://t.me/hpfaceitchat"
NEWS_CHANNEL = "@hp404news"
LEADER_USERNAME = "nelinner"
VERIFY_CHANNEL = "https://t.me/+wdNdSgYj86A2M2Uy"
DB_NAME = "faceit.db"

MAPS = ["Dune", "Province", "Sandstone", "Hanami", "Rust", "Prison", "Breeze",
        "Bridge", "Pool", "Cableway", "Pipeline", "Village", "Arena"]

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
            elo_5x5 INTEGER DEFAULT 0,
            elo_2x2 INTEGER DEFAULT 0,
            elo_1x1 INTEGER DEFAULT 0,
            can_create_lobby INTEGER DEFAULT 0,
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
    expected_columns = {
        "user_id": "INTEGER PRIMARY KEY",
        "nickname": "TEXT UNIQUE",
        "game_id": "TEXT",
        "password_hash": "TEXT",
        "is_logged_in": "INTEGER DEFAULT 0",
        "elo_5x5": "INTEGER DEFAULT 0",
        "elo_2x2": "INTEGER DEFAULT 0",
        "elo_1x1": "INTEGER DEFAULT 0",
        "can_create_lobby": "INTEGER DEFAULT 0",
        "premium_until": "TEXT",
        "premium": "INTEGER DEFAULT 0",
        "verified": "INTEGER DEFAULT 0",
        "matches_played": "INTEGER DEFAULT 0",
        "kills": "INTEGER DEFAULT 0",
        "deaths": "INTEGER DEFAULT 0",
        "avatar_file_id": "TEXT",
    }

    try:
        existing_info = await db_fetchall("PRAGMA table_info(users)")
        existing_columns = {row['name'] for row in existing_info}
    except:
        return

    for col_name, col_def in expected_columns.items():
        if col_name not in existing_columns:
            try:
                await db_execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_def}")
                print(f"Миграция: добавлена колонка {col_name}")
            except Exception as e:
                print(f"Не удалось добавить колонку {col_name}: {e}")

    # Сброс старых ELO со 1000 на 0
    await db_execute("""
        UPDATE users SET elo_5x5 = 0, elo_2x2 = 0, elo_1x1 = 0
        WHERE elo_5x5 = 1000 AND elo_2x2 = 1000 AND elo_1x1 = 1000
    """)
    # Руководителю всегда разрешено создавать лобби
    leader = await db_fetchone("SELECT user_id FROM users WHERE nickname = ?", (LEADER_USERNAME,))
    if leader:
        await db_execute("UPDATE users SET can_create_lobby = 1 WHERE user_id = ?", (leader['user_id'],))
    print("Миграция завершена.")

async def db_execute(sql: str, params: tuple = ()):
    try:
        def _exec():
            conn = _get_conn()
            conn.execute(sql, params)
            conn.commit()
            conn.close()
        await asyncio.to_thread(_exec)
    except Exception as e:
        logging.error(f"DB execute error: {e}")

async def db_fetchone(sql: str, params: tuple = ()) -> dict | None:
    try:
        def _fetch():
            conn = _get_conn()
            row = conn.execute(sql, params).fetchone()
            conn.close()
            return dict(row) if row else None
        return await asyncio.to_thread(_fetch)
    except Exception as e:
        logging.error(f"DB fetchone error: {e}")
        return None

async def db_fetchall(sql: str, params: tuple = ()) -> list:
    try:
        def _fetch():
            conn = _get_conn()
            rows = conn.execute(sql, params).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        return await asyncio.to_thread(_fetch)
    except Exception as e:
        logging.error(f"DB fetchall error: {e}")
        return []

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
    if await is_leader(user_id):
        return True
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

async def get_leader_id() -> int | None:
    row = await db_fetchone("SELECT user_id FROM users WHERE nickname = ?", (LEADER_USERNAME,))
    return row['user_id'] if row else None

def get_level(elo: int) -> int:
    return max(1, elo // 200)

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
class AdminReplacePlayer(StatesGroup):
    waiting_lobby_id = State()
    waiting_player_index = State()
    waiting_new_nick = State()
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
        [InlineKeyboardButton(text="9. Заменить игрока", callback_data="admin_replace_player")],
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
    username = message.from_user.username

    if username and username.lower() == LEADER_USERNAME.lower():
        await db_execute("INSERT OR REPLACE INTO admins VALUES (?, 'leader')", (user_id,))
        await db_execute("UPDATE users SET can_create_lobby = 1 WHERE user_id = ?", (user_id,))

    if await is_banned(user_id):
        await message.answer("Вы забанены.")
        return
    if not await check_subscription(bot, user_id):
        await message.answer(f"Для доступа подпишитесь на {CHANNEL_USERNAME}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Подписаться", url=f"https://t.me/{CHANNEL_USERNAME[1:]}")],
            [InlineKeyboardButton(text="🔎 Проверить", callback_data="check_sub")]
        ]))
        return

    user = await db_fetchone("SELECT nickname, is_logged_in FROM users WHERE user_id = ?", (user_id,))
    if user and user['is_logged_in']:
        await message.answer(f"✊ Добро пожаловать обратно, {user['nickname']}!", reply_markup=main_keyboard())
        return

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🆕 Зарегистрироваться", callback_data="auth_register")],
        [InlineKeyboardButton(text="🔑 Войти", callback_data="auth_login")]
    ])
    await message.answer(f"Добро пожаловать в {BOT_USERNAME}! Выберите действие:", reply_markup=markup)
    await state.set_state(AuthStates.waiting_for_choice)

@dp.callback_query(F.data == "check_sub")
async def check_sub_callback(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if await check_subscription(bot, callback.from_user.id):
        await callback.message.delete()
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🆕 Зарегистрироваться", callback_data="auth_register")],
            [InlineKeyboardButton(text="🔑 Войти", callback_data="auth_login")]
        ])
        await callback.message.answer(f"Добро пожаловать в {BOT_USERNAME}! Выберите действие:", reply_markup=markup)
        await state.set_state(AuthStates.waiting_for_choice)
    else:
        await callback.answer("❌ Вы не подписаны!", show_alert=True)

# --- АВТОРИЗАЦИЯ ---
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
    if await is_nickname_similar(nick, exclude_user_id=message.from_user.id):
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
    await message.answer("✅ Регистрация завершена! Вы вошли в аккаунт.\n"
                         "⚠️ Для создания лобби необходимо получить разрешение руководителя.",
                         reply_markup=main_keyboard())
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
    total_elo, rank = await get_elo_rank(user_id)
    kd = user['kills'] / max(1, user['deaths'])
    premium = await is_premium(user_id)
    verified = await is_verified(user_id)
    lobby_perm = "✅" if await can_create_lobby(user_id) else "❌ (требуется разрешение)"
    text = (f"🪪 {user['nickname']}\n"
            f"🔗 ID: {user['game_id']}\n"
            f"🔫 K/D: {kd:.2f}\n"
            f"🏆 Общий рейтинг: #{rank}\n"
            f"⭐ Premium: {'✅' if premium else '❌'}\n"
            f"✅ Верификация: {'✅' if verified else '❌'}\n"
            f"🎮 5x5: {user['elo_5x5']} | 2x2: {user['elo_2x2']} | 1x1: {user['elo_1x1']}\n"
            f"🛠 Создание лобби: {lobby_perm}")
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🖼 Установить аватар", callback_data="upload_avatar")],
        [InlineKeyboardButton(text="🚪 Выйти из аккаунта", callback_data="logout_account")]
    ])
    await message.answer(text, reply_markup=markup)

@dp.callback_query(F.data == "upload_avatar")
async def upload_avatar_callback(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Отправьте изображение для аватара.")
    await state.set_state(AvatarUpload.waiting_photo)
    await callback.answer()

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
        await message.answer("У вас нет созданных лобби.")
        return
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
        await callback.answer("Это не ваше лобби.", show_alert=True)
        return
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
        await callback.answer("Недоступно.", show_alert=True)
        return
    if lobby['message_id']:
        try:
            await bot.delete_message(chat_id=CHANNEL_USERNAME, message_id=lobby['message_id'])
        except:
            pass
    await db_execute("DELETE FROM lobby_players WHERE lobby_id=?", (lobby_id,))
    await db_execute("DELETE FROM lobbies WHERE id=?", (lobby_id,))
    await callback.message.edit_text("✅ Лобби отменено.")
    await callback.answer("Лобби удалено.")

@dp.callback_query(F.data.startswith("result_lobby_"))
async def result_from_mylobby(callback: CallbackQuery, state: FSMContext):
    lobby_id = int(callback.data.split("_")[2])
    lobby = await db_fetchone("SELECT * FROM lobbies WHERE id=?", (lobby_id,))
    if not lobby or lobby['host_id'] != callback.from_user.id or lobby['status'] != 'in_progress':
        await callback.answer("Матч не начат или уже завершён.", show_alert=True)
        return
    await state.update_data(lobby_id=lobby_id, host_id=lobby['host_id'], map_name=lobby['map'], format=lobby['format'])
    await callback.message.answer("Пришлите скриншот результатов:")
    await state.set_state(ResultStates.waiting_screenshot)
    await callback.answer()

# --- ЛОББИ (СОЗДАНИЕ, ПРИСОЕДИНЕНИЕ, ЖЕРЕБЬЁВКА) ---
@dp.message(F.text == "➕ Создать матч")
async def create_match(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if not await db_fetchone("SELECT is_logged_in FROM users WHERE user_id = ? AND is_logged_in = 1", (user_id,)):
        await message.answer("Вы не вошли в аккаунт. Используйте /start.")
        return
    if not await can_create_lobby(user_id):
        await message.answer(
            "⛔ У вас нет разрешения на создание лобби.\n"
            "Отправьте запрос руководителю:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📩 Запросить разрешение", callback_data="request_lobby_permission")]
            ])
        )
        return
    await message.answer("Выбери формат:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="5x5", callback_data="format_5x5")],
        [InlineKeyboardButton(text="2x2", callback_data="format_2x2")],
        [InlineKeyboardButton(text="1x1", callback_data="format_1x1")]
    ]))
    await state.set_state(LobbyStates.choosing_format)

@dp.callback_query(F.data == "request_lobby_permission")
async def request_permission(callback: CallbackQuery, bot: Bot):
    user_id = callback.from_user.id
    user = await db_get_account(user_id)
    if not user:
        await callback.answer("Сначала войдите в аккаунт.")
        return
    leader_id = await get_leader_id()
    if not leader_id:
        await callback.answer("Руководитель не найден в базе.")
        return
    await bot.send_message(
        leader_id,
        f"📩 Запрос на создание лобби\n"
        f"👤 Ник: {user['nickname']}\n"
        f"🆔 ID: {user_id}\n\n"
        f"Разрешить?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve_lobby_{user_id}")],
            [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"deny_lobby_{user_id}")]
        ])
    )
    await callback.answer("Запрос отправлен руководителю.", show_alert=True)

@dp.callback_query(F.data.startswith("approve_lobby_"))
async def approve_lobby(callback: CallbackQuery, bot: Bot):
    if not await is_leader(callback.from_user.id):
        await callback.answer("Только руководитель может одобрять.")
        return
    target_id = int(callback.data.split("_")[2])
    await db_execute("UPDATE users SET can_create_lobby = 1 WHERE user_id = ?", (target_id,))
    await callback.message.edit_text(f"✅ Пользователю {target_id} разрешено создавать лобби.")
    try:
        await bot.send_message(target_id, "✅ Руководитель одобрил вам создание лобби!")
    except:
        pass

@dp.callback_query(F.data.startswith("deny_lobby_"))
async def deny_lobby(callback: CallbackQuery, bot: Bot):
    if not await is_leader(callback.from_user.id):
        await callback.answer("Только руководитель может отклонять.")
        return
    target_id = int(callback.data.split("_")[2])
    await db_execute("UPDATE users SET can_create_lobby = 0 WHERE user_id = ?", (target_id,))
    await callback.message.edit_text(f"❌ Пользователю {target_id} отказано в создании лобби.")
    try:
        await bot.send_message(target_id, "❌ Руководитель отклонил ваш запрос на создание лобби.")
    except:
        pass

@dp.callback_query(LobbyStates.choosing_format)
async def format_chosen(callback: CallbackQuery, state: FSMContext):
    fmt = callback.data.split("_")[1]
    await state.update_data(format=fmt)
    await callback.message.delete()
    maps = {"5x5": ["Dune","Sandstone","Rust","Province","Hanami","Breeze","Prison"],
            "2x2": ["Dune","Sandstone","Rust","Province","Hanami","Breeze","Prison"],
            "1x1": ["Bridge","Pool","Cableway","Pipeline","Village","Arena"]}
    builder = InlineKeyboardBuilder()
    for m in maps[fmt]:
        builder.button(text=m, callback_data=f"map_{m}")
    await callback.message.answer("Выбери карту:", reply_markup=builder.adjust(2).as_markup())
    await state.set_state(LobbyStates.choosing_map)

@dp.callback_query(LobbyStates.choosing_map)
async def map_chosen(callback: CallbackQuery, state: FSMContext):
    map_name = callback.data.split("_",1)[1]
    data = await state.get_data()
    fmt = data['format']
    guide = {
        "5x5": "1. Выберите формат «Турнир»\n2. 13 раундов\n3. Баланс до 16к",
        "2x2": "1. Формат «Союзники»\n2. 13 раундов\n3. Баланс 16к",
        "1x1": "1. Формат «Дуэли»\n2. Раунды по умолчанию"
    }
    text = f"⚙️ Настройки ({fmt})\n\n{guide[fmt]}\n\nКарта: {map_name}\nГотов создать лобби?"
    await state.update_data(map=map_name)
    await callback.message.delete()
    await callback.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Создать", callback_data="create_lobby")],
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
    await update_lobby_message(bot, lid)
    await callback.message.delete()
    await callback.message.answer(f"✅ Лобби #{lid} создано!", reply_markup=main_keyboard())
    await state.clear()

@dp.callback_query(F.data == "cancel_lobby")
async def cancel_lobby(callback: CallbackQuery, state: FSMContext):
    await callback.message.delete()
    await callback.message.answer("Создание отменено.", reply_markup=main_keyboard())
    await state.clear()

async def update_lobby_message(bot: Bot, lobby_id: int):
    lobby = await db_fetchone("SELECT message_id, format, map, host_id FROM lobbies WHERE id=?", (lobby_id,))
    if not lobby:
        return
    msg_id, fmt, map_name, host_id = lobby['message_id'], lobby['format'], lobby['map'], lobby['host_id']
    players = [r['user_id'] for r in await db_fetchall("SELECT user_id FROM lobby_players WHERE lobby_id=?", (lobby_id,))]
    host_acc = await db_get_account(host_id)
    host_name = host_acc['nickname'] if host_acc else str(host_id)

    players_list = []
    for i, uid in enumerate(players, 1):
        acc = await db_get_account(uid)
        player = await db_get_player(uid)
        if acc:
            is_admin_player = await is_admin(uid)
            role = "ADMIN" if is_admin_player else ""
            elo = player['elo_5x5'] + player['elo_2x2'] + player['elo_1x1'] if player else 0
            players_list.append(f"{i}. {acc['nickname']} {role} | {elo} ELO")

    needed = {"5x5":10,"2x2":4,"1x1":2}[fmt]
    text = (f"🔎 Лобби #{lobby_id} | {fmt} | {map_name}\n\n"
            f"👥 Список игроков ({len(players)}/{needed}):\n"
            + "\n".join(players_list) + "\n\n"
            f"Хост: {host_name}")

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✊ Присоединиться", callback_data=f"join_{lobby_id}")],
        [InlineKeyboardButton(text="🔙 Выйти", callback_data=f"leave_{lobby_id}")]
    ])
    if msg_id:
        try:
            await bot.edit_message_text(text, chat_id=CHANNEL_USERNAME, message_id=msg_id, reply_markup=markup)
        except:
            pass
    else:
        msg = await bot.send_message(chat_id=CHANNEL_USERNAME, text=text, reply_markup=markup)
        await db_execute("UPDATE lobbies SET message_id=? WHERE id=?", (msg.message_id, lobby_id))

@dp.callback_query(F.data.startswith("join_"))
async def join_lobby(callback: CallbackQuery, bot: Bot):
    lid = int(callback.data.split("_")[1])
    uid = callback.from_user.id
    # Проверка авторизации
    if not await db_fetchone("SELECT is_logged_in FROM users WHERE user_id = ? AND is_logged_in = 1", (uid,)):
        await callback.answer("Сначала войдите в аккаунт через /start", show_alert=True)
        return
    lobby = await db_fetchone("SELECT format, status FROM lobbies WHERE id=?", (lid,))
    if not lobby or lobby['status'] != 'open':
        await callback.answer("Лобби закрыто.", show_alert=True)
        return
    needed = {"5x5":10,"2x2":4,"1x1":2}[lobby['format']]
    if await db_fetchone("SELECT * FROM lobby_players WHERE lobby_id=? AND user_id=?", (lid, uid)):
        await callback.answer("Уже в лобби.", show_alert=True)
        return
    count = (await db_fetchone("SELECT COUNT(*) as cnt FROM lobby_players WHERE lobby_id=?", (lid,)))['cnt']
    if count >= needed:
        await callback.answer("Заполнено.", show_alert=True)
        return
    await db_execute("INSERT INTO lobby_players VALUES (?,?,0)", (lid, uid))
    await update_lobby_message(bot, lid)
    if count+1 == needed:
        host = (await db_fetchone("SELECT host_id FROM lobbies WHERE id=?", (lid,)))['host_id']
        try:
            await bot.send_message(host, f"Лобби #{lid} заполнено! Жеребьёвка.", reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🔄 Жеребьёвка", callback_data=f"shuffle_{lid}")]]))
        except:
            pass
    await callback.answer("Присоединился!")

@dp.callback_query(F.data.startswith("leave_"))
async def leave_lobby(callback: CallbackQuery, bot: Bot):
    lid = int(callback.data.split("_")[1])
    await db_execute("DELETE FROM lobby_players WHERE lobby_id=? AND user_id=?", (lid, callback.from_user.id))
    await update_lobby_message(bot, lid)
    await callback.answer("Вышел.")

@dp.callback_query(F.data.startswith("shuffle_"))
async def shuffle_lobby(callback: CallbackQuery, bot: Bot):
    lid = int(callback.data.split("_")[1])
    lobby = await db_fetchone("SELECT host_id, format, map, message_id FROM lobbies WHERE id=?", (lid,))
    if not lobby or lobby['host_id'] != callback.from_user.id:
        await callback.answer("Только хост.", show_alert=True)
        return
    players = [r['user_id'] for r in await db_fetchall("SELECT user_id FROM lobby_players WHERE lobby_id=?", (lid,))]
    random.shuffle(players)
    half = len(players)//2
    for u in players[:half]:
        await db_execute("UPDATE lobby_players SET team=1 WHERE lobby_id=? AND user_id=?", (lid, u))
    for u in players[half:]:
        await db_execute("UPDATE lobby_players SET team=2 WHERE lobby_id=? AND user_id=?", (lid, u))
    await db_execute("UPDATE lobbies SET status='in_progress' WHERE id=?", (lid,))

    ct_list = [f"👤 {(await db_get_account(u))['nickname']}" if (await db_get_account(u)) else f"ID {u}" for u in players[:half]]
    t_list = [f"👤 {(await db_get_account(u))['nickname']}" if (await db_get_account(u)) else f"ID {u}" for u in players[half:]]

    text = (f"⚔️ Жеребьёвка лобби #{lid}\n\n🔵 CT:\n" + "\n".join(ct_list) + "\n\n🔴 T:\n" + "\n".join(t_list))
    if lobby['message_id']:
        try:
            await bot.delete_message(chat_id=CHANNEL_USERNAME, message_id=lobby['message_id'])
        except:
            pass
    msg = await bot.send_message(chat_id=CHANNEL_USERNAME, text=text)
    await db_execute("UPDATE lobbies SET message_id=? WHERE id=?", (msg.message_id, lid))
    for u in players:
        try:
            team = "защите" if u in players[:half] else "атаке"
            await bot.send_message(u, f"Лобби #{lid}: вы в {team}. Игра началась!")
        except:
            pass
    await callback.answer("Жеребьёвка проведена!")

@dp.message(F.text == "🔍 Найти матч")
async def find_match(message: Message):
    lobbies = await db_fetchall("SELECT id, format, map FROM lobbies WHERE status='open' ORDER BY created_at DESC LIMIT 10")
    if not lobbies:
        await message.answer("Нет открытых лобби.")
        return
    for lobby in lobbies:
        lid, fmt, mn = lobby['id'], lobby['format'], lobby['map']
        count = (await db_fetchone("SELECT COUNT(*) as cnt FROM lobby_players WHERE lobby_id=?", (lid,)))['cnt']
        needed = {"5x5":10,"2x2":4,"1x1":2}.get(fmt,10)
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✊ Присоединиться", callback_data=f"join_{lid}")],
            [InlineKeyboardButton(text="🔙 Выйти", callback_data=f"leave_{lid}")]
        ])
        await message.answer(f"Лобби #{lid} ({fmt}) {mn}\n{count}/{needed}", reply_markup=markup)

# --- РЕЗУЛЬТАТЫ ---
@dp.message(Command("results"))
async def results_start(message: Message, state: FSMContext):
    await message.answer("Введите номер лобби:")
    await state.set_state(ResultStates.waiting_lobby_id)

@dp.message(ResultStates.waiting_lobby_id)
async def results_lobby_id(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("Номер лобби должен быть числом.")
        return
    lid = int(message.text)
    lobby = await db_fetchone("SELECT * FROM lobbies WHERE id=?", (lid,))
    if not lobby:
        await message.answer("Лобби не найдено.")
        return
    if lobby['status'] != 'in_progress':
        await message.answer("Матч не начат или завершён.")
        return
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
    if len(parts) < 2:
        await message.answer("Неверный формат. Введите: CT T")
        return
    try:
        ct_score = int(parts[0])
        t_score = int(parts[1])
    except:
        await message.answer("Счёт должен быть числом.")
        return
    await state.update_data(ct_score=ct_score, t_score=t_score)
    await message.answer("🔄 Команды поменялись сторонами?",
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                             [InlineKeyboardButton(text="✅ Да", callback_data="swap_yes")],
                             [InlineKeyboardButton(text="❌ Нет", callback_data="swap_no")]
                         ]))
    await state.set_state(ResultStates.confirm_swap)

@dp.callback_query(ResultStates.confirm_swap, F.data.in_(["swap_yes", "swap_no"]))
async def results_swap(callback: CallbackQuery, state: FSMContext, bot: Bot):
    swapped = callback.data == "swap_yes"
    data = await state.get_data()
    lid = data['lobby_id']
    ct_score = data['ct_score']
    t_score = data['t_score']
    screenshot = data['screenshot']
    host_id = data['host_id']
    map_name = data['map_name']
    fmt = data['format']

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
        uid = p['user_id']
        acc = await db_get_account(uid)
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
    result_text = (f"📊 РЕЗУЛЬТАТ МАТЧА\nЛобби #{lid} | host: {host_name}\n"
                   f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                   f"🗺 {map_name}\n\n"
                   f"{'🔄 Команды поменялись сторонами' if swapped else ''}\n\n"
                   f"🔵 CT: {ct_score}\n" + "\n".join(ct_list) + "\n\n"
                   f"🔴 T: {t_score}\n" + "\n".join(t_list) + "\n\n"
                   f"🏆 Победитель: {winner}\n📸 Скриншот прилагается")
    try:
        await bot.send_photo(chat_id=CHANNEL_USERNAME, photo=screenshot, caption=result_text)
    except:
        await bot.send_message(chat_id=CHANNEL_USERNAME, text=result_text)

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
    await state.update_data(from_nick=message.text); await message.answer("Прикрепи скриншот (или '-'):"); await state.set_state(TicketPlayerStates.photo)

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
    top5 = await db_fetchall("SELECT user_id, nickname, elo_5x5, elo_2x2, elo_1x1 FROM users ORDER BY (elo_5x5+elo_2x2+elo_1x1) DESC LIMIT 5")
    medals = ["🥇","🥈","🥉","🏆","🏆"]
    text = "🏆 Топ FACEIT:\n"
    for i, row in enumerate(top5):
        total = row['elo_5x5'] + row['elo_2x2'] + row['elo_1x1']
        text += f"{medals[i]} {row['nickname']} : {total} ELO | TOP {i+1}\n"
    text += "————————————————\n"
    total_elo, rank = await get_elo_rank(message.from_user.id)
    user = await db_fetchone("SELECT nickname FROM users WHERE user_id=?", (message.from_user.id,))
    if user:
        text += f"🔎 Твоё место: #{rank}\n🪪 {user['nickname']}\n🔫 ELO: {total_elo}"
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
    channel_post = (f"❌ Забанен игрок\n————————\n🛡️ Администратор: {admin_nick}\n⛓️ Забанил: {target_nick}\nℹ️ Причина: {reason}\n🕓 Время бана: {duration_text}\n————————\n🎮 404hp FACEIT | {BOT_USERNAME}")
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

# --- ЗАМЕНА ИГРОКА ---
@dp.callback_query(F.data == "admin_replace_player")
async def replace_player_start(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id): return
    await callback.message.delete()
    lobbies = await db_fetchall("SELECT id, format, map FROM lobbies WHERE status='open' OR status='in_progress' ORDER BY created_at DESC")
    if not lobbies:
        await callback.message.answer("Нет активных лобби.")
        return
    builder = InlineKeyboardBuilder()
    for lobby in lobbies:
        builder.button(text=f"Лобби #{lobby['id']} ({lobby['format']}) {lobby['map']}", callback_data=f"replobby_{lobby['id']}")
    builder.button(text="🔙 Назад", callback_data="main_menu")
    builder.adjust(1)
    await callback.message.answer("Выберите лобби:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("replobby_"))
async def replace_lobby_selected(callback: CallbackQuery, state: FSMContext):
    lobby_id = int(callback.data.split("_")[1])
    await state.update_data(replace_lobby_id=lobby_id)
    players = await db_fetchall("SELECT user_id, team FROM lobby_players WHERE lobby_id=?", (lobby_id,))
    ct_players, t_players, ct_ids, t_ids = [], [], [], []
    for p in players:
        acc = await db_get_account(p['user_id'])
        name = acc['nickname'] if acc else str(p['user_id'])
        if p['team'] == 1:
            ct_players.append(name); ct_ids.append(p['user_id'])
        else:
            t_players.append(name); t_ids.append(p['user_id'])

    text = "Выберите игрока для замены:\n\n"
    builder = InlineKeyboardBuilder()
    if ct_players:
        text += "🔵 CT:\n"
        for i, name in enumerate(ct_players, 1): text += f"{i}. {name}\n"
    if t_players:
        text += "\n🔴 T:\n"
        for i, name in enumerate(t_players, 1): text += f"{i}. {name}\n"
    for i, uid in enumerate(ct_ids):
        builder.button(text=f"CT {i+1}", callback_data=f"replace_{lobby_id}_{uid}")
    for i, uid in enumerate(t_ids):
        builder.button(text=f"T {i+1}", callback_data=f"replace_{lobby_id}_{uid}")
    builder.button(text="🔙 Назад", callback_data="main_menu")
    builder.adjust(3)
    await state.update_data(replace_players={p['user_id']: p['team'] for p in players})
    await callback.message.edit_text(text, reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("replace_"))
async def replace_player_old_selected(callback: CallbackQuery, state: FSMContext):
    _, lobby_id, old_uid = callback.data.split("_")
    lobby_id = int(lobby_id); old_uid = int(old_uid)
    await state.update_data(replace_old_uid=old_uid, replace_lobby_id=lobby_id)
    await callback.message.edit_text("Введите ник нового игрока:")
    await state.set_state(AdminReplacePlayer.waiting_new_nick)

@dp.message(AdminReplacePlayer.waiting_new_nick)
async def replace_new_nick(message: Message, state: FSMContext, bot: Bot):
    new_nick = message.text.strip()
    new_uid = await find_user_by_nickname(new_nick)
    if not new_uid:
        await message.answer("Игрок с таким ником не найден.")
        return
    data = await state.get_data()
    lobby_id = data['replace_lobby_id']
    old_uid = data['replace_old_uid']
    old = await db_fetchone("SELECT team FROM lobby_players WHERE lobby_id=? AND user_id=?", (lobby_id, old_uid))
    if not old:
        await message.answer("Старый игрок уже не в лобби.")
        await state.clear()
        return
    team = old['team']
    await db_execute("DELETE FROM lobby_players WHERE lobby_id=? AND user_id=?", (lobby_id, old_uid))
    await db_execute("INSERT OR IGNORE INTO lobby_players VALUES (?,?,?)", (lobby_id, new_uid, team))
    await update_lobby_message(bot, lobby_id)
    await message.answer(f"✅ Игрок заменён на {new_nick}.", reply_markup=main_keyboard())
    await state.clear()

# --- ТИКЕТЫ АДМИНА ---
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
    await run_migrations()
    bot = Bot(token=BOT_TOKEN, session=AiohttpSession())
    await dp.start_polling(bot, polling_timeout=30, handle_as_tasks=False)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())

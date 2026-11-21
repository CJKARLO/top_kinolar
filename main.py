import logging
import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, Forbidden
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)


BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "bot.db"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def generate_movie_code() -> str:
    with get_connection() as database:
        rows = database.execute("SELECT code FROM movies").fetchall()
    numbers = [int(row["code"]) for row in rows if row["code"].strip().isdigit()]
    return str(max(numbers, default=0) + 1)


def normalize_movie_code(code: str) -> str:
    code = code.strip().replace(" ", "")
    return str(int(code)) if code.isdigit() else code


def init_database() -> None:
    with get_connection() as database:
        database.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS subscription (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                chat_id TEXT,
                invite_link TEXT
            );

            CREATE TABLE IF NOT EXISTS movies (
                code TEXT PRIMARY KEY COLLATE NOCASE,
                title TEXT NOT NULL,
                url TEXT,
                file_id TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            INSERT OR IGNORE INTO subscription (id, chat_id, invite_link)
            VALUES (1, '@it_school_oficial', 'https://t.me/it_school_oficial');

            DELETE FROM movies
            WHERE title GLOB 'Sinov videosi*';

            INSERT OR IGNORE INTO movies (code, title, url) VALUES
                ('1', 'Sinov videosi 1', 'https://kinogomy.net/films/10597-povorot-ne-tuda-2026.html'),
                ('2', 'Sinov videosi 2', 'https://samplelib.com/lib/preview/mp4/sample-10s.mp4'),
                ('3', 'Sinov videosi 3', 'https://samplelib.com/lib/preview/mp4/sample-15s.mp4'),
                ('4', 'Sinov videosi 4', 'https://samplelib.com/lib/preview/mp4/sample-20s.mp4'),
                ('5', 'Sinov videosi 5', 'https://samplelib.com/lib/preview/mp4/sample-30s.mp4'),
                ('6', 'Sinov videosi 6', 'https://www.w3schools.com/html/mov_bbb.mp4'),
                ('7', 'Sinov videosi 7', 'https://samplelib.com/lib/preview/mp4/sample-5s.mp4'),
                ('8', 'Sinov videosi 8', 'https://samplelib.com/lib/preview/mp4/sample-10s.mp4'),
                ('9', 'Sinov videosi 9', 'https://samplelib.com/lib/preview/mp4/sample-15s.mp4'),
                ('10', 'Sinov videosi 10', 'https://samplelib.com/lib/preview/mp4/sample-20s.mp4');
            """
        )


def ensure_schema() -> None:
    with get_connection() as database:
        database.executescript(
            """
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

        user_columns = {row[1] for row in database.execute("PRAGMA table_info(users)")}
        if "is_blocked" not in user_columns:
            database.execute("ALTER TABLE users ADD COLUMN is_blocked INTEGER NOT NULL DEFAULT 0")

        movie_columns = {row[1] for row in database.execute("PRAGMA table_info(movies)")}
        if "description" not in movie_columns:
            database.execute("ALTER TABLE movies ADD COLUMN description TEXT")
        if "genre" not in movie_columns:
            database.execute("ALTER TABLE movies ADD COLUMN genre TEXT")
        if "views" not in movie_columns:
            database.execute("ALTER TABLE movies ADD COLUMN views INTEGER NOT NULL DEFAULT 0")

        old_movies = database.execute("SELECT code FROM movies WHERE code LIKE 'K-%'").fetchall()
        for row in old_movies:
            suffix = row["code"].upper().replace("K-", "")
            if suffix.isdigit():
                new_code = str(int(suffix))
                database.execute("UPDATE movies SET code=? WHERE code=? COLLATE NOCASE", (new_code, row["code"]))


def is_admin(update: Update) -> bool:
    user = update.effective_user
    if user is None:
        return False
    if str(user.id) in {
        value.strip() for value in os.getenv("ADMIN_IDS", "").split(",") if value.strip()
    }:
        return True
    with get_connection() as database:
        return database.execute(
            "SELECT 1 FROM admins WHERE user_id = ?", (user.id,)
        ).fetchone() is not None


def is_blocked(update: Update) -> bool:
    user = update.effective_user
    if user is None:
        return False
    with get_connection() as database:
        row = database.execute(
            "SELECT is_blocked FROM users WHERE user_id = ?", (user.id,)
        ).fetchone()
    return bool(row and row[0])


def save_user(update: Update) -> None:
    user = update.effective_user
    if user is None:
        return
    with get_connection() as database:
        database.execute(
            """
            INSERT INTO users (user_id, username, first_name, last_name)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_name = excluded.last_name
            """,
            (user.id, user.username, user.first_name, user.last_name),
        )


def get_subscription() -> sqlite3.Row | None:
    with get_connection() as database:
        return database.execute(
            "SELECT chat_id, invite_link FROM subscription WHERE id = 1"
        ).fetchone()


def admin_ids() -> set[int]:
    raw_ids = os.getenv("ADMIN_IDS", "")
    return {int(value.strip()) for value in raw_ids.split(",") if value.strip().isdigit()}


async def is_subscribed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    subscription = get_subscription()
    if subscription is None or not subscription["chat_id"]:
        return True

    user = update.effective_user
    if user is None:
        return False

    try:
        member = await context.bot.get_chat_member(subscription["chat_id"], user.id)
        return member.status in {"member", "administrator", "creator"} or (
            member.status == "restricted" and member.is_member
        )
    except (BadRequest, Forbidden) as error:
        logger.warning(
            "Could not check membership for user %s in %s: %s",
            user.id,
            subscription["chat_id"],
            error,
        )
        return False


def subscription_message(subscription: sqlite3.Row) -> str:
    link = subscription["invite_link"]
    if not link:
        chat_id = subscription["chat_id"]
        link = f"https://t.me/{chat_id.lstrip('@')}" if chat_id.startswith("@") else "https://t.me/"
    return "🔒 <b>Botdan foydalanish uchun kanalga obuna bo'ling</b>\n\n1. Quyidagi tugma orqali kanalga kiring.\n2. Obuna bo'lgach, «Obunani tekshirish» tugmasini bosing."


def subscription_keyboard(subscription: sqlite3.Row) -> InlineKeyboardMarkup:
    link = subscription["invite_link"]
    if not link:
        chat_id = subscription["chat_id"]
        link = f"https://t.me/{chat_id.lstrip('@')}" if chat_id.startswith("@") else "https://t.me/"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📢 Kanalga obuna bo'lish", url=link)],
            [InlineKeyboardButton("✅ Obunani tekshirish", callback_data="check_subscription")],
        ]
    )


async def prompt_subscription(update: Update) -> None:
    subscription = get_subscription()
    if update.message and subscription:
        await update.message.reply_text(
            subscription_message(subscription),
            reply_markup=subscription_keyboard(subscription),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


async def check_subscription(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if not query:
        return

    if await is_subscribed(update, context):
        await query.answer("Obuna tasdiqlandi! ✅")
        await query.edit_message_text(
            "✅ <b>Obuna tasdiqlandi!</b>\n\nEndi kino kodini yuboring.",
            parse_mode="HTML",
        )
        return

    await query.answer("Kanalga obuna bo'lganingizni tekshiring.", show_alert=True)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    save_user(update)
    if not update.message:
        return
    if is_blocked(update):
        await update.message.reply_text("Siz bloklangansiz. Botdan foydalanish cheklangan.")
        return

    if not await is_subscribed(update, context):
        await prompt_subscription(update)
        return

    await update.message.reply_text(
        "Xush kelibsiz! Kino kodini kiriting, masalan: 1\n"
        "Mavjud kinolarni ko'rish uchun /movies."
    )


async def set_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await update.message.reply_text("Bu buyruq faqat admin uchun.")
        return

    if len(context.args) < 1:
        await update.message.reply_text(
            "Foydalanish: /setchannel @kanal_nomi https://t.me/kanal_havolasi"
        )
        return

    chat_id = context.args[0]
    invite_link = context.args[1] if len(context.args) > 1 else ""
    with get_connection() as database:
        database.execute(
            "UPDATE subscription SET chat_id = ?, invite_link = ? WHERE id = 1",
            (chat_id, invite_link or None),
        )
    await update.message.reply_text(
        f"Majburiy obuna sozlandi: {chat_id}\n"
        "Botni kanalga administrator qilib qo'shganingizga ishonch hosil qiling."
    )


async def unset_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await update.message.reply_text("Bu buyruq faqat admin uchun.")
        return
    with get_connection() as database:
        database.execute(
            "UPDATE subscription SET chat_id = NULL, invite_link = NULL WHERE id = 1"
        )
    await update.message.reply_text("Majburiy obuna o'chirildi.")


async def add_movie(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await update.message.reply_text("Bu buyruq faqat admin uchun.")
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "Foydalanish: /addmovie KOD Kino nomi [video_havolasi]"
        )
        return

    code = generate_movie_code() if context.args[0].strip().lower() in {"avto", "auto"} else normalize_movie_code(context.args[0].strip())
    url = context.args[-1] if len(context.args) >= 3 and context.args[-1].startswith("http") else None
    title_words = context.args[1:-1] if url else context.args[1:]
    title = " ".join(title_words).strip()
    if not title:
        await update.message.reply_text("Kino nomini kiriting.")
        return

    with get_connection() as database:
        database.execute(
            """
            INSERT INTO movies (code, title, url)
            VALUES (?, ?, ?)
            ON CONFLICT(code) DO UPDATE SET
                title = excluded.title,
                url = excluded.url,
                file_id = NULL
            """,
            (code, title, url),
        )
    await update.message.reply_text(f"{code} kodi bilan kino saqlandi.")


async def add_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await update.message.reply_text("Bu buyruq faqat admin uchun.")
        return
    if len(context.args) < 2:
        await update.message.reply_text(
            "Avval videoni xabarga reply qilib `/addvideo KOD Kino nomi` deb yozing."
        )
        return
    if not update.message or not update.message.reply_to_message:
        await update.message.reply_text("Videoga reply qilib qayta yuboring.")
        return

    video = update.message.reply_to_message.video
    if video is None:
        await update.message.reply_text("Reply qilingan xabar video emas.")
        return

    code = context.args[0].strip()
    title = " ".join(context.args[1:]).strip()
    with get_connection() as database:
        database.execute(
            """
            INSERT INTO movies (code, title, url, file_id)
            VALUES (?, ?, NULL, ?)
            ON CONFLICT(code) DO UPDATE SET
                title = excluded.title,
                url = NULL,
                file_id = excluded.file_id
            """,
            (code, title, video.file_id),
        )
    await update.message.reply_text(f"{code} kodi bilan video saqlandi.")


async def delete_movie(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await update.message.reply_text("Bu buyruq faqat admin uchun.")
        return
    if not context.args:
        await update.message.reply_text("Foydalanish: /deletemovie KOD")
        return

    code = context.args[0]
    with get_connection() as database:
        result = database.execute("DELETE FROM movies WHERE code = ? COLLATE NOCASE", (code,))
    await update.message.reply_text(
        "Kino o'chirildi." if result.rowcount else "Bunday kod topilmadi."
    )


async def list_movies(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_subscribed(update, context):
        await prompt_subscription(update)
        return

    with get_connection() as database:
        movies = database.execute(
            "SELECT code, title FROM movies ORDER BY code"
        ).fetchall()
    if not movies:
        await update.message.reply_text("Hozircha kinolar qo'shilmagan.")
        return

    lines = ["Mavjud kinolar:", ""]
    lines.extend(f"{movie['code']} - {movie['title']}" for movie in movies)
    await update.message.reply_text("\n".join(lines))


async def handle_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    save_user(update)
    if not update.message or not update.message.text:
        return
    if is_blocked(update):
        await update.message.reply_text("Siz bloklangansiz. Botdan foydalanish cheklangan.")
        return
    if not await is_subscribed(update, context):
        await prompt_subscription(update)
        return

    query_text = normalize_movie_code(update.message.text.strip())


    with get_connection() as database:
        movie = database.execute(
            "SELECT code, title, description, genre, url, file_id FROM movies WHERE code=? COLLATE NOCASE",
            (query_text,),
        ).fetchone()

        if movie is None:
            movies = database.execute(
                """SELECT code, title FROM movies
                WHERE title LIKE ? COLLATE NOCASE OR genre LIKE ? COLLATE NOCASE
                LIMIT 20""",
                (f"%{query_text}%", f"%{query_text}%"),
            ).fetchall()
            if movies:
                lines = ["🔎 <b>Topilgan kinolar</b>", ""]
                lines.extend(f"<code>{item['code']}</code> — {item['title']}" for item in movies)
                await update.message.reply_text("\n".join(lines), parse_mode="HTML")
            else:
                await update.message.reply_text("Bunday kino topilmadi. Kod yoki nom bo‘yicha qidiring.")
            return

        database.execute("UPDATE movies SET views=views+1 WHERE code=? COLLATE NOCASE", (movie["code"],))

    caption = f"🎬 <b>{movie['title']}</b>"
    if movie["genre"]:
        caption += f"\n📂 Janr: {movie['genre']}"
    if movie["description"]:
        caption += f"\n📝 {movie['description']}"

    if movie["file_id"]:
        await update.message.reply_video(video=movie["file_id"], caption=caption, parse_mode="HTML")
    elif movie["url"]:
        await update.message.reply_video(video=movie["url"], caption=caption, parse_mode="HTML")
    else:
        await update.message.reply_text("Bu kino uchun video hali yuklanmagan.")


async def block_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await update.message.reply_text("Bu buyruq faqat admin uchun.")
        return
    if not context.args:
        await update.message.reply_text("Foydalanish: /block ID")
        return
    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID raqam bo‘lishi kerak.")
        return
    with get_connection() as database:
        database.execute("UPDATE users SET is_blocked=1 WHERE user_id=?", (user_id,))
    await update.message.reply_text(f"<code>{user_id}</code> bloklandi.", parse_mode="HTML")


async def unblock_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await update.message.reply_text("Bu buyruq faqat admin uchun.")
        return
    if not context.args:
        await update.message.reply_text("Foydalanish: /unblock ID")
        return
    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID raqam bo‘lishi kerak.")
        return
    with get_connection() as database:
        database.execute("UPDATE users SET is_blocked=0 WHERE user_id=?", (user_id,))
    await update.message.reply_text(f"<code>{user_id}</code> blokdan chiqarildi.", parse_mode="HTML")


async def admin_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await update.message.reply_text("Bu buyruq faqat admin uchun.")
        return
    if not context.args:
        await update.message.reply_text("Foydalanish: /adminadd ID")
        return
    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID raqam bo‘lishi kerak.")
        return
    with get_connection() as database:
        database.execute("INSERT OR IGNORE INTO admins(user_id) VALUES (?)", (user_id,))
    await update.message.reply_text(f"<code>{user_id}</code> admin qilindi.", parse_mode="HTML")


async def admin_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await update.message.reply_text("Bu buyruq faqat admin uchun.")
        return
    if not context.args:
        await update.message.reply_text("Foydalanish: /adminremove ID")
        return
    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID raqam bo‘lishi kerak.")
        return
    with get_connection() as database:
        database.execute("DELETE FROM admins WHERE user_id=?", (user_id,))
    await update.message.reply_text(f"<code>{user_id}</code> adminlikdan olib tashlandi.", parse_mode="HTML")


async def edit_movie(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await update.message.reply_text("Bu buyruq faqat admin uchun.")
        return
    if len(context.args) < 3:
        await update.message.reply_text("Foydalanish: /editmovie KOD title|description|genre Yangi matn")
        return
    code = context.args[0]
    field = context.args[1].lower()
    allowed = {"title": "title", "description": "description", "genre": "genre"}
    if field not in allowed:
        await update.message.reply_text("Maydon: title, description yoki genre.")
        return
    value = " ".join(context.args[2:]).strip()
    with get_connection() as database:
        result = database.execute(
            f"UPDATE movies SET {allowed[field]}=? WHERE code=? COLLATE NOCASE", (value, code)
        )
    await update.message.reply_text("Tahrirlandi." if result.rowcount else "Kino topilmadi.")


BROADCAST_TEXT = 1


async def start_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        if update.callback_query:
            await update.callback_query.answer("Faqat admin uchun.", show_alert=True)
        return ConversationHandler.END
    source = update.callback_query.message if update.callback_query else update.message
    await source.reply_text("Yuboriladigan reklama yoki xabarnoma matnini yuboring:")
    return BROADCAST_TEXT


async def send_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    with get_connection() as database:
        users = database.execute(
            "SELECT user_id FROM users WHERE is_blocked=0"
        ).fetchall()

    sent = 0
    failed = 0
    for user in users:
        try:
            await context.bot.send_message(user["user_id"], text)
            sent += 1
        except (BadRequest, Forbidden):
            failed += 1
    await update.message.reply_text(f"Xabar yuborildi: {sent}. Yuborilmadi: {failed}.")
    return ConversationHandler.END


async def admin_blocked(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        return
    with get_connection() as database:
        users = database.execute(
            "SELECT user_id, username, first_name FROM users WHERE is_blocked=1 LIMIT 50"
        ).fetchall()
    if not users:
        text = "Bloklangan foydalanuvchi yo‘q."
    else:
        lines = ["🚫 <b>Bloklanganlar</b>"]
        lines.extend(
            f"• <code>{u['user_id']}</code> — @{u['username']}" if u['username'] else
            f"• <code>{u['user_id']}</code> — {u['first_name']}"
            for u in users
        )
        text = "\n".join(lines)
    await update.callback_query.edit_message_text(text, parse_mode="HTML")


def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 Kino qo‘shish", callback_data="admin_add")],
        [InlineKeyboardButton("📋 Kinolar", callback_data="admin_movies")],
        [InlineKeyboardButton("📊 Statistika", callback_data="admin_stats")],
        [InlineKeyboardButton("👥 Foydalanuvchilar", callback_data="admin_users")],
        [InlineKeyboardButton("🚫 Bloklanganlar", callback_data="admin_blocked")],
        [InlineKeyboardButton("📣 Reklama yuborish", callback_data="admin_broadcast")],
        [InlineKeyboardButton("👤 Admin qo‘shish", callback_data="admin_add_admin")],
        [InlineKeyboardButton("➖ Adminni o‘chirish", callback_data="admin_remove_admin")],
    ])


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await update.message.reply_text("Bu buyruq faqat admin uchun.")
        return
    await update.message.reply_text(
        "👨‍💼 <b>Admin panel</b>",
        reply_markup=admin_panel_keyboard(),
        parse_mode="HTML",
    )


ADD_CODE, ADD_TITLE, ADD_DESCRIPTION, ADD_GENRE, ADD_VIDEO = range(5)


async def start_add_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        if update.callback_query:
            await update.callback_query.answer("Faqat admin uchun.", show_alert=True)
        return ConversationHandler.END

    source = update.callback_query.message if update.callback_query else update.message
    await source.reply_text("1/5 — Kino kodini yuboring:")
    return ADD_CODE


async def add_movie_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = normalize_movie_code(update.message.text.strip())
    if not code:
        await update.message.reply_text("Kino kodi bo‘sh bo‘lmasligi kerak.")
        return ADD_CODE
    context.user_data["new_movie_code"] = code
    await update.message.reply_text("2/5 — Kino nomini yuboring:")
    return ADD_TITLE


async def add_movie_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_movie_title"] = update.message.text.strip()
    await update.message.reply_text("3/5 — Kino tavsifini yuboring:")
    return ADD_DESCRIPTION


async def add_movie_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_movie_description"] = update.message.text.strip()
    await update.message.reply_text("4/5 — Kategoriya yoki janrni yuboring:")
    return ADD_GENRE


async def add_movie_genre(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_movie_genre"] = update.message.text.strip()
    await update.message.reply_text("5/5 — Kino videosini yuboring:")
    return ADD_VIDEO


async def add_movie_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    video = update.message.video
    if video is None:
        await update.message.reply_text("Iltimos, video yuboring.")
        return ADD_VIDEO

    code = context.user_data["new_movie_code"]
    title = context.user_data["new_movie_title"]
    description = context.user_data["new_movie_description"]
    genre = context.user_data["new_movie_genre"]
    with get_connection() as database:
        database.execute(
            """
            INSERT INTO movies (code, title, description, genre, url, file_id)
            VALUES (?, ?, ?, ?, NULL, ?)
            ON CONFLICT(code) DO UPDATE SET
                title=excluded.title,
                description=excluded.description,
                genre=excluded.genre,
                url=NULL,
                file_id=excluded.file_id
            """,
            (code, title, description, genre, video.file_id),
        )
    await update.message.reply_text(f"✅ {code} kodli kino saqlandi.")
    context.user_data.clear()
    return ConversationHandler.END


async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Bekor qilindi.")
    return ConversationHandler.END


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        return
    with get_connection() as database:
        users = database.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        blocked = database.execute("SELECT COUNT(*) FROM users WHERE is_blocked=1").fetchone()[0]
        movies = database.execute("SELECT COUNT(*) FROM movies").fetchone()[0]
        views = database.execute("SELECT COALESCE(SUM(views),0) FROM movies").fetchone()[0]
    text = (
        "📊 <b>Bot statistikasi</b>\n\n"
        f"👥 Foydalanuvchilar: <b>{users}</b>\n"
        f"🚫 Bloklangan: <b>{blocked}</b>\n"
        f"🎬 Kinolar: <b>{movies}</b>\n"
        f"▶️ Ko‘rishlar: <b>{views}</b>"
    )
    await update.callback_query.edit_message_text(text, parse_mode="HTML")


async def admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        return
    with get_connection() as database:
        users = database.execute(
            "SELECT user_id, username, first_name, is_blocked FROM users ORDER BY joined_at DESC LIMIT 50"
        ).fetchall()
    if not users:
        text = "Foydalanuvchilar yo‘q."
    else:
        lines = ["👥 <b>Foydalanuvchilar</b>", ""]
        for user in users:
            name = f"@{user['username']}" if user['username'] else user['first_name'] or "Noma'lum"
            status = "🚫" if user['is_blocked'] else "✅"
            lines.append(f"{status} {name} — <code>{user['user_id']}</code>")
        text = "\n".join(lines)
    await update.callback_query.edit_message_text(text, parse_mode="HTML")


async def admin_movies(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        return
    with get_connection() as database:
        movies = database.execute(
            "SELECT code, title FROM movies ORDER BY created_at DESC LIMIT 30"
        ).fetchall()
    if not movies:
        await update.callback_query.edit_message_text("Kinolar yo‘q.")
        return

    keyboard = []
    lines = ["🎬 <b>Kinolar</b>"]
    for movie in movies:
        code = movie["code"]
        lines.append(f"• <code>{code}</code> — {movie['title']}")
        keyboard.append([
            InlineKeyboardButton(f"🗑 {code}", callback_data=f"movie_delete:{code}"),
            InlineKeyboardButton(f"📤 {code}", callback_data=f"movie_publish:{code}"),
        ])
    keyboard.append([InlineKeyboardButton("⬅️ Panel", callback_data="admin_back")])
    await update.callback_query.edit_message_text(
        "\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
    )


async def movie_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not is_admin(update):
        return
    data = query.data
    action, code = data.split(":", 1)
    if action == "movie_delete":
        with get_connection() as database:
            database.execute("DELETE FROM movies WHERE code=? COLLATE NOCASE", (code,))
        await query.answer("Kino o‘chirildi.")
        await query.edit_message_text(f"🗑 <code>{code}</code> kodi bilan kino o‘chirildi.", parse_mode="HTML")
    elif action == "movie_publish":
        subscription = get_subscription()
        if not subscription or not subscription["chat_id"]:
            await query.answer("Avval /setchannel orqali kanalni sozlang.", show_alert=True)
            return
        with get_connection() as database:
            movie = database.execute(
                "SELECT title, description, genre, url, file_id FROM movies WHERE code=? COLLATE NOCASE",
                (code,),
            ).fetchone()
        if not movie:
            await query.answer("Kino topilmadi.", show_alert=True)
            return
        caption = f"🎬 <b>{movie['title']}</b>\n"
        if movie["genre"]:
            caption += f"📂 Janr: {movie['genre']}\n"
        if movie["description"]:
            caption += f"📝 {movie['description']}\n"
        caption += f"🔑 Kod: <code>{code}</code>"
        if movie["file_id"]:
            await context.bot.send_video(subscription["chat_id"], movie["file_id"], caption=caption, parse_mode="HTML")
        elif movie["url"]:
            await context.bot.send_video(subscription["chat_id"], movie["url"], caption=caption, parse_mode="HTML")
        await query.answer("Kino kanalga yuborildi.")


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not is_admin(update):
        return
    data = query.data
    if data == "admin_back":
        await query.edit_message_text("👨‍💼 <b>Admin panel</b>", reply_markup=admin_panel_keyboard(), parse_mode="HTML")
    elif data == "admin_stats":
        await admin_stats(update, context)
    elif data == "admin_users":
        await admin_users(update, context)
    elif data == "admin_movies":
        await admin_movies(update, context)
    elif data == "admin_blocked":
        await admin_blocked(update, context)
    elif data in {"admin_add_admin", "admin_remove_admin"}:
        command = "/adminadd ID" if data == "admin_add_admin" else "/adminremove ID"
        await query.edit_message_text(f"Adminni boshqarish: <code>{command}</code>", parse_mode="HTML")


def main() -> None:
    env_file = BASE_DIR / ".env"
    load_dotenv(env_file)
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError(
            f"BOT_TOKEN topilmadi. {env_file} faylini yarating va "
            "ichiga BOT_TOKEN=BotFather_dan_yangi_token hamda "
            "ADMIN_IDS=Telegram_user_ID qatorlarini kiriting. "
            ".env.example faylidan namuna olishingiz mumkin."
        )
    if not os.getenv("ADMIN_IDS"):
        raise RuntimeError(
            f"ADMIN_IDS topilmadi. {env_file} fayliga "
            "ADMIN_IDS=Telegram_user_ID qatorini qo'shing."
        )

    init_database()
    ensure_schema()
    application = Application.builder().token(token).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CommandHandler("movies", list_movies))
    application.add_handler(CommandHandler("setchannel", set_subscription))
    application.add_handler(CommandHandler("unsetchannel", unset_subscription))
    application.add_handler(CommandHandler("addmovie", add_movie))
    application.add_handler(CommandHandler("addvideo", add_video))
    application.add_handler(CommandHandler("deletemovie", delete_movie))
    application.add_handler(CommandHandler("editmovie", edit_movie))
    application.add_handler(CommandHandler("block", block_user))
    application.add_handler(CommandHandler("unblock", unblock_user))
    application.add_handler(CommandHandler("adminadd", admin_add))
    application.add_handler(CommandHandler("adminremove", admin_remove))

    add_movie_handler = ConversationHandler(
        entry_points=[
            CommandHandler("adminaddmovie", start_add_movie),
            CallbackQueryHandler(start_add_movie, pattern="^admin_add$"),
        ],
        states={
            ADD_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_movie_code)],
            ADD_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_movie_title)],
            ADD_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_movie_description)],
            ADD_GENRE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_movie_genre)],
            ADD_VIDEO: [MessageHandler(filters.VIDEO, add_movie_video)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
    )
    application.add_handler(add_movie_handler)

    broadcast_handler = ConversationHandler(
        entry_points=[
            CommandHandler("broadcast", start_broadcast),
            CallbackQueryHandler(start_broadcast, pattern="^admin_broadcast$"),
        ],
        states={BROADCAST_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, send_broadcast)]},
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
    )
    application.add_handler(broadcast_handler)

    application.add_handler(CallbackQueryHandler(check_subscription, pattern="^check_subscription$"))
    application.add_handler(CallbackQueryHandler(movie_callback, pattern="^movie_(delete|publish):"))
    application.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin_"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_code))
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

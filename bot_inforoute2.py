import logging
import asyncio
import asyncpg
import os
from aiohttp import web
from telegram import (Update, InlineKeyboardButton, InlineKeyboardMarkup,
                      KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove)
from telegram.ext import (ApplicationBuilder, CallbackQueryHandler,
                          ContextTypes, MessageHandler, CommandHandler, filters)

# === CONFIGURATION ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = -1002679914144
ADMIN_LOG_GROUP_ID = -1002344064291
COMMENT_GROUP_ID = -1002540408114
BYPASS_CONFIRM_GROUP_ID = -1002344064291
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.environ.get("PORT", 10000))

# === POSTGRESQL CONFIG VIA DATABASE_URL ===
PG_DSN = os.getenv("DATABASE_URL")
if not PG_DSN:
    raise ValueError("La variable d'environnement DATABASE_URL est manquante.")

# === STOCKAGE TEMPORAIRE ===
pending_messages = {}
message_links = {}
user_contacts = {}

# === INIT DB ===
async def init_db():
    conn = await asyncpg.connect(dsn=PG_DSN)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS blacklist (
            user_id BIGINT PRIMARY KEY
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS contacts (
            user_id BIGINT PRIMARY KEY,
            phone TEXT
        )
    """)
    await conn.close()

# === DB FUNCTIONS ===
async def block_user_id(user_id):
    conn = await asyncpg.connect(dsn=PG_DSN)
    await conn.execute("INSERT INTO blacklist (user_id) VALUES ($1) ON CONFLICT DO NOTHING", user_id)
    await conn.close()

async def unblock_user_id(user_id):
    conn = await asyncpg.connect(dsn=PG_DSN)
    await conn.execute("DELETE FROM blacklist WHERE user_id = $1", user_id)
    await conn.close()

async def get_blocked_users():
    conn = await asyncpg.connect(dsn=PG_DSN)
    rows = await conn.fetch("SELECT user_id FROM blacklist")
    await conn.close()
    return [row["user_id"] for row in rows]

async def is_user_blocked(user_id):
    conn = await asyncpg.connect(dsn=PG_DSN)
    row = await conn.fetchrow("SELECT 1 FROM blacklist WHERE user_id = $1", user_id)
    await conn.close()
    return row is not None

async def save_user_contact(user_id, phone):
    conn = await asyncpg.connect(dsn=PG_DSN)
    await conn.execute("""
        INSERT INTO contacts (user_id, phone) VALUES ($1, $2)
        ON CONFLICT (user_id) DO UPDATE SET phone = $2
    """, user_id, phone)
    await conn.close()

async def get_user_contact(user_id):
    conn = await asyncpg.connect(dsn=PG_DSN)
    row = await conn.fetchrow("SELECT phone FROM contacts WHERE user_id = $1", user_id)
    await conn.close()
    return row["phone"] if row else "Non enregistré"

# === LOGGING ===
logging.basicConfig(level=logging.INFO)

# === DEMANDE DE CONTACT À LA PREMIÈRE INTERACTION ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    phone = await get_user_contact(user.id)
    if phone != "Non enregistré":
        await update.message.reply_text("✅ Ton numéro est déjà enregistré.")
        return

    reply_markup = ReplyKeyboardMarkup(
        [[KeyboardButton("📞 Partager mon numéro", request_contact=True)]],
        one_time_keyboard=True,
        resize_keyboard=True,
        input_field_placeholder="Appuie pour envoyer ton numéro",
        selective=True
    )

    await update.message.reply_text(
        "Bienvenue ! Pour utiliser ce bot, merci de partager ton numéro de téléphone, "
        "il restera confidentiel. C’est une mesure pour éviter les spams et abus.",
        reply_markup=reply_markup
    )

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact
    if contact.user_id == update.effective_user.id:
        await save_user_contact(contact.user_id, contact.phone_number)
        await update.message.reply_text("✅ Merci, ton numéro a bien été enregistré.", reply_markup=ReplyKeyboardRemove())
    else:
        await update.message.reply_text("⚠️ Ce numéro ne correspond pas à ton compte.")

# === GESTION DES MESSAGES TEXTE ===
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    message = update.message.text

    # Vérifie que l'utilisateur a partagé son numéro
    phone = await get_user_contact(user_id)
    if phone == "Non enregistré":
        await update.message.reply_text("📵 Tu dois d'abord partager ton numéro avec /start.")
        return

    # Vérifie s'il est bloqué
    if await is_user_blocked(user_id):
        await update.message.reply_text("🚫 Tu es actuellement bloqué et ne peux pas envoyer de messages.")
        return

    # Envoie la confirmation avec boutons
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Oui !", callback_data=f"confirm|{user_id}"),
         InlineKeyboardButton("Non ! je me suis trompé", callback_data=f"cancel|{user_id}")]
    ])

    await update.message.reply_text(f"📝 Ton message :\n\n{message}\n\nSouhaites-tu l'envoyer ?", reply_markup=keyboard)
    pending_messages[user_id] = message

# === CALLBACK CONFIRMATION ===
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split("|")

    if len(data) != 2:
        return

    action, user_id = data
    user_id = int(user_id)
    message = pending_messages.get(user_id)

    if not message:
        await query.edit_message_text("⚠️ Aucun message à envoyer.")
        return

    user = await context.bot.get_chat(user_id)
    phone = await get_user_contact(user_id)

    if action == "confirm":
        # Envoie dans le canal principal
        sent = await context.bot.send_message(chat_id=CHANNEL_ID, text=message)

        # Envoie résumé dans le groupe admin avec les boutons
        admin_text = (
        f"📩 *Message reçu :*\n"
        f"```{message}```\n\n"
        f"👤 *Utilisateur* : @{user.username if user.username else 'Aucun'}\n"
        f"🆔 *ID* : `{user_id}`\n"
        f"📞 *Téléphone* : `{phone}`"
        )

        buttons = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🗑 Poubelle", callback_data=f"delete|{sent.message_id}"),
                InlineKeyboardButton("Sup & Ban", callback_data=f"ban|{user_id}|{sent.message_id}")
            ]
        ])

        await context.bot.send_message(chat_id=ADMIN_LOG_GROUP_ID, text=admin_text, parse_mode="Markdown", reply_markup=buttons)
        await query.edit_message_text("✅ Ton message a été publié !")
        del pending_messages[user_id]

    elif action == "cancel":
        await query.edit_message_text("❌ Message annulé.")
        del pending_messages[user_id]

    elif action.startswith("delete"):
        _, msg_id = data
        await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=int(msg_id))
        await query.edit_message_text("🗑 Message supprimé.")

    elif action.startswith("ban"):
        _, uid, msg_id = data
        await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=int(msg_id))
        await block_user_id(int(uid))
        await query.edit_message_text("🚫 Message supprimé et utilisateur banni.")

# === COMMANDE: LISTE DES BLOQUÉS ===
async def blocked_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    blocked_users = await get_blocked_users()
    if not blocked_users:
        await update.message.reply_text("✅ Aucun utilisateur bloqué.")
        return

    text = "🚫 Liste des utilisateurs bloqués :\n"
    for user_id in blocked_users:
        username = "(inconnu)"
        try:
            user = await context.bot.get_chat(user_id)
            username = f"@{user.username}" if user.username else "(aucun username)"
        except:
            pass
        text += f"- `{user_id}` {username}\n"
    await update.message.reply_text(text, parse_mode="Markdown")

# === COMMANDE: UNBLOCK USER ===
async def unblock_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Utilisation : /unblockuser <user_id>")
        return
    try:
        user_id = int(context.args[0])
        await unblock_user_id(user_id)
        await update.message.reply_text(f"✅ Utilisateur {user_id} débloqué.")
    except:
        await update.message.reply_text("❌ Erreur de format. Utilise un ID valide.")

# === COMMANDE: BLOCK USER ===
async def block_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Utilisation : /blockuser <user_id>")
        return
    try:
        user_id = int(context.args[0])
        await block_user_id(user_id)
        await update.message.reply_text(f"⛔️ Utilisateur {user_id} bloqué.")
    except:
        await update.message.reply_text("❌ Erreur de format. Utilise un ID valide.")

# === COMMANDE: AFFICHER NUMÉRO ===
async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Utilisation : /phone <user_id>")
        return
    try:
        user_id = int(context.args[0])
        phone = await get_user_contact(user_id)
        await update.message.reply_text(f"📞 Numéro pour l'utilisateur `{user_id}` : `{phone}`", parse_mode="Markdown")
    except Exception:
        await update.message.reply_text("❌ Erreur lors de la récupération du numéro.")

# === COMMANDE: INFOS UTILISATEUR ===
async def find_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Utilisation : /finduser <user_id>")
        return
    try:
        user_id = int(context.args[0])
        user = await context.bot.get_chat(user_id)
        full_name = f"{user.first_name} {user.last_name}" if user.last_name else user.first_name
        await update.message.reply_text(
            f"Nom : {full_name}\nUsername : @{user.username if user.username else 'Aucun'}\nID : {user.id}"
        )
    except Exception:
        await update.message.reply_text("Utilisateur introuvable.")

# === ROUTE POUR VÉRIFIER SI LE BOT EST EN VIE ===
async def handle_root(request):
    return web.Response(text="Bot is alive.")

# === LANCE LE SERVEUR HTTP AIOHTTP ===
async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_root)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

# === MAIN ===
async def main():
    await init_db()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("blockuser", block_user))
    app.add_handler(CommandHandler("unblockuser", unblock_user))
    app.add_handler(CommandHandler("blocked", blocked_list))
    app.add_handler(CommandHandler("phone", get_phone))
    app.add_handler(CommandHandler("finduser", find_user))
    app.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))
    app.add_handler(CallbackQueryHandler(handle_callback))

    await start_web_server()

    print("Bot démarré avec webhook...")
    await app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        webhook_url=WEBHOOK_URL
    )

if __name__ == '__main__':
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.run(main())

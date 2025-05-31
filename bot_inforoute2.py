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
blacklisted_phones = set()

# === INIT DB ===
async def init_db():
    conn = await asyncpg.connect(dsn=PG_DSN)

    # Création des tables
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS blacklist (
            user_id BIGINT PRIMARY KEY
            -- phone sera ajoutée ensuite
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS contacts (
            user_id BIGINT PRIMARY KEY,
            phone TEXT
        )
    """)

    # Ajout de la colonne 'phone' à blacklist si elle n'existe pas
    try:
        await conn.execute("ALTER TABLE blacklist ADD COLUMN phone TEXT")
    except asyncpg.exceptions.DuplicateColumnError:
        pass

    # Contrainte UNIQUE sur contacts.phone
    contact_constraint = await conn.fetchval("""
        SELECT 1 FROM pg_constraint WHERE conname = 'unique_phone'
    """)
    if not contact_constraint:
        await conn.execute("ALTER TABLE contacts ADD CONSTRAINT unique_phone UNIQUE (phone)")

    # Contrainte UNIQUE sur blacklist.phone
    blacklist_constraint = await conn.fetchval("""
        SELECT 1 FROM pg_constraint WHERE conname = 'unique_blacklist_phone'
    """)
    if not blacklist_constraint:
        await conn.execute("ALTER TABLE blacklist ADD CONSTRAINT unique_blacklist_phone UNIQUE (phone)")

    await conn.close()

# === DB FUNCTIONS ===
async def block_user_id(user_id, phone=None):
    conn = await asyncpg.connect(dsn=PG_DSN)

    # Si le téléphone est fourni, on vérifie s’il est déjà bloqué par un autre user
    if phone:
        existing = await conn.fetchrow("SELECT user_id FROM blacklist WHERE phone = $1", phone)
        if existing and existing["user_id"] != user_id:
            await conn.close()
            return False  # Numéro déjà bloqué par un autre utilisateur

        # Insert or update
        await conn.execute("""
            INSERT INTO blacklist (user_id, phone)
            VALUES ($1, $2)
            ON CONFLICT (user_id) DO UPDATE SET phone = $2
        """, user_id, phone)
    else:
        # Ajoute sans numéro si non fourni
        await conn.execute("""
            INSERT INTO blacklist (user_id)
            VALUES ($1)
            ON CONFLICT DO NOTHING
        """, user_id)

    await conn.close()
    return True

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

# === DB FUNCTIONS - CONTACTS ===
async def save_user_contact(user_id, phone):
    conn = await asyncpg.connect(dsn=PG_DSN)

    # Vérifie si le téléphone est déjà utilisé par un autre user_id
    existing = await conn.fetchrow("SELECT user_id FROM contacts WHERE phone = $1", phone)

    if existing and existing["user_id"] != user_id:
        await conn.close()
        return False  # Numéro déjà utilisé par un autre utilisateur

    # Sinon, on enregistre ou met à jour
    await conn.execute("""
        INSERT INTO contacts (user_id, phone) VALUES ($1, $2)
        ON CONFLICT (user_id) DO UPDATE SET phone = $2
    """, user_id, phone)

    await conn.close()
    return True

async def get_user_contact(user_id):
    conn = await asyncpg.connect(dsn=PG_DSN)
    row = await conn.fetchrow("SELECT phone FROM contacts WHERE user_id = $1", user_id)
    await conn.close()
    return row["phone"] if row else "Non enregistré"

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
    if contact.user_id != update.effective_user.id:
        await update.message.reply_text("⚠️ Ce numéro ne correspond pas à ton compte.")
        return

    success = await save_user_contact(contact.user_id, contact.phone_number)

    if success:
        await update.message.reply_text("✅ Merci, ton numéro a bien été enregistré.", reply_markup=ReplyKeyboardRemove())
    else:
        await update.message.reply_text("🚫 Ce numéro est déjà utilisé par un autre utilisateur.")

# === GESTION DES MESSAGES TEXTE ===
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    message = update.message.text

    phone = await get_user_contact(user_id)
    if phone == "Non enregistré":
        await update.message.reply_text("📵 Tu dois d'abord partager ton numéro avec /start.")
        return

    if update.message.chat.id == BYPASS_CONFIRM_GROUP_ID:
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Oui, envoyer !", callback_data=f"confirm|{user_id}"),
         InlineKeyboardButton("❌ Non, annuler", callback_data=f"cancel|{user_id}")]
    ])

    await update.message.reply_text(f"📝 Ton message :\n\n{message}\n\nTon message est-il correct ?", reply_markup=keyboard)
    pending_messages[user_id] = message

# === SUPPRESSION AUTOMATIQUE APRÈS 3 HEURES ===
async def auto_delete_message(context: ContextTypes.DEFAULT_TYPE, message_id: int):
    await asyncio.sleep(3 * 60 * 60)
    try:
        await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=message_id)
        logging.info(f"Message {message_id} supprimé automatiquement du canal.")
    except Exception as e:
        logging.warning(f"Erreur suppression automatique message {message_id} : {e}")

# === CALLBACK CONFIRMATION (corrigé ici) ===
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split("|")

    try:
        action = data[0]

        if action in ["confirm", "cancel"] and len(data) == 2:
            user_id = int(data[1])

            if action == "confirm":
                message = pending_messages.get(user_id)
                if not message:
                    await query.edit_message_text("⚠️ Aucun message à envoyer.")
                    return

                await confirm_and_forward(user_id, message, context)
                await query.edit_message_text("✅ Ton message a été publié !")
                del pending_messages[user_id]

            elif action == "cancel":
                del pending_messages[user_id]
                await query.edit_message_text("❌ Message annulé.")

        elif action == "delete" and len(data) == 2:
            msg_id = int(data[1])
            value = message_links.get(msg_id)
            if value:
                uid = value["user_id"]
                text = value["text"]
                try:
                    await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=msg_id)
                    user = await context.bot.get_chat(uid)
                    phone = await get_user_contact(uid)

                    summary = (
                        f"🗑 *Message supprimé du canal*\n"
                        f"👤 Nom : {user.first_name} {user.last_name if user.last_name else ''}\n"
                        f"🔗 Username : @{user.username if user.username else 'Aucun'}\n"
                        f"🆔 ID : `{uid}`\n"
                        f"📞 Téléphone : `{phone}`\n"
                        f"\n📨 Message :\n```{text}```"
                    )

                    await query.edit_message_text(text=summary, parse_mode="Markdown")
                    del message_links[msg_id]
                except Exception as e:
                    logging.warning(f"Erreur lors de la suppression du message {msg_id}: {e}")
                    await query.edit_message_text("⚠️ Impossible de supprimer ce message.")
            else:
                await query.edit_message_text("⚠️ Message non reconnu.")

        elif action == "ban" and len(data) == 3:
            uid = int(data[1])
            msg_id = int(data[2])
            value = message_links.get(msg_id)

            if not value:
                await query.edit_message_text("⚠️ Impossible de traiter cette action (message introuvable).")
                return

            message = value.get("text", "Non disponible")
            phone = await get_user_contact(uid)

            # Empêche doublon de numéro
            success = await block_user_id(uid, phone)
            if not success:
                await query.edit_message_text("🚫 Ce numéro est déjà banni par un autre utilisateur.")
                return

            try:
                await save_user_contact(uid, phone)
                await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=msg_id)
                blacklisted_phones.add(phone)

                user = await context.bot.get_chat(uid)
                summary = (
                    f"🚫 *Message supprimé et utilisateur banni*\n"
                    f"👤 Utilisateur : @{user.username if user.username else 'Aucun'}\n"
                    f"🆔 ID : `{uid}`\n"
                    f"📞 Téléphone : `{phone}`\n"
                    f"📨 Message :\n```{message}```"
                )

                await query.edit_message_text(text=summary, parse_mode="Markdown")
                del message_links[msg_id]
            except Exception as e:
                logging.exception(f"Erreur dans Sup & Ban : {e}")
                await query.edit_message_text("⚠️ Une erreur est survenue pendant l'action.")

    except Exception as e:
        logging.exception(f"Erreur dans handle_callback : {e}")
        await query.edit_message_text("⚠️ Une erreur inattendue est survenue.")

# === FORWARD FUNCTION ===
async def confirm_and_forward(user_id, message, context):
    user = await context.bot.get_chat(user_id)
    phone = await get_user_contact(user_id)
    sent = await context.bot.send_message(chat_id=CHANNEL_ID, text=message)

    context.application.create_task(auto_delete_message(context, sent.message_id))

    admin_text = (
    "🆕 *Nouveau message reçu*\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    f"👤 *Utilisateur* : {user.first_name} {user.last_name or ''}\n"
    f"🔗 *Username* : {('@' + user.username) if user.username else '_(aucun)_'}\n"
    f"🆔 *ID* : `{user_id}`\n"
    f"📞 *Téléphone* : `{phone}`\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "✉️ *Message :*\n"
    f"```{message}```"
    )


    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🗑 Poubelle", callback_data=f"delete|{sent.message_id}"),
            InlineKeyboardButton("🚫 Sup & Ban", callback_data=f"ban|{user_id}|{sent.message_id}")
        ]
    ])

    message_links[sent.message_id] = {"user_id": user_id, "text": message}

    await context.bot.send_message(chat_id=ADMIN_LOG_GROUP_ID, text=admin_text, parse_mode="Markdown", reply_markup=buttons)

# === COMMANDES ADMIN ===
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

import logging
import asyncio
import json
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import (ApplicationBuilder, CallbackQueryHandler,
                          ContextTypes, MessageHandler, CommandHandler, filters)

# === CONFIGURATION ===
BOT_TOKEN = "7630579050:AAH3rGEWP2RjFWGJ4CyAw843Qs1KN8IjrLI"
CHANNEL_ID = -1002679914144
ADMIN_LOG_GROUP_ID = -1002344064291
COMMENT_GROUP_ID = -1002540408114
BYPASS_CONFIRM_GROUP_ID = -1002344064291

# === STOCKAGE TEMPORAIRE ===
pending_messages = {}
message_links = {}
blocked_users = set()
user_contacts = {}

# === FICHIERS DE DONNÉES ===
BLACKLIST_FILE = "blocked_ids.json"
CONTACTS_FILE = "contacts.json"

def load_data():
    global blocked_users, user_contacts
    if os.path.exists(BLACKLIST_FILE):
        with open(BLACKLIST_FILE, "r") as f:
            blocked_users = set(json.load(f))
    if os.path.exists(CONTACTS_FILE):
        with open(CONTACTS_FILE, "r") as f:
            user_contacts = json.load(f)

def save_blacklist():
    with open(BLACKLIST_FILE, "w") as f:
        json.dump(list(blocked_users), f)

def save_contacts():
    with open(CONTACTS_FILE, "w") as f:
        json.dump(user_contacts, f)

load_data()

# === LOGGING ===
logging.basicConfig(level=logging.INFO)

# === DEMANDE DE CONTACT À LA PREMIÈRE INTERACTION ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if str(user.id) in user_contacts:
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
        user_contacts[str(contact.user_id)] = contact.phone_number
        save_contacts()
        await update.message.reply_text("✅ Merci, ton numéro a bien été enregistré.", reply_markup=ReplyKeyboardMarkup([[]], remove_keyboard=True))
    else:
        await update.message.reply_text("⚠️ Ce numéro ne correspond pas à ton compte.")

# === COMMANDE: LISTE DES BLOQUÉS ===
async def blocked_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        blocked_users.discard(user_id)
        save_blacklist()
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
        blocked_users.add(user_id)
        save_blacklist()
        await update.message.reply_text(f"⛔️ Utilisateur {user_id} bloqué.")
    except:
        await update.message.reply_text("❌ Erreur de format. Utilise un ID valide.")

# === COMMANDE: AFFICHER NUMÉRO ===
async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Utilisation : /phone <user_id>")
        return
    try:
        user_id = str(int(context.args[0]))
        phone = user_contacts.get(user_id, "Non enregistré")
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

# === MAIN ===
if __name__ == '__main__':
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("blockuser", block_user))
    app.add_handler(CommandHandler("unblockuser", unblock_user))
    app.add_handler(CommandHandler("blocked", blocked_list))
    app.add_handler(CommandHandler("phone", get_phone))
    app.add_handler(CommandHandler("finduser", find_user))

    app.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_callback))

    print("Bot démarré...")
    app.run_polling()

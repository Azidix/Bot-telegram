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

# === SUPPRESSION AUTOMATIQUE APRÈS 3H ===
async def auto_delete_message(context: ContextTypes.DEFAULT_TYPE, message_id: int):
    try:
        await asyncio.sleep(3 * 60 * 60)
        await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=message_id)
    except Exception as e:
        logging.warning(f"Erreur suppression automatique message {message_id} : {e}")

# === RÉCEPTION DES MESSAGES ===
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id in blocked_users:
        return

    if str(user.id) not in user_contacts:
        keyboard = [[InlineKeyboardButton("🚀 Démarrer", callback_data="force_start")]]
        await update.message.reply_text(
            "⚠️ Tu dois partager ton numéro de téléphone pour utiliser ce bot.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    message = update.effective_message
    chat_id = update.effective_chat.id

    if chat_id in [COMMENT_GROUP_ID, BYPASS_CONFIRM_GROUP_ID]:
        return

    pending_messages[(chat_id, message.message_id)] = message

    keyboard = [
        [
            InlineKeyboardButton("✅ Oui, envoyer !", callback_data=f"confirm|{chat_id}|{message.message_id}"),
            InlineKeyboardButton("❌ Non, annuler", callback_data=f"cancel|{chat_id}|{message.message_id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await message.reply_text("Ton message est-il correct ?", reply_markup=reply_markup)

# === CALLBACK DES BOUTONS INLINE ===
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception as e:
        logging.warning(f"Impossible de répondre à la callback : {e}")
    user = update.effective_user

    if query.data == "force_start":
        keyboard = [[KeyboardButton("📞 Partager mon numéro", request_contact=True)]]
        reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
        await query.message.reply_text(
            "Merci de cliquer sur le bouton ci-dessous pour partager ton numéro de téléphone :",
            reply_markup=reply_markup
        )
        return

    if str(user.id) not in user_contacts:
        await query.edit_message_text(
            "⚠️ Tu dois partager ton numéro de téléphone pour utiliser le bot.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🚀 Démarrer", callback_data="force_start")]
            ])
        )
        return

    try:
        parts = query.data.split("|")
        action = parts[0]

        if action in ["confirm", "cancel"]:
            if len(parts) != 3:
                raise ValueError(f"callback_data invalide pour {action}")
            chat_id = int(parts[1])
            message_id = int(parts[2])

        elif action == "deleteban":
            if len(parts) != 3:
                raise ValueError("callback_data invalide pour deleteban")
            message_id = int(parts[1])
            user_id = int(parts[2])
            blocked_users.add(user_id)
            save_blacklist()
            await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=message_id)

            user_info = await context.bot.get_chat(user_id)
            full_name = f"{user_info.first_name} {user_info.last_name}" if user_info.last_name else user_info.first_name
            phone = user_contacts.get(str(user_id), "Non fourni")
            username = f"@{user_info.username}" if user_info.username else "aucun"

            await context.bot.send_message(
                chat_id=ADMIN_LOG_GROUP_ID,
                text=(
                    f"✉️ Message supprimé et utilisateur banni\n"
                    f"👤 Nom : {full_name}\n"
                    f"🔗 Username : {username}\n"
                    f"🆔 ID : `{user_id}`\n"
                    f"📞 Téléphone : `{phone}`\n\n"
                    f"📨 Message :\n{value.get('text', 'Non disponible')}"
                ),
                parse_mode="Markdown"
            )

            await query.edit_message_text(
                text=f"🗑 Message supprimé du canal et utilisateur `{user_id}` banni !",
                parse_mode="Markdown"
            )
            return

        elif action == "delete":
            message_id = int(parts[1])
            for log_id, value in list(message_links.items()):
                if isinstance(value, dict) and value["canal_id"] == message_id:
                    user_id = value["user_id"]
                    await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=message_id)

                    user_info = await context.bot.get_chat(user_id)
                    full_name = f"{user_info.first_name} {user_info.last_name}" if user_info.last_name else user_info.first_name
                    phone = user_contacts.get(str(user_id), "Non fourni")
                    username = f"@{user_info.username}" if user_info.username else "aucun"

                    await context.bot.send_message(
                        chat_id=ADMIN_LOG_GROUP_ID,
                        text=(
                            f"✉️ Message supprimé du canal\n"
                            f"👤 Nom : {full_name}\n"
                            f"🔗 Username : {username}\n"
                            f"🆔 ID : `{user_id}`\n"
                            f"📞 Téléphone : `{phone}`\n\n"
                            f"📨 Message :\n{value.get('text', 'Non disponible')}"
                        ),
                        parse_mode="Markdown"
                    )

                    await query.edit_message_text("🗑 Message supprimé du canal.")
                    del message_links[log_id]
                    break
            else:
                await query.edit_message_text("⚠️ Message non reconnu.")

        else:
            raise ValueError(f"Action inconnue : {action}")

    except Exception:
        logging.exception("Erreur parsing callback_data :")
        await query.edit_message_text("⚠️ Erreur dans les données du bouton.")
        return

    if action == "confirm":
        key = (chat_id, message_id)
        original = pending_messages.get(key)

        if not original:
            await query.edit_message_text("❌ Erreur : message introuvable ou expiré.")
            return

        sent = await context.bot.send_message(
            chat_id=CHANNEL_ID,
            text=original.text,
            disable_notification=True
        )

        context.application.create_task(auto_delete_message(context, sent.message_id))

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🗑 Poubelle", callback_data=f"delete|{sent.message_id}"),
                InlineKeyboardButton("🚫 Sup & Ban", callback_data=f"deleteban|{sent.message_id}|{user.id}"),
                InlineKeyboardButton("🔎 Voir utilisateur", url=f"tg://user?id={user.id}")
            ]
        ])

        full_name = f"{user.first_name} {user.last_name}" if user.last_name else user.first_name
        phone = user_contacts.get(str(user.id), "Non fourni")
        log_text = (
            f"✉️ Message envoyé par : {full_name} (@{user.username if user.username else 'aucun'})\n"
            f"ID : `{user.id}`\n"
            f"📞 Téléphone : `{phone}`\n\n"
            f"📨 Message :\n{original.text}"
        )

        try:
            await context.bot.send_message(
                chat_id=ADMIN_LOG_GROUP_ID,
                text=log_text,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
            logging.info("✅ Log envoyé au groupe admin.")
        except Exception as e:
            logging.error(f"❌ Erreur lors de l'envoi au groupe admin : {e}")

        message_links[message_id] = {
    "canal_id": sent.message_id,
    "user_id": user.id,
    "text": original.text
}

        del pending_messages[key]
        await query.edit_message_text("✅ Message envoyé avec succès. Merci beaucoup pour ta participation =)")

    elif action == "cancel":
        await query.edit_message_text("❌ Message annulé.")

# === MAIN ===
if __name__ == '__main__':
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_callback))

    print("Bot démarré...")
    app.run_polling()

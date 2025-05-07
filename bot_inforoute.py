from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# === Configuration ===
TOKEN = "7769744871:AAE06AQib-Ww943VGK64KxqC3lmTeHaLa-Q"
PRIVATE_GROUP_ID = -1002617454677
ADMIN_CHAT_ID = 5221384710

# Dictionnaire temporaire pour les messages en attente
pending_messages = {}

# Commande de démarrage
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Envoie-moi un message que tu souhaites publier anonymement.")

# Traitement des messages texte
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    pending_messages[user_id] = update.message.text

    keyboard = [
        [InlineKeyboardButton("✅ Oui, je veux envoyer ce message !", callback_data="confirm")],
        [InlineKeyboardButton("❌ Non ! Je me suis trompé !", callback_data="cancel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "Souhaites-tu envoyer ce message anonymement ?",
        reply_markup=reply_markup
    )

# Gestion des boutons inline
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    user_id = user.id

    if query.data == "confirm":
        message_text = pending_messages.pop(user_id, None)
        if message_text:
            # Envoi dans le groupe privé
            await context.bot.send_message(chat_id=PRIVATE_GROUP_ID, text=message_text)

            # Informations pour l'admin
            username = f"@{user.username}" if user.username else "(aucun username)"
            full_name = user.full_name
            telegram_id = user.id

            admin_message = (
                f"🔔 Message envoyé par : {full_name} ({username}) — ID: {telegram_id}"
            )
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_message)

            await query.edit_message_text("✅ Ton message a bien été envoyé anonymement.")
        else:
            await query.edit_message_text("❌ Une erreur est survenue. Aucun message trouvé.")
    elif query.data == "cancel":
        pending_messages.pop(user_id, None)
        await query.edit_message_text("🚫 Message annulé.")

# Lancement du bot
def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button))

    print("✅ Bot démarré...")
    app.run_polling()

if __name__ == "__main__":
    main()


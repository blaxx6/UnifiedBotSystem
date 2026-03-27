import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

# ---------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------
import os

# REPLACE THIS WITH YOUR ACTUAL TOKEN FROM BOTFATHER
# Better yet, use environment variables: export TELEGRAM_BOT_TOKEN="your_token"
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN_HERE")

# ---------------------------------------------------------
# LOGGING SETUP (To see errors in console)
# ---------------------------------------------------------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# ---------------------------------------------------------
# BOT FUNCTIONS
# ---------------------------------------------------------

# 1. Handle the /start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(
        chat_id=update.effective_chat.id, 
        text="Namaste! I am your Indic AI Assistant. Send me a message!"
    )

# 2. Handle text messages (The "Echo" part)
async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    print(f"User said: {user_text}") # Print to your console
    
    # Send the text back to the user
    await context.bot.send_message(
        chat_id=update.effective_chat.id, 
        text=f"You said: {user_text}"
    )

# ---------------------------------------------------------
# MAIN APPLICATION
# ---------------------------------------------------------
if __name__ == '__main__':
    # Build the application
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Add handlers
    start_handler = CommandHandler('start', start)
    echo_handler = MessageHandler(filters.TEXT & (~filters.COMMAND), echo)
    
    application.add_handler(start_handler)
    application.add_handler(echo_handler)
    
    print("Bot is polling... (Press Ctrl+C to stop)")
    # Run the bot
    application.run_polling()
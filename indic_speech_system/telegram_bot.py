from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from bot_handler import BotHandler
from config import Config
from contacts_manager import log_contact
from database import db
import os

class TelegramBot:
    def __init__(self):
        self.handler = BotHandler()
        self.app = Application.builder().token(Config.TELEGRAM_BOT_TOKEN).build()
        self.setup_handlers()
    
    def setup_handlers(self):
        self.app.add_handler(CommandHandler("start", self.start_command))
        self.app.add_handler(MessageHandler(filters.VOICE, self.handle_voice))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text))
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        log_contact("telegram", user.id, user.full_name)
        await update.message.reply_text("🎤 Welcome! Send a message to start.")

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        text = update.message.text
        
        # Log & Save Incoming
        log_contact("telegram", user.id, user.full_name)
        db.save_message(
            platform='telegram',
            user_id=str(user.id),
            user_name=user.full_name,
            message_text=text,
            direction='incoming',
            message_type='text'
        )
        
        # Process & Save Reply
        result = await self.handler.process_text_message(user.id, text)
        await update.message.reply_text(result['message'])
        
        db.save_message(
            platform='telegram',
            user_id=str(user.id),
            user_name="Bot",
            message_text=result['message'],
            direction='outgoing',
            message_type='text'
        )

    async def handle_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        log_contact("telegram", user.id, user.full_name)
        
        db.save_message(
            platform='telegram',
            user_id=str(user.id),
            user_name=user.full_name,
            message_text="[Voice Message]",
            direction='incoming',
            message_type='audio'
        )

        processing_msg = await update.message.reply_text("⏳ Processing...")
        
        try:
            voice_file = await update.message.voice.get_file()
            file_path = f"{self.handler.temp_dir}/{user.id}.ogg"
            await voice_file.download_to_drive(file_path)
            wav_path = file_path.replace('.ogg', '.wav')
            os.system(f"ffmpeg -i {file_path} -ar 16000 -ac 1 {wav_path} -y 2>/dev/null")

            result = await self.handler.process_voice_message(user.id, wav_path)
            
            await processing_msg.delete()
            if result['type'] == 'audio':
                await update.message.reply_voice(voice=open(result['audio_path'], 'rb'), caption=result['message'])
            else:
                await update.message.reply_text(result['message'])
                
            db.save_message(
                platform='telegram',
                user_id=str(user.id),
                user_name="Bot",
                message_text=result['message'],
                direction='outgoing',
                message_type='text'
            )
            
            if os.path.exists(file_path): os.remove(file_path)
            if os.path.exists(wav_path): os.remove(wav_path)

        except Exception as e:
            await processing_msg.edit_text(f"❌ Error: {str(e)}")

    def run(self):
        print("🤖 Telegram Bot Running...")
        self.app.run_polling()

if __name__ == "__main__":
    bot = TelegramBot()
    bot.run()
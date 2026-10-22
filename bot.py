import os
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Импортируем наши новые модули
from garmin_client import GarminClient
from workout_builder import build_garmin_workout
from ai_parser import AIParser

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Загрузка переменных окружения (Render)
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
PORT = int(os.environ.get("PORT", 10000))

# --- ЗАГЛУШКА ДЛЯ RENDER ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

def run_dummy_server():
    server = HTTPServer(('0.0.0.0', PORT), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()
# ---------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Я готов. Надиктуй беговую тренировку голосом или напиши текстом.")

async def process_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Если бот дошел до сюда, значит он жив и слышит Telegram
    status_msg = await update.message.reply_text("1️⃣ Получил сообщение! Инициализирую нейросеть...")
    
    try:
        ai = AIParser(api_key=GEMINI_API_KEY)
        
        if update.message.voice:
            await status_msg.edit_text("2️⃣ Скачиваю аудиофайл с серверов Telegram...")
            file = await context.bot.get_file(update.message.voice.file_id)
            voice_path = "temp_voice.ogg"
            await file.download_to_drive(voice_path)
            
            await status_msg.edit_text("3️⃣ Отправил аудио в Gemini, жду ответ (это может занять пару секунд)...")
            parsed_result = ai.parse_audio(voice_path)
            os.remove(voice_path)
        else:
            await status_msg.edit_text("2️⃣ Отправил текст в Gemini, жду ответ...")
            parsed_result = ai.parse_text(update.message.text)
            
        await status_msg.edit_text(f"4️⃣ Нейросеть ответила! Собираю JSON для Garmin...\nНазвание: {parsed_result.get('workoutName')}")
        
        scheduled_date = parsed_result.get("scheduledDate")
        garmin_payload = build_garmin_workout(parsed_result)
        
        await status_msg.edit_text("5️⃣ Стучусь в Garmin (SSO-авторизация)...")
        client = GarminClient(username=GARMIN_EMAIL, password=GARMIN_PASSWORD)
        client.login()
        
        await status_msg.edit_text("6️⃣ Авторизация успешна! Отправляю тренировку на сервер...")
        workout_id = client.save_workout(garmin_payload)
        
        reply_text = f"✅ Тренировка «{parsed_result.get('workoutName')}» успешно создана!"
        
        if scheduled_date and workout_id:
            await status_msg.edit_text(f"7️⃣ Тренировка сохранена. Добавляю в календарь на {scheduled_date}...")
            client.schedule_workout(workout_id, scheduled_date)
            reply_text += f"\n📅 Добавлена в календарь на: **{scheduled_date}**"
            
        await status_msg.edit_text(reply_text, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Ошибка в процессе обработки: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ Застрял на текущем шаге. Текст ошибки:\n`{str(e)}`", parse_mode="Markdown")
            
        # 3. Собираем JSON для Garmin
        scheduled_date = parsed_result.get("scheduledDate")
        garmin_payload = build_garmin_workout(parsed_result)
        
        await status_msg.edit_text("🔄 Отправляю тренировку в Garmin Connect...")
        
        # 4. Авторизуемся и отправляем данные через наш новый клиент
        client = GarminClient(username=GARMIN_EMAIL, password=GARMIN_PASSWORD)
        client.login()
        
        workout_id = client.save_workout(garmin_payload)
        
        reply_text = f"✅ Тренировка «{parsed_result.get('workoutName')}» успешно создана!"
        
        # 5. Планируем в календарь, если есть дата
        if scheduled_date and workout_id:
            client.schedule_workout(workout_id, scheduled_date)
            reply_text += f"\n📅 Добавлена в календарь на: **{scheduled_date}**"
            
        await status_msg.edit_text(reply_text, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Ошибка в процессе обработки: {e}")
        await status_msg.edit_text(f"❌ Произошла ошибка: {str(e)}")

def main():
    if not all([TELEGRAM_TOKEN, GEMINI_API_KEY, GARMIN_EMAIL, GARMIN_PASSWORD]):
        logger.error("Отсутствуют необходимые переменные окружения!")
        return

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.VOICE | filters.TEXT, process_message))
    
    logger.info("Бот запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()

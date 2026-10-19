import os
import json
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from groq import Groq
from garminconnect import Garmin

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Переменные окружения
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")

groq_client = Groq(api_key=GROQ_API_KEY)

# Подключение к Garmin
def get_garmin_client():
    client = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    client.login()
    return client

SYSTEM_PROMPT = """
Ты — ассистент для создания тренировок Garmin. Твоя задача — распарсить текст/расшифровку голоса бегуна и вернуть строго JSON следующей структуры для Garmin Connect API:

{
  "workoutName": "Название (например: 6x1200m @ 5:00)",
  "description": "Описание тренировки",
  "sportType": "running",
  "steps": [
    {"type": "warmup", "endCondition": "distance", "value": 1500},
    {"type": "repeat", "iterations": 6, "steps": [
      {"type": "interval", "endCondition": "distance", "value": 1200, "targetType": "pace", "targetLow": 4.55, "targetHigh": 5.05},
      {"type": "recovery", "endCondition": "time", "value": 120}
    ]},
    {"type": "cooldown", "endCondition": "distance", "value": 1500}
  ]
}

Правила:
1. Дистанция в метрах (1.5 км = 1500).
2. Время в секундах (2 мин = 120).
3. Темп переводи в формат мин.сек/км или целевой диапазон pace (targetLow и targetHigh в формате float, например 5.00 -> 5.0).
4. Отвечай ТОЛЬКО валидным JSON без лишнего текста и markdown-оформления.
"""

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Надиктуй или напиши мне тренировку, например: 'Интервалы 6 по 1200 темп 5:00, разминка и заминка 1.5 км, отдых 2 минуты шагом'.")

async def process_voice_or_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = await update.message.reply_text("⏳ Обрабатываю...")
    
    try:
        # Если пришел голос
        if update.message.voice:
            file = await context.bot.get_file(update.message.voice.file_id)
            voice_path = "voice.ogg"
            await file.download_to_drive(voice_path)
            
            # Расшифровка через Groq Whisper
            with open(voice_path, "rb") as audio_file:
                transcription = groq_client.audio.transcriptions.create(
                    file=(voice_path, audio_file.read()),
                    model="whisper-large-v3-turbo",
                    language="ru"
                )
            text_prompt = transcription.text
            os.remove(voice_path)
            await status_msg.edit_text(f"🗣 Распознано: «{text_prompt}»\n\nСоздаю структуру тренировки...")
        else:
            text_prompt = update.message.text

        # Парсинг через Llama 3 в Groq
        chat_completion = groq_client.chat.completions.create(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text_prompt}
            ],
            model="llama-3.3-70b-versatile",
            response_format={"type": "json_object"}
        )
        
        workout_data = json.loads(chat_completion.choices[0].message.content)
        
        # Отправка в Garmin Connect
        await status_msg.edit_text("🔄 Подключаюсь к Garmin Connect...")
        garmin = get_garmin_client()
        
        # Загрузка тренировки в Garmin
        garmin.create_workout(workout_data)
        
        await status_msg.edit_text(f"✅ Тренировка «{workout_data.get('workoutName', 'Интервалы')}» успешно создана в Garmin Connect!\n\nОткрой приложение Garmin Connect на телефоне и синхронизируй часы.")

    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await status_msg.edit_text(f"❌ Ошибка при создании тренировки: {str(e)}")

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.VOICE | filters.TEXT, process_voice_or_text))
    
    print("Бот запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()
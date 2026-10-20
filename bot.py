import os
import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from groq import Groq
from garminconnect import Garmin

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
PORT = int(os.environ.get("PORT", 10000))

groq_client = Groq(api_key=GROQ_API_KEY)

# --- ЗАГЛУШКА ДЛЯ RENDER (чтобы не отключал бота) ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

def run_dummy_server():
    server = HTTPServer(('0.0.0.0', PORT), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()
# ---------------------------------------------------

def get_garmin_client():
    client = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    client.login()
    return client

# --- СБОРКА GARMIN JSON ---
def build_garmin_workout(parsed_json):
    """Превращает простой ответ от LLM в сложный формат Garmin API"""
    workout = {
        "sport": "RUNNING",
        "workoutName": parsed_json["workoutName"],
        "workoutSegments": [{"segmentOrder": 1, "sport": "RUNNING", "workoutSteps": []}]
    }
    
    step_order = 1
    garmin_steps = workout["workoutSegments"][0]["workoutSteps"]
    
    def create_step(step_data, order):
        type_map = {"warmup": (1, "warmup"), "cooldown": (2, "cooldown"), "active": (3, "active"), "recovery": (4, "recovery")}
        step_type_id, step_type_key = type_map.get(step_data.get("type", "active"), (3, "active"))
        
        dur_map = {"time": (2, "time"), "distance": (3, "distance"), "lap": (1, "lap.button")}
        cond_id, cond_key = dur_map.get(step_data.get("duration_type", "lap"), (1, "lap.button"))
        
        step = {
            "type": "ExecutableStepDTO",
            "stepOrder": order,
            "stepType": {"stepTypeId": step_type_id, "stepTypeKey": step_type_key},
            "endCondition": {"conditionTypeId": cond_id, "conditionTypeKey": cond_key},
            "endConditionValue": step_data.get("duration_value"),
            "targetType": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
            "targetValueOne": None,
            "targetValueTwo": None
        }
        
        # Настройка целей (Пульс или Темп)
        t_type = step_data.get("target_type")
        if t_type == "hr":
            step["targetType"] = {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.bpm"}
            step["targetValueOne"] = step_data.get("target_low")
            step["targetValueTwo"] = step_data.get("target_high")
        elif t_type == "pace":
            step["targetType"] = {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone"}
            sec_low = step_data.get("target_low_sec")
            sec_high = step_data.get("target_high_sec")
            # Перевод секунд на километр в метры в секунду для Garmin
            if sec_high: step["targetValueOne"] = round(1000.0 / sec_high, 3) # Медленная граница
            if sec_low: step["targetValueTwo"] = round(1000.0 / sec_low, 3)   # Быстрая граница
            
        return step

    for step_data in parsed_json.get("steps", []):
        if step_data.get("type") == "repeat":
            repeat_step = {
                "type": "RepeatGroupDTO",
                "stepOrder": step_order,
                "numberOfIterations": step_data.get("iterations", 1),
                "workoutSteps": []
            }
            step_order += 1
            for sub_step in step_data.get("steps", []):
                repeat_step["workoutSteps"].append(create_step(sub_step, step_order))
                step_order += 1
            garmin_steps.append(repeat_step)
        else:
            garmin_steps.append(create_step(step_data, step_order))
            step_order += 1
            
    return workout

# --- ОТПРАВКА В GARMIN ---
def save_and_schedule(garmin, workout_json, scheduled_date):
    # Прямой и самый надежный запрос в Garmin API
    resp = garmin.garth.client.post("connectapi", "/workout-service/workout", json=workout_json)
    workout_id = resp.get("workoutId") if isinstance(resp, dict) else resp.json().get("workoutId")
    
    if scheduled_date and workout_id:
        garmin.garth.client.post("connectapi", f"/workout-service/schedule/{workout_id}", json={"date": scheduled_date})
    return workout_id

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Я готов. Надиктуй тренировку (например: 'Завтра бежим 100 минут с пульсом 165-175, добавь разминку 5 минут').")

async def process_voice_or_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_msg = await update.message.reply_text("⏳ Думаю...")
    
    try:
        if update.message.voice:
            file = await context.bot.get_file(update.message.voice.file_id)
            voice_path = "voice.ogg"
            await file.download_to_drive(voice_path)
            
            with open(voice_path, "rb") as audio_file:
                transcription = groq_client.audio.transcriptions.create(
                    file=(voice_path, audio_file.read()),
                    model="whisper-large-v3-turbo",
                    language="ru"
                )
            text_prompt = transcription.text
            os.remove(voice_path)
            await status_msg.edit_text(f"🗣 Распознано: «{text_prompt}»\n\nРазбираю структуру...")
        else:
            text_prompt = update.message.text

        # Получаем правильное время по часовому поясу пользователя (UTC+5)
        tz = timezone(timedelta(hours=5))
        now = datetime.now(tz)
        today_str = now.strftime("%Y-%m-%d")
        
        system_prompt = f"""
Ты — продвинутый ассистент бегуна. Сегодняшняя дата: {today_str} (UTC+5).
Проанализируй запрос и верни СТРОГО JSON без лишних слов.

Формат JSON:
{{
  "workoutName": "Короткое название",
  "scheduledDate": "YYYY-MM-DD или null",
  "steps": [
    // Шаги могут быть типа "warmup", "active", "recovery", "cooldown" или "repeat"
    // Если просят разминку/заминку, обязательно добавляй warmup/cooldown.
  ]
}}

Пример обычного шага (duration_type: "time" или "distance"):
{{"type": "active", "duration_type": "time", "duration_value": 6000, "target_type": "hr", "target_low": 165, "target_high": 175}}

Пример интервалов (repeat):
{{
  "type": "repeat", "iterations": 4, "steps": [
    {{"type": "active", "duration_type": "distance", "duration_value": 400, "target_type": "hr", "target_low": 0, "target_high": 170}},
    {{"type": "recovery", "duration_type": "distance", "duration_value": 100, "target_type": "none"}}
  ]
}}

Правила перевода значений:
1. duration_value: время всегда в секундах (100 мин = 6000, 5 мин = 300). Дистанция всегда в метрах.
2. target_type: может быть "none", "hr" или "pace".
3. Для пульса (hr): указывай target_low (нижний порог) и target_high (верхний порог). Если сказано "до 170", ставь target_low=0, target_high=170.
4. Для темпа (pace): переведи целевой темп в секунды/км. Например, темп 5:00 = 300 сек, 5:20 = 320 сек. Укажи их в "target_low_sec" и "target_high_sec".
5. Даты: "завтра", "послезавтра", "21 октября" переводи в YYYY-MM-DD относительно {today_str}.
"""

        chat_completion = groq_client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text_prompt}
            ],
            model="llama3-70b-8192",
            response_format={"type": "json_object"}
        )
        
        parsed_result = json.loads(chat_completion.choices[0].message.content)
        scheduled_date = parsed_result.get("scheduledDate")
        
        # Переводим простой JSON в монструозный формат Garmin
        garmin_payload = build_garmin_workout(parsed_result)
        
        await status_msg.edit_text("🔄 Отправляю в Garmin Connect...")
        garmin = get_garmin_client()
        
        # Отправляем и ставим в календарь
        save_and_schedule(garmin, garmin_payload, scheduled_date)
        
        reply_text = f"✅ Тренировка «{parsed_result.get('workoutName')}» успешно создана!"
        if scheduled_date:
            reply_text += f"\n📅 Добавлена в календарь на: **{scheduled_date}**"
            
        await status_msg.edit_text(reply_text, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await status_msg.edit_text(f"❌ Ошибка: {str(e)}")

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.VOICE | filters.TEXT, process_voice_or_text))
    
    print("Бот запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()

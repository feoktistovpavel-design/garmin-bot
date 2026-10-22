import os
import json
import logging
from datetime import datetime, timedelta, timezone
import google.generativeai as genai

logger = logging.getLogger(__name__)

class AIParser:
    def __init__(self, api_key):
        genai.configure(api_key=api_key)
        # Используем мощную модель, согласованную ранее
        self.model = genai.GenerativeModel(
            'gemini-3.8-flash', 
            generation_config={"response_mime_type": "application/json"}
        )

    def _get_system_prompt(self):
        # Динамически подставляем дату и часовой пояс UTC+5 (Ташкент/Астана)
        tz = timezone(timedelta(hours=5))
        today_str = datetime.now(tz).strftime("%Y-%m-%d")
        
        return f"""
Ты — продвинутый ассистент бегуна. Сегодняшняя дата: {today_str} (UTC+5).
Проанализируй текст или аудио и верни СТРОГО JSON без лишних слов.

Формат JSON:
{{
  "workoutName": "Короткое название",
  "scheduledDate": "YYYY-MM-DD или null",
  "steps": [
    // Шаги могут быть типа "warmup", "active", "recovery", "cooldown" или "repeat"
  ]
}}

Пример обычного шага:
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
3. Для пульса (hr): указывай target_low и target_high. Если сказано "до 170", ставь target_low=0, target_high=170.
4. Для темпа (pace): переведи темп в секунды/км (напр. 5:00 = 300). Укажи их в "target_low_sec" и "target_high_sec".
5. Даты: "завтра", "послезавтра", "21 октября" переводи в YYYY-MM-DD относительно {today_str}.
"""

    def parse_text(self, text):
        """Отправляет текстовый запрос в Gemini."""
        prompt = self._get_system_prompt()
        response = self.model.generate_content([prompt, text])
        return json.loads(response.text)

    def parse_audio(self, audio_path):
        """Отправляет аудиофайл в Gemini напрямую."""
        prompt = self._get_system_prompt()
        audio_file = genai.upload_file(path=audio_path)
        
        try:
            response = self.model.generate_content([prompt, audio_file])
            return json.loads(response.text)
        finally:
            # Обязательно удаляем файл с серверов Google после обработки
            audio_file.delete()
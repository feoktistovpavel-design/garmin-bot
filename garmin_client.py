import re
import logging
import cloudscraper

logger = logging.getLogger(__name__)

class GarminClient:
    _WORKOUT_SERVICE_ENDPOINT = "/proxy/workout-service"
    _REQUIRED_HEADERS = {"Referer": "https://connect.garmin.com/modern/workouts", "nk": "NT"}

    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.connect_url = "https://connect.garmin.com"
        self.sso_url = "https://sso.garmin.com"
        self.session = cloudscraper.CloudScraper()
        
    def login(self):
        """Проверяет текущую сессию и при необходимости авторизуется."""
        url = self.connect_url + "/modern/settings"
        response = self.session.get(url, allow_redirects=False)
        if response.status_code != 200:
            self._authenticate()

    def _authenticate(self):
        """Процесс SSO-авторизации и извлечения тикета (из кода mkuthan)."""
        logger.info("Начинаем авторизацию в Garmin SSO...")
        url = self.sso_url + "/sso/signin"
        headers = {"origin": "https://sso.garmin.com"}
        params = {"service": "https://connect.garmin.com/modern"}
        data = {"username": self.username, "password": self.password, "embed": "false"}

        auth_response = self.session.post(url, headers=headers, params=params, data=data)
        auth_response.raise_for_status()

        # Вытаскиваем тикет авторизации регулярным выражением
        match = re.search(r'response_url\s*=\s*".*\?ticket=(.+)"', auth_response.text)
        if not match:
            raise ValueError("Не удалось извлечь auth ticket из ответа Garmin.")
        
        auth_ticket = match.group(1)
        
        # Завершаем вход
        response = self.session.get(self.connect_url + "/modern", params={"ticket": auth_ticket})
        response.raise_for_status()
        logger.info("Авторизация успешно завершена!")

    def save_workout(self, workout_json):
        """Сохраняет готовую тренировку в профиль."""
        url = f"{self.connect_url}{self._WORKOUT_SERVICE_ENDPOINT}/workout"
        response = self.session.post(url, headers=self._REQUIRED_HEADERS, json=workout_json)
        response.raise_for_status()
        
        # Возвращаем ID созданной тренировки
        return response.json().get("workoutId")

    def schedule_workout(self, workout_id, date):
        """Добавляет тренировку в календарь на указанную дату."""
        url = f"{self.connect_url}{self._WORKOUT_SERVICE_ENDPOINT}/schedule/{workout_id}"
        json_data = {"date": date}
        response = self.session.post(url, headers=self._REQUIRED_HEADERS, json=json_data)
        response.raise_for_status()
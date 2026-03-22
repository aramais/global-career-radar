from __future__ import annotations

import os

import requests

from job_intake.config.settings import TelegramConfig


class TelegramNotifier:
    def __init__(self, config: TelegramConfig) -> None:
        self.config = config

    def send(self, message: str) -> bool:
        if not self.config.enabled:
            return False
        token = os.getenv(self.config.bot_token_env)
        chat_id = os.getenv(self.config.chat_id_env)
        if not token or not chat_id:
            return False
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "disable_web_page_preview": True},
            timeout=20,
        )
        response.raise_for_status()
        return True

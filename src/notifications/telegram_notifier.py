from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class TelegramTarget:
    chat_id: str


class TelegramNotifier:
    def __init__(self, bot_token: str, primary_chat_id: str, secondary_chat_id: str | None = None) -> None:
        self.bot_token = bot_token
        self.targets = [TelegramTarget(primary_chat_id)]
        if secondary_chat_id:
            self.targets.append(TelegramTarget(secondary_chat_id))

    def send(self, message: str) -> list[dict]:
        return [self._send_to_chat(target.chat_id, message) for target in self.targets]

    def _send_to_chat(self, chat_id: str, message: str) -> dict:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message,
            "disable_web_page_preview": True,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            body = response.read().decode("utf-8")
        return {"channel": "telegram", "target": chat_id, "response": body}

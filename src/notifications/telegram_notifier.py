from __future__ import annotations

import json
import urllib.request


class TelegramNotifier:
    def __init__(self, bot_token: str, primary_chat_id: str, secondary_chat_id: str | None = None) -> None:
        self.bot_token = bot_token
        self.primary_chat_id = primary_chat_id
        self.secondary_chat_id = secondary_chat_id

    def send(self, message: str) -> list[dict]:
        outputs = [self._send_to_chat(self.primary_chat_id, message)]
        if self.secondary_chat_id:
            outputs.append(self._send_to_chat(self.secondary_chat_id, message))
        return outputs

    def _send_to_chat(self, chat_id: str, message: str) -> dict:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = json.dumps({"chat_id": chat_id, "text": message}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
        return {"channel": "telegram", "chat_id": chat_id, "response": body}

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class ResendTarget:
    email: str


class ResendNotifier:
    def __init__(self, api_key: str, sender: str, primary_email: str, secondary_email: str | None = None) -> None:
        self.api_key = api_key
        self.sender = sender
        self.targets = [ResendTarget(primary_email)]
        if secondary_email:
            self.targets.append(ResendTarget(secondary_email))

    def send(self, subject: str, message: str) -> list[dict]:
        return [self._send(target.email, subject, message) for target in self.targets]

    def _send(self, recipient: str, subject: str, message: str) -> dict:
        payload = {
            "from": self.sender,
            "to": [recipient],
            "subject": subject,
            "text": message,
        }
        req = urllib.request.Request(
            "https://api.resend.com/emails",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            body = response.read().decode("utf-8")
        return {"channel": "resend", "target": recipient, "response": body}

from __future__ import annotations

import json
import urllib.request


class ResendNotifier:
    def __init__(self, api_key: str, sender: str, primary_email: str, secondary_email: str | None = None) -> None:
        self.api_key = api_key
        self.sender = sender
        self.primary_email = primary_email
        self.secondary_email = secondary_email

    def send(self, subject: str, message: str) -> list[dict]:
        outputs = [self._send(self.primary_email, subject, message)]
        if self.secondary_email:
            outputs.append(self._send(self.secondary_email, subject, message))
        return outputs

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
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
        return {"channel": "resend", "recipient": recipient, "response": body}

from __future__ import annotations

import urllib.request


class NtfyNotifier:
    def __init__(self, topic_url: str, primary_channel: str, secondary_channel: str | None = None) -> None:
        self.topic_url = topic_url.rstrip("/")
        self.primary_channel = primary_channel
        self.secondary_channel = secondary_channel

    def send(self, message: str, title: str = "floor-alert") -> list[dict]:
        outputs = [self._send(self.primary_channel, message, title)]
        if self.secondary_channel:
            outputs.append(self._send(self.secondary_channel, message, title))
        return outputs

    def _send(self, channel: str, message: str, title: str) -> dict:
        url = f"{self.topic_url}/{channel}"
        req = urllib.request.Request(
            url,
            data=message.encode("utf-8"),
            headers={"Title": title, "Content-Type": "text/plain; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
        return {"channel": "ntfy", "target": channel, "response": body}

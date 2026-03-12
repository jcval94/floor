from __future__ import annotations

import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class NtfyTarget:
    channel: str


class NtfyNotifier:
    def __init__(self, topic_url: str, primary_channel: str, secondary_channel: str | None = None) -> None:
        self.topic_url = topic_url.rstrip("/")
        self.targets = [NtfyTarget(primary_channel)]
        if secondary_channel:
            self.targets.append(NtfyTarget(secondary_channel))

    def send(self, message: str, title: str = "floor-alert") -> list[dict]:
        return [self._send(target.channel, message, title) for target in self.targets]

    def _send(self, channel: str, message: str, title: str) -> dict:
        url = f"{self.topic_url}/{channel}"
        req = urllib.request.Request(
            url,
            data=message.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": "default",
                "Content-Type": "text/plain; charset=utf-8",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            body = response.read().decode("utf-8")
        return {"channel": "ntfy", "target": channel, "response": body}

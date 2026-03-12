from __future__ import annotations

import hashlib
import json
from pathlib import Path


class HistoryWriter:
    def __init__(self, root_dir: str) -> None:
        self.root = Path(root_dir)
        self.root.mkdir(parents=True, exist_ok=True)

    def write_snapshot(self, namespace: str, date: str, session: str, payload: dict) -> dict:
        ns_dir = self.root / namespace / date
        ns_dir.mkdir(parents=True, exist_ok=True)

        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

        file_name = f"{date}_{session}_{digest[:10]}.json"
        target = ns_dir / file_name

        if target.exists():
            return {"path": str(target), "written": False, "reason": "duplicate"}

        # avoid duplicate content under same date/session
        for existing in ns_dir.glob(f"{date}_{session}_*.json"):
            data = existing.read_text(encoding="utf-8")
            existing_digest = hashlib.sha256(data.encode("utf-8")).hexdigest()
            if existing_digest == digest:
                return {"path": str(existing), "written": False, "reason": "duplicate"}

        target.write_text(canonical + "\n", encoding="utf-8")
        latest = self.root / namespace / "latest.json"
        latest.write_text(canonical + "\n", encoding="utf-8")
        return {"path": str(target), "written": True, "reason": "new"}

    def write_daily_summary(self, date: str, summary: dict) -> str:
        d = self.root / "summaries" / "daily"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{date}.json"
        p.write_text(json.dumps(summary, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        return str(p)

    def write_weekly_summary(self, week_id: str, summary: dict) -> str:
        d = self.root / "summaries" / "weekly"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{week_id}.json"
        p.write_text(json.dumps(summary, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        return str(p)

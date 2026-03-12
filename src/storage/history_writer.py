from __future__ import annotations

import hashlib
import json
from pathlib import Path


class HistoryWriter:
    def __init__(self, root_dir: str) -> None:
        self.root = Path(root_dir)
        self.root.mkdir(parents=True, exist_ok=True)

    def write_snapshot(self, namespace: str, date: str, session: str, payload: dict) -> dict:
        ns_root = self.root / namespace
        ns_dir = ns_root / date / session
        ns_dir.mkdir(parents=True, exist_ok=True)

        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        filename = f"snapshot_{date}_{session}_{digest[:12]}.json"
        target = ns_dir / filename

        if target.exists():
            return {"path": str(target), "written": False, "reason": "duplicate"}

        for existing in ns_dir.glob(f"snapshot_{date}_{session}_*.json"):
            existing_canonical = existing.read_text(encoding="utf-8").strip()
            existing_digest = hashlib.sha256(existing_canonical.encode("utf-8")).hexdigest()
            if existing_digest == digest:
                return {"path": str(existing), "written": False, "reason": "duplicate"}

        target.write_text(canonical + "\n", encoding="utf-8")

        latest = ns_root / "latest.json"
        latest.write_text(canonical + "\n", encoding="utf-8")

        manifest = ns_root / "manifest.jsonl"
        manifest_record = {
            "date": date,
            "session": session,
            "digest": digest,
            "path": str(target),
        }
        with manifest.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(manifest_record, sort_keys=True, ensure_ascii=False) + "\n")

        return {"path": str(target), "written": True, "reason": "new"}

    def write_daily_summary(self, date: str, summary: dict) -> str:
        path = self.root / "summaries" / "daily" / f"{date}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        return str(path)

    def write_weekly_summary(self, week_id: str, summary: dict) -> str:
        path = self.root / "summaries" / "weekly" / f"{week_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        return str(path)

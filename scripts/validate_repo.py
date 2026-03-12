from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _run(cmd: list[str]) -> int:
    print("$", " ".join(cmd))
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src") + (":" + env.get("PYTHONPATH", "") if env.get("PYTHONPATH") else "")
    return subprocess.run(cmd, cwd=ROOT, env=env).returncode


def validate_configs() -> list[str]:
    errors: list[str] = []
    config_dir = ROOT / "config"
    if not config_dir.exists():
        errors.append("config directory missing")
        return errors
    for path in sorted(config_dir.glob("*.yaml")):
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            errors.append(f"empty config file: {path}")
        if ":" not in content:
            errors.append(f"invalid yaml-like structure: {path}")
    return errors


def validate_dataset_schemas() -> list[str]:
    errors: list[str] = []
    latest_dir = ROOT / "site" / "data"
    if not latest_dir.exists():
        return errors

    required = {
        "dashboard.json": {"system_health"},
        "metrics.json": {"status"},
        "strategy.json": {"status"},
    }

    for file_name, keys in required.items():
        p = latest_dir / file_name
        if not p.exists():
            errors.append(f"missing dataset: {p}")
            continue
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            errors.append(f"invalid json: {p}")
            continue
        missing = [k for k in keys if k not in payload]
        if missing:
            errors.append(f"dataset {p} missing keys: {missing}")
    return errors


def review_secrets_and_permissions() -> list[str]:
    warnings: list[str] = []
    secret_patterns = ["BEGIN PRIVATE KEY", "api_key", "secret", "password"]
    scan_paths = [ROOT / "src", ROOT / "config", ROOT / "site"]

    for base in scan_paths:
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_dir() or path.suffix in {".png", ".jpg", ".pdf", ".pyc"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
            for pattern in secret_patterns:
                if pattern.lower() in text and "_env" not in text:
                    warnings.append(f"potential secret pattern '{pattern}' in {path.relative_to(ROOT)}")
                    break

    for path in (ROOT / "scripts").glob("*.py"):
        mode = os.stat(path).st_mode & 0o777
        if mode & 0o002:
            warnings.append(f"insecure writable-by-others script permissions: {path.relative_to(ROOT)} ({oct(mode)})")
    return warnings


def run_smoke() -> int:
    return _run([sys.executable, "-m", "floor.main", "--help"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true", help="Run smoke test only")
    args = parser.parse_args()

    if args.smoke:
        return run_smoke()

    failed = False

    config_errors = validate_configs()
    schema_errors = validate_dataset_schemas()
    secret_warnings = review_secrets_and_permissions()

    if config_errors:
        failed = True
        print("[FAIL] config validation")
        for err in config_errors:
            print(" -", err)
    else:
        print("[OK] config validation")

    if schema_errors:
        failed = True
        print("[FAIL] dataset schema validation")
        for err in schema_errors:
            print(" -", err)
    else:
        print("[OK] dataset schema validation")

    if secret_warnings:
        print("[WARN] secrets/permissions review")
        for w in secret_warnings:
            print(" -", w)
    else:
        print("[OK] secrets/permissions review")

    # Idempotency and gating-related tests
    test_cmds = [
        ["pytest", "-q", "tests/test_no_leakage.py", "tests/test_session_gating.py"],
        ["pytest", "-q", "tests/test_workflow_guards.py"],
    ]
    for cmd in test_cmds:
        if _run(cmd) != 0:
            failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

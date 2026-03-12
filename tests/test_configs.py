from __future__ import annotations

from pathlib import Path


CONFIG_FILES = [
    "config/costs.yaml",
    "config/horizons.yaml",
    "config/notifications.yaml",
    "config/pages.yaml",
    "config/retraining.yaml",
    "config/risk.yaml",
    "config/sheets.yaml",
    "config/strategies.yaml",
    "config/universe.yaml",
]


def test_all_configs_have_minimal_yaml_shape() -> None:
    for file_path in CONFIG_FILES:
        content = Path(file_path).read_text(encoding="utf-8")
        assert content.strip()
        assert ":" in content


def test_notifications_config_has_channel_like_entries() -> None:
    content = Path("config/notifications.yaml").read_text(encoding="utf-8").lower()
    assert "notifications:" in content
    assert any(k in content for k in ["slack", "email", "telegram", "ntfy", "resend"])

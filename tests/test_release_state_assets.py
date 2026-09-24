from utils.release_state_assets import (
    latest_complete_payload,
    payload_is_complete,
    prune_asset_ids,
)


def _asset(asset_id: int, name: str, updated: str) -> dict:
    return {"id": asset_id, "name": name, "updated_at": updated}


def test_select_ignores_torn_newer_generation() -> None:
    base = "state.tar.gz"
    release = {
        "assets": [
            _asset(1, f"{base}.run-100-a1", "2026-09-23T10:00:00Z"),
            _asset(2, f"{base}.run-100-a1.sha256", "2026-09-23T10:00:01Z"),
            _asset(3, f"{base}.run-101-a1", "2026-09-23T11:00:00Z"),
        ]
    }
    assert latest_complete_payload(release, base=base, suffixes=(".sha256",)) == f"{base}.run-100-a1"


def test_select_requires_all_runtime_companions() -> None:
    base = "runtime.tar.gz"
    release = {
        "assets": [
            _asset(1, f"{base}.run-200-a1", "2026-09-23T10:00:00Z"),
            _asset(2, f"{base}.run-200-a1.sha256", "2026-09-23T10:00:01Z"),
            _asset(3, f"{base}.run-200-a1.metadata.json", "2026-09-23T10:00:02Z"),
        ]
    }
    suffixes = (".sha256", ".metadata.json")
    selected = f"{base}.run-200-a1"
    assert latest_complete_payload(release, base=base, suffixes=suffixes) == selected
    assert payload_is_complete(release, payload_name=selected, suffixes=suffixes)


def test_prune_keeps_two_newest_complete_and_removes_incomplete() -> None:
    base = "state.json"
    release = {
        "assets": [
            _asset(1, f"{base}.run-10-a1", "2026-09-23T10:00:00Z"),
            _asset(2, f"{base}.run-10-a1.sha256", "2026-09-23T10:00:01Z"),
            _asset(3, f"{base}.run-11-a1", "2026-09-23T11:00:00Z"),
            _asset(4, f"{base}.run-11-a1.sha256", "2026-09-23T11:00:01Z"),
            _asset(5, f"{base}.run-12-a1", "2026-09-23T12:00:00Z"),
            _asset(6, f"{base}.run-12-a1.sha256", "2026-09-23T12:00:01Z"),
            _asset(7, f"{base}.run-13-a1", "2026-09-23T13:00:00Z"),
        ]
    }
    assert prune_asset_ids(release, base=base, suffixes=(".sha256",), keep=2) == [1, 2, 7]

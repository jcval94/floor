from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AssetGroup:
    payload: str
    updated_at: str
    assets: dict[str, dict[str, Any]]


def _assets(payload: object) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    raw = payload.get("assets")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def versioned_groups(
    release_payload: object,
    *,
    base: str,
    suffixes: tuple[str, ...],
) -> list[AssetGroup]:
    pattern = re.compile(rf"^{re.escape(base)}\.run-(\d+)(.*)$")
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    allowed_suffixes = {"", *suffixes}

    for asset in _assets(release_payload):
        name = str(asset.get("name") or "")
        match = pattern.match(name)
        if not match:
            continue
        run_id, suffix = match.groups()
        if suffix not in allowed_suffixes:
            continue
        payload_name = f"{base}.run-{run_id}"
        grouped.setdefault(payload_name, {})[suffix] = asset

    out: list[AssetGroup] = []
    for payload_name, members in grouped.items():
        if "" not in members or any(suffix not in members for suffix in suffixes):
            continue
        updated = max(str(item.get("updated_at") or item.get("created_at") or "") for item in members.values())
        out.append(AssetGroup(payload_name, updated, members))

    out.sort(key=lambda item: (item.updated_at, item.payload), reverse=True)
    return out


def latest_complete_payload(
    release_payload: object,
    *,
    base: str,
    suffixes: tuple[str, ...],
) -> str | None:
    groups = versioned_groups(release_payload, base=base, suffixes=suffixes)
    return groups[0].payload if groups else None


def payload_is_complete(
    release_payload: object,
    *,
    payload_name: str,
    suffixes: tuple[str, ...],
) -> bool:
    names = {str(asset.get("name") or "") for asset in _assets(release_payload)}
    return payload_name in names and all(payload_name + suffix in names for suffix in suffixes)


def prune_asset_ids(
    release_payload: object,
    *,
    base: str,
    suffixes: tuple[str, ...],
    keep: int,
) -> list[int]:
    if keep < 1:
        raise ValueError("keep must be >= 1")

    complete = versioned_groups(release_payload, base=base, suffixes=suffixes)
    retained = {group.payload for group in complete[:keep]}
    pattern = re.compile(rf"^{re.escape(base)}\.run-(\d+)(.*)$")
    delete: list[int] = []

    for asset in _assets(release_payload):
        name = str(asset.get("name") or "")
        match = pattern.match(name)
        if not match:
            continue
        run_id, _ = match.groups()
        payload_name = f"{base}.run-{run_id}"
        if payload_name in retained:
            continue
        asset_id = asset.get("id")
        if isinstance(asset_id, int):
            delete.append(asset_id)

    return sorted(set(delete))


def _read_stdin_json() -> object:
    try:
        return json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid release JSON: {exc}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="Select complete versioned GitHub Release state assets")
    sub = parser.add_subparsers(dest="cmd", required=True)

    select = sub.add_parser("select")
    select.add_argument("--base", required=True)
    select.add_argument("--suffix", action="append", default=[])

    complete = sub.add_parser("is-complete")
    complete.add_argument("--payload", required=True)
    complete.add_argument("--suffix", action="append", default=[])

    prune = sub.add_parser("prune-ids")
    prune.add_argument("--base", required=True)
    prune.add_argument("--suffix", action="append", default=[])
    prune.add_argument("--keep", type=int, default=2)

    args = parser.parse_args()
    payload = _read_stdin_json()
    suffixes = tuple(args.suffix)

    if args.cmd == "select":
        selected = latest_complete_payload(payload, base=args.base, suffixes=suffixes)
        if selected:
            print(selected)
        return 0

    if args.cmd == "is-complete":
        return 0 if payload_is_complete(payload, payload_name=args.payload, suffixes=suffixes) else 1

    for asset_id in prune_asset_ids(payload, base=args.base, suffixes=suffixes, keep=args.keep):
        print(asset_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

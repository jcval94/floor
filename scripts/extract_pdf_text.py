#!/usr/bin/env python3
"""Extrae texto de PDFs sin dependencias externas.

Uso:
    python scripts/extract_pdf_text.py

Salida:
    docs/20_fuentes/*.txt
"""

from __future__ import annotations

import json
import re
import unicodedata
import zlib
from collections import defaultdict
from pathlib import Path

STREAM_RE = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.S)
HEX_TJ_RE = re.compile(r"<([0-9A-Fa-f]+)>\s*Tj")
ARR_TJ_RE = re.compile(r"\[(.*?)\]\s*TJ", re.S)
BT_ET_RE = re.compile(r"BT(.*?)ET", re.S)
PAIR_RE = re.compile(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>")


def _decode_unicode_hex(h: str) -> str:
    raw = bytes.fromhex(h)
    if len(raw) >= 2 and len(raw) % 2 == 0:
        try:
            return raw.decode("utf-16-be")
        except UnicodeDecodeError:
            pass
    return "".join(chr(b) for b in raw)


def _decompress_streams(pdf_bytes: bytes) -> list[bytes]:
    out: list[bytes] = []
    for m in STREAM_RE.finditer(pdf_bytes):
        data = m.group(1)
        try:
            out.append(zlib.decompress(data))
        except zlib.error:
            continue
    return out


def _build_merged_cmap(streams: list[bytes]) -> dict[str, str]:
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for s in streams:
        if b"beginbfchar" not in s and b"beginbfrange" not in s:
            continue
        txt = s.decode("latin1", "ignore")
        for src, dst in PAIR_RE.findall(txt):
            src_key = src.upper().zfill(4)
            try:
                decoded = _decode_unicode_hex(dst)
            except ValueError:
                continue
            if decoded:
                counts[src_key][decoded] += 1

    merged: dict[str, str] = {}
    for src, options in counts.items():
        merged[src] = max(options, key=options.get)
    return merged


def _decode_hex_token(token: str, cmap: dict[str, str]) -> str:
    token = token.upper()
    if len(token) % 4 == 0:
        chunks = [token[i : i + 4] for i in range(0, len(token), 4)]
    else:
        step = 2
        chunks = [token[i : i + step].zfill(4) for i in range(0, len(token), step)]
    return "".join(cmap.get(c, "") for c in chunks)


def extract_text(pdf_path: Path) -> str:
    streams = _decompress_streams(pdf_path.read_bytes())
    cmap = _build_merged_cmap(streams)

    lines: list[str] = []
    for s in streams:
        if b"BT" not in s:
            continue
        block = s.decode("latin1", "ignore")
        for bt in BT_ET_RE.findall(block):
            parts: list[str] = []
            for tok in HEX_TJ_RE.findall(bt):
                parts.append(_decode_hex_token(tok, cmap))
            for arr in ARR_TJ_RE.findall(bt):
                for tok in re.findall(r"<([0-9A-Fa-f]+)>", arr):
                    parts.append(_decode_hex_token(tok, cmap))
            line = "".join(parts).strip()
            if line:
                lines.append(line)

    return "\n".join(lines)


def slugify(name: str) -> str:
    norm = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(ch for ch in norm if not unicodedata.combining(ch))
    out = []
    for ch in ascii_only.lower():
        out.append(ch if ch.isalnum() else "-")
    slug = re.sub(r"-+", "-", "".join(out)).strip("-")
    return slug or "documento"


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    pdfs = sorted(repo.glob("*.pdf"))
    out_dir = repo / "docs" / "20_fuentes"
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = []
    for pdf in pdfs:
        text = extract_text(pdf)
        target = out_dir / f"{slugify(pdf.stem)}.txt"
        target.write_text(text, encoding="utf-8")
        info = {
            "pdf": pdf.name,
            "txt": str(target.relative_to(repo)),
            "chars": len(text),
            "lines": text.count("\n") + (1 if text else 0),
        }
        manifest.append(info)
        print(f"[ok] {pdf.name} -> {target.relative_to(repo)} ({len(text)} chars)")

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ok] manifest -> {manifest_path.relative_to(repo)}")


if __name__ == "__main__":
    main()

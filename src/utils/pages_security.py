from __future__ import annotations

import argparse
from pathlib import Path

CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "connect-src 'self'; "
    "img-src 'self' data:; "
    "style-src 'self' 'unsafe-inline'; "
    "font-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'none'"
)
CSP_META = f'<meta http-equiv="Content-Security-Policy" content="{CSP}"/>'
REFERRER_META = '<meta name="referrer" content="no-referrer"/>'


def harden_site_html(site_dir: Path) -> dict[str, int]:
    if not site_dir.exists():
        raise RuntimeError(f"site directory does not exist: {site_dir}")

    html_files = sorted(site_dir.glob("*.html"))
    if not html_files:
        raise RuntimeError(f"site directory has no top-level HTML files: {site_dir}")

    changed = 0
    for path in html_files:
        text = path.read_text(encoding="utf-8")
        if "<head" not in text or "</head>" not in text:
            raise RuntimeError(f"HTML missing head element: {path}")

        additions: list[str] = []
        if "http-equiv=\"Content-Security-Policy\"" not in text:
            additions.append(CSP_META)
        if "name=\"referrer\"" not in text:
            additions.append(REFERRER_META)
        if not additions:
            continue

        text = text.replace("</head>", "".join(additions) + "</head>", 1)
        path.write_text(text, encoding="utf-8")
        changed += 1

    return {"html_files": len(html_files), "changed": changed}


def validate_site_html_security(site_dir: Path) -> dict[str, int]:
    html_files = sorted(site_dir.glob("*.html"))
    if not html_files:
        raise RuntimeError(f"site directory has no top-level HTML files: {site_dir}")

    for path in html_files:
        text = path.read_text(encoding="utf-8")
        if CSP_META not in text:
            raise RuntimeError(f"CSP missing or altered: {path}")
        if REFERRER_META not in text:
            raise RuntimeError(f"referrer policy missing: {path}")
        lowered = text.lower()
        for event_attr in (
            " onclick=",
            " onload=",
            " onerror=",
            " onmouseover=",
            " onfocus=",
        ):
            if event_attr in lowered:
                raise RuntimeError(f"inline event handler forbidden in static HTML: {path}")
        if "javascript:" in lowered:
            raise RuntimeError(f"javascript: URL forbidden in static HTML: {path}")

    return {"html_files": len(html_files), "validated": len(html_files)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Harden GitHub Pages HTML with a strict meta CSP")
    parser.add_argument("--site-dir", default="site")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    site_dir = Path(args.site_dir)

    try:
        result = (
            validate_site_html_security(site_dir)
            if args.validate_only
            else harden_site_html(site_dir)
        )
        validate_site_html_security(site_dir)
    except RuntimeError as exc:
        print(f"FAIL: {exc}")
        return 1

    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

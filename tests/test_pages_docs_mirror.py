from pathlib import Path


def test_docs_dashboard_mirror_is_non_authoritative_and_non_executable() -> None:
    dashboard_pages = (
        "index.html",
        "about.html",
        "drift.html",
        "forecasts.html",
        "incidents.html",
        "models.html",
        "strategies.html",
        "tickers.html",
    )
    for name in dashboard_pages:
        text = (Path("docs") / name).read_text(encoding="utf-8")
        assert "mirror no autoritativo" in text
        assert "GitHub Actions / deploy-pages" in text
        assert "default-src 'none'" in text
        assert "assets/app.js" not in text

    for name in ("app.js", "charts.js", "router.js", "styles.css", "utils.js"):
        assert not (Path("docs/assets") / name).exists()

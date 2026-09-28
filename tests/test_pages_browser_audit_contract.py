from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "e2e_yahoo_pages_audit.yml"


def test_browser_audit_covers_every_public_page() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    expected = {
        "home": "index.html",
        "forecasts": "forecasts.html",
        "tickers": "tickers.html",
        "strategies": "strategies.html",
        "models": "models.html",
        "drift": "drift.html",
        "incidents": "incidents.html",
        "system": "system.html",
        "about": "about.html",
    }
    for name, page in expected.items():
        assert f"'{name}': {{" in workflow
        assert f"'path': '{page}'" in workflow

    assert "assert len(results) == 9" in workflow
    assert "'audited_view_count': len(results)" in workflow
    assert "'expected_view_count': len(expected_views)" in workflow


def test_browser_audit_requires_semantic_rendering_not_only_http_200() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    for selector in (
        "#heroStatus",
        "#forecastCards",
        "#tickersTable",
        "#leagueStatus",
        "#centralSkillTable",
        "#driftLight",
        "#status",
        "#systemOverview",
        "#main-content",
    ):
        assert f"'critical_selector': '{selector}'" in workflow

    assert "body_locator.get_attribute('data-page') == name" in workflow
    assert "h1.count() == 1 and h1.is_visible()" in workflow
    assert "critical.count() == 1 and critical.is_visible()" in workflow
    assert "critical.inner_text().strip()" in workflow
    assert "spec['expected_text'].lower() in body.lower()" in workflow


def test_browser_audit_keeps_failure_and_visual_evidence_for_all_views() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "page.on('console'" in workflow
    assert "page.on('pageerror'" in workflow
    assert "page.on('requestfailed'" in workflow
    assert "response.status >= 400" in workflow
    assert "assert not all_console_errors" in workflow
    assert "assert not all_failed_requests" in workflow
    assert "assert not all_http_errors" in workflow
    assert "page.screenshot(path=f'artifacts/e2e/pages_{name}.png', full_page=True)" in workflow
    assert "artifacts/e2e/pages_*.png" in workflow

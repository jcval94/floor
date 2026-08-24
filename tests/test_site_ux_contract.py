from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_primary_navigation_is_product_oriented_and_consistent() -> None:
    expected = ["Resumen", "Pronósticos", "Tickers", "Analítica", "Salud del sistema"]
    for name in ["index.html", "forecasts.html", "tickers.html", "strategies.html", "system.html"]:
        text = (SITE / name).read_text(encoding="utf-8")
        for label in expected:
            assert label in text
        assert 'data-nav-toggle' in text
        assert 'aria-controls="primaryNav"' in text
        assert 'class="skip-link"' in text


def test_forecasts_do_not_present_range_width_as_alpha() -> None:
    forecasts = (SITE / "forecasts.html").read_text(encoding="utf-8")
    app = (SITE / "assets" / "app.js").read_text(encoding="utf-8")
    assert "Top oportunidades" not in forecasts
    assert "Score objetivo" not in forecasts
    assert "Top oportunidades" not in app
    assert "Score objetivo" not in app
    assert "rangos esperados" in forecasts.lower()
    assert "no por supuesto potencial de compra" in forecasts.lower()


def test_frontend_never_invents_healthy_state_or_half_confidence() -> None:
    app = (SITE / "assets" / "app.js").read_text(encoding="utf-8")
    assert "floor_time_probability || 0.5" not in app
    assert "ceiling_time_probability || 0.5" not in app
    assert "drift_level: 'GREEN'" not in app
    assert "status: 'OK'" not in app
    assert "severity: 'SEV4'" not in app
    assert "drift_level: 'UNKNOWN'" in app
    assert "status: 'UNKNOWN'" in app


def test_horizon_names_are_human_readable() -> None:
    utils = (SITE / "assets" / "utils.js").read_text(encoding="utf-8")
    assert "d1: '1 sesión'" in utils
    assert "w1: '5 sesiones'" in utils
    assert "q1: '10 sesiones'" in utils
    assert "m3: '3 meses'" in utils
    methodology = (SITE / "about.html").read_text(encoding="utf-8")
    assert "10 sesiones" in methodology
    assert "quarter" in methodology.lower()


def test_system_health_consolidates_trust_surfaces() -> None:
    page = (SITE / "system.html").read_text(encoding="utf-8")
    for target in ["systemComponents", "systemAudit", "systemModels", "systemDrift", "systemIncidents"]:
        assert f'id="{target}"' in page
    assert "nunca como saludable por defecto" in page.lower()


def test_methodology_states_limits_and_non_advisory_scope() -> None:
    methodology = (SITE / "about.html").read_text(encoding="utf-8").lower()
    assert "no constituye asesoría financiera" in methodology
    assert "no implican por sí mismos una recomendación de compra o venta" in methodology
    assert "abstenerse" in methodology


def test_chart_design_defines_neutral_track_and_directional_sides() -> None:
    css = (SITE / "assets" / "styles.css").read_text(encoding="utf-8")
    charts = (SITE / "assets" / "charts.js").read_text(encoding="utf-8")
    assert "--line:" in css
    assert "range-downside" in charts
    assert "range-upside" in charts
    assert "role=\"img\"" in charts

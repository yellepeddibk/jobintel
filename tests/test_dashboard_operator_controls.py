"""The production dashboard never renders operator controls (AppTest, offline)."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from jobintel.core.config import settings

DASHBOARD = Path(__file__).resolve().parents[1] / "app" / "dashboard.py"


def _render(monkeypatch: pytest.MonkeyPatch, env: str) -> AppTest:
    # The script re-reads settings on every run.
    monkeypatch.setattr(settings, "ENV", env)
    at = AppTest.from_file(str(DASHBOARD), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def _button_labels(at: AppTest) -> list[str]:
    return [b.label for b in at.sidebar.button]


def _expander_labels(at: AppTest) -> list[str]:
    return [e.label for e in at.sidebar.expander]


def test_production_dashboard_hides_operator_controls(monkeypatch):
    at = _render(monkeypatch, "production")

    buttons = _button_labels(at)
    expanders = _expander_labels(at)
    assert not any("Run ingest" in label for label in buttons)
    assert not any("Clear cache" in label for label in buttons)
    assert not any("Ingest jobs" in label for label in expanders)
    assert not any("Debug Config" in label for label in expanders)
    assert not any("DATABASE_URL" in m.value for m in at.sidebar.markdown)


def test_production_dashboard_keeps_read_only_filters(monkeypatch):
    at = _render(monkeypatch, "production")

    text_inputs = [t.label for t in at.sidebar.text_input]
    assert "Keyword filter" in text_inputs
    assert "Location contains" in text_inputs
    # The ingest form's own inputs are gone with it.
    assert "Search" not in text_inputs
    assert not at.sidebar.number_input


@pytest.mark.parametrize("env", ["development", "test"])
def test_non_production_dashboard_shows_operator_controls(monkeypatch, env):
    at = _render(monkeypatch, env)

    buttons = _button_labels(at)
    expanders = _expander_labels(at)
    assert any("Run ingest" in label for label in buttons)
    assert any("Clear cache" in label for label in buttons)
    assert any("Ingest jobs" in label for label in expanders)
    assert any("Debug Config" in label for label in expanders)

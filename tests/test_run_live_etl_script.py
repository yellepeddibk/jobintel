"""scripts/run_live_etl.py exit status and reporting.

Runs the script in-process with runpy. fetch_from_source is mocked and the database
is the in-memory SQLite engine from conftest, so no network or external database.
"""

import runpy
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker

import jobintel.db as db
from jobintel.etl import pipeline

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_live_etl.py"


def _fetch_failing_for(*failing: str):
    def fake_fetch(source_name, search, limit):
        if source_name in failing:
            raise RuntimeError(f"{source_name} upstream unavailable")
        return [
            {
                "source": source_name,
                "url": f"https://example.invalid/{source_name}/1",
                "title": f"{source_name} engineer",
                "description": "python",
            }
        ], []

    return fake_fetch


def _run_script(monkeypatch, engine, fake_fetch) -> tuple[int, list[str]]:
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(db, "init_db", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT)])
    with patch.object(pipeline, "fetch_from_source", side_effect=fake_fetch) as mock_fetch:
        with pytest.raises(SystemExit) as exit_info:
            runpy.run_path(str(SCRIPT), run_name="__main__")
    return exit_info.value.code, [c.args[0] for c in mock_fetch.call_args_list]


def test_exits_nonzero_and_still_runs_every_source_when_one_fails(monkeypatch, engine, capsys):
    code, attempted = _run_script(monkeypatch, engine, _fetch_failing_for("arbeitnow"))

    assert code == 1
    assert attempted == ["remotive", "arbeitnow", "remoteok"]
    out, err = capsys.readouterr()
    assert "✅ remotive" in out
    assert "✅ remoteok" in out
    assert "1 of 3 sources failed: arbeitnow" in out
    # The traceback reaches the job log.
    assert "❌ arbeitnow: failed" in err
    assert "RuntimeError: arbeitnow upstream unavailable" in err


def test_exits_zero_when_every_source_succeeds(monkeypatch, engine, capsys):
    code, attempted = _run_script(monkeypatch, engine, _fetch_failing_for())

    assert code == 0
    assert attempted == ["remotive", "arbeitnow", "remoteok"]
    assert "sources failed" not in capsys.readouterr().out


def test_rejects_unsupported_category_option(monkeypatch, capsys):
    """--category was accepted and silently ignored; now it is refused up front."""
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--category", "devops"])
    with patch.object(pipeline, "fetch_from_source") as mock_fetch:
        with pytest.raises(SystemExit) as exit_info:
            runpy.run_path(str(SCRIPT), run_name="__main__")

    assert exit_info.value.code == 2  # argparse usage error
    assert "unrecognized arguments: --category devops" in capsys.readouterr().err
    mock_fetch.assert_not_called()

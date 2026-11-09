"""Run the Streamlit script headlessly: rendering, the Host check and CSV import."""

import sqlite3
from pathlib import Path

import pytest
import streamlit as st
from streamlit.runtime.context import ContextProxy
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")


@pytest.fixture()
def app(tmp_path, monkeypatch):
    """Return a factory for an AppTest whose requests carry the given Host header."""
    db = tmp_path / "resloom.db"
    monkeypatch.setenv("RESLOOM_DB", str(db))
    st.cache_resource.clear()

    def make(host="127.0.0.1:8501"):
        monkeypatch.setattr(ContextProxy, "headers", property(lambda self: {"Host": host}))
        return AppTest.from_file(APP, default_timeout=60)

    make.db = db
    yield make
    st.cache_resource.clear()


def _project_titles(db):
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT data FROM projects").fetchall()
    import json

    return [json.loads(r[0])["title"] for r in rows]


def test_app_renders_without_exceptions(app):
    at = app()
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.header[0].value.startswith("Resloom")
    assert len(at.tabs) >= 5
    text = " ".join(m.value for m in at.markdown)
    assert "Plan" not in text and "Pro feature" not in text


@pytest.mark.parametrize("host", ["localhost:8501", "[::1]:8501", "127.0.0.1"])
def test_local_hosts_are_served(app, host):
    at = app(host)
    at.run()
    assert not at.exception
    assert at.header and at.header[0].value.startswith("Resloom")


@pytest.mark.parametrize("host", ["attacker.example:8501", "attacker.example", "192.168.1.20:8501", "127.0.0.1.attacker.example"])
def test_foreign_host_gets_no_data(app, host):
    at = app(host)
    at.run()
    assert not at.header, "the app rendered for a foreign Host"
    assert any("only answers" in e.value for e in at.error)
    assert not app.db.exists(), "the database was opened for a foreign Host"


def test_uploaded_csv_is_imported_once(app):
    at = app()
    at.run()
    csv_text = b"title,principal_investigator,status\nUnique Upload Title,Dr. Test,active\n"
    at.file_uploader(key="proj_csv").set_value(("projects.csv", csv_text, "text/csv"))
    at.run(timeout=60)
    assert not at.exception, [e.value for e in at.exception]
    assert any("Imported/updated 1 project" in s.value for s in at.success)
    assert _project_titles(app.db).count("Unique Upload Title") == 1
    # The file is still in the widget on later reruns; it must not be imported again.
    at.run(timeout=60)
    at.run(timeout=60)
    assert _project_titles(app.db).count("Unique Upload Title") == 1

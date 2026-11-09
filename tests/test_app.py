"""Run the Streamlit script headlessly against a fresh database and check every tab renders."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")


def test_app_renders_without_exceptions(tmp_path, monkeypatch):
    monkeypatch.setenv("RESLOOM_DB", str(tmp_path / "resloom.db"))
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.header[0].value.startswith("Resloom")
    labels = [t.label for t in at.tabs]
    assert len(labels) >= 5, labels
    text = " ".join(m.value for m in at.markdown)
    assert "Plan" not in text and "Pro feature" not in text

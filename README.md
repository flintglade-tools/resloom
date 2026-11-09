# Resloom

Resloom is a local workspace for research groups and university departments: project and grant tracking, compliance deadlines, shared equipment and room booking, and editable progress reports. It is a Streamlit app that keeps its data in one SQLite file.

## Features

- **Projects and grants**: sponsor, grant number, budget and dates; expenditures by category with spent, remaining and percent used; status changes; notes and links; CSV import and export.
- **Compliance deadlines**: templates for IRB, IACUC, safety, biosafety and export-control items; month calendar; overdue list; bulk status changes and snoozing.
- **Resources and bookings**: equipment, rooms and facilities; weekly recurring bookings checked for conflicts as a whole series; maintenance blackouts; pending and confirmed bookings; usage over the last 30 days; alternative slots when a request conflicts.
- **Reports**: progress, annual, compliance and resource templates filled from the records; versioned drafts edited side by side with the source data; Markdown and PDF export. An optional draft narrative can come from a local [Ollama](https://ollama.com) server at `localhost:11434`; it is off unless you tick it for a report.
- **Search and export**: search across projects, deadlines, resources and bookings; full JSON snapshot; CSV exports; a ZIP pack with all CSVs, the JSON snapshot and a sample report.

A demo group with three projects, deadlines, resources and bookings is created on first run.

## Run

Python 3.11 or newer:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/streamlit run app.py
```

Open http://127.0.0.1:8501. The database is `data/resloom.db`; set `RESLOOM_DB` to use another file.

With Docker:

```bash
docker compose up -d --build
```

The container runs as an unprivileged user, keeps its database in the `resloom-data` volume, and publishes the app on `127.0.0.1:8501`.

## Test

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

## Layout

```
app.py                Streamlit interface: overview, projects, compliance, resources, reports
resloom/models.py     Pydantic models: projects, expenditures, deadlines, resources, bookings, reports, settings
resloom/storage.py    SQLite storage and the demo data
resloom/services.py   conflict detection, deadline rules, report drafts, PDF, imports and exports
tests/
```

Each record type has its own SQLite table, and each row holds the record as JSON validated by its Pydantic model. Booking conflicts are checked before any row of a recurring series is written, so a conflicting series leaves storage unchanged. Deadline alerts use the snoozed due date when one is set.

## Access

Resloom has no sign-in. It listens on `127.0.0.1` by default and answers only pages opened as `127.0.0.1`, `localhost` or `[::1]`, so other machines and pages reached through another host name get no data. Anyone who can open the page locally can read and change every record. CSV exports prefix cells that start with `=`, `+`, `-` or `@` with an apostrophe so spreadsheets do not run them as formulas.

## License

MIT. See [LICENSE](LICENSE).

"""Storage roundtrip and seed tests. Uses temp DB."""

import io
import json
import tempfile
import zipfile
from pathlib import Path
from datetime import date, datetime, timedelta

import pytest

from resloom.storage import Storage
from resloom.models import Project, ComplianceDeadline, Resource, Booking, DeadlineType, ResourceType, ProjectExpenditure, DeadlineStatus, BookingStatus
from resloom.services import (
    get_project_budget_status, snooze_deadline, get_deadline_templates, get_compliance_calendar, get_overdue_deadlines,
    create_recurring_bookings, get_resource_utilization, suggest_alternative_slots,
    create_report_draft, save_edited_report, export_report_as_pdf_bytes, llm_assist_narrative,
    search_all, export_deadlines_csv, export_resources_csv, create_institutional_pack,
    export_projects_csv, import_projects_from_csv, get_upcoming_deadlines, export_all_to_json,
)


def test_project_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "test.db"
        s = Storage(db)
        p = Project(title="Roundtrip Study", principal_investigator="Dr. Test", start_date=date.today())
        saved = s.upsert_project(p)
        loaded = s.get_project(saved.id)
        assert loaded is not None
        assert loaded.title == "Roundtrip Study"
        assert len(s.list_projects()) >= 1  # includes seed + this one, but seed may vary


def test_deadline_crud_and_filter():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "test.db"
        s = Storage(db)
        # Clear would be hard; just add and check
        dl = ComplianceDeadline(title="Test Safety Cert", type=DeadlineType.SAFETY, due_date=date.today() + timedelta(days=5))
        s.upsert_deadline(dl)
        all_d = s.list_deadlines()
        assert any(d.id == dl.id for d in all_d)


def test_booking_conflict_persists():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "test.db"
        s = Storage(db)
        r = Resource(name="Test Rig", type=ResourceType.EQUIPMENT)
        s.upsert_resource(r)
        b1 = Booking(
            resource_id=r.id,
            booked_by="Alice",
            start=datetime(2026, 9, 1, 9, 0),
            end=datetime(2026, 9, 1, 11, 0),
            purpose="Test A",
        )
        s.upsert_booking(b1)
        loaded = s.list_bookings(resource_id=r.id)
        assert len(loaded) >= 1


def test_expenditures_roundtrip_and_total():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "test.db"
        s = Storage(db)
        p = Project(title="Exp Project", principal_investigator="Dr. Money", start_date=date(2026, 1, 1), budget_usd=50000)
        s.upsert_project(p)
        e1 = ProjectExpenditure(project_id=p.id, amount_usd=1200.0, date=date(2026, 2, 1), category="supplies")
        e2 = ProjectExpenditure(project_id=p.id, amount_usd=3500.0, date=date(2026, 3, 1), category="personnel")
        s.upsert_expenditure(e1)
        s.upsert_expenditure(e2)
        exps = s.list_expenditures(project_id=p.id)
        assert len(exps) == 2
        total = sum(e.amount_usd for e in exps)
        assert total == 4700.0
        assert s.delete_expenditure(e1.id)


def test_budget_status_service():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "test.db"
        s = Storage(db)
        p = Project(title="Budget Test", principal_investigator="Dr. B", start_date=date(2026, 1, 1), budget_usd=10000.0)
        s.upsert_project(p)
        s.upsert_expenditure(ProjectExpenditure(project_id=p.id, amount_usd=2500.0, date=date(2026, 2, 1)))
        s.upsert_expenditure(ProjectExpenditure(project_id=p.id, amount_usd=1200.0, date=date(2026, 3, 1)))
        status = get_project_budget_status(s, p)
        assert status["spent"] == 3700.0
        assert status["pct_used"] > 30


def test_delete_project_removes_linked_expenditures():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "test.db"
        s = Storage(db)
        p = Project(title="Delete Budget", principal_investigator="Dr. Gone", start_date=date(2026, 1, 1))
        s.upsert_project(p)
        s.upsert_expenditure(ProjectExpenditure(project_id=p.id, amount_usd=99, date=date(2026, 1, 2)))
        dl = ComplianceDeadline(
            title="Linked after delete",
            type=DeadlineType.SAFETY,
            due_date=date(2026, 12, 1),
            project_id=p.id,
        )
        s.upsert_deadline(dl)
        r = Resource(name="Unlink Rig", type=ResourceType.EQUIPMENT)
        s.upsert_resource(r)
        b = Booking(
            resource_id=r.id,
            project_id=p.id,
            booked_by="Tech",
            start=datetime(2026, 9, 1, 9, 0),
            end=datetime(2026, 9, 1, 10, 0),
            purpose="Linked booking",
        )
        s.upsert_booking(b)

        assert s.delete_project(p.id)

        assert s.get_project(p.id) is None
        assert s.list_expenditures(p.id) == []
        # Deadlines/bookings retained but unlinked (no dangling project_id)
        reloaded_dl = s.get_deadline(dl.id)
        assert reloaded_dl is not None
        assert reloaded_dl.project_id is None
        reloaded_b = next(x for x in s.list_bookings() if x.id == b.id)
        assert reloaded_b.project_id is None


def test_deadline_snooze_and_delete():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "test.db"
        s = Storage(db)
        dl = ComplianceDeadline(title="Test Deadline", type=DeadlineType.SAFETY, due_date=date(2026, 8, 1))
        s.upsert_deadline(dl)
        # Snooze via storage + model
        dl.snoozed_until = date(2026, 8, 15)
        s.upsert_deadline(dl)
        loaded = s.get_deadline(dl.id)
        assert loaded.effective_due == date(2026, 8, 15)
        assert s.delete_deadline(dl.id) is True
        assert s.get_deadline(dl.id) is None


def test_compliance_services():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "test.db"
        s = Storage(db)
        # Templates
        templates = get_deadline_templates()
        assert len(templates) >= 3
        assert any("IRB" in t["title"] for t in templates)

        dl = ComplianceDeadline(title="Snooze Me", type=DeadlineType.IRB, due_date=date(2026, 9, 1))
        s.upsert_deadline(dl)
        snoozed = snooze_deadline(s, dl.id, 14, "PI on leave")
        assert snoozed is not None
        assert snoozed.effective_due > dl.due_date

        # Calendar
        cal = get_compliance_calendar(s, 2026, 9)
        assert "deadlines_by_date" in cal

        # Overdue
        past = ComplianceDeadline(title="Past Due", type=DeadlineType.SAFETY, due_date=date(2025, 1, 1), status=DeadlineStatus.PENDING)
        s.upsert_deadline(past)
        overs = get_overdue_deadlines(s)
        assert any(o.title == "Past Due" for o in overs)


def test_upcoming_deadlines_respect_effective_due_date():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "test.db"
        s = Storage(db)
        dl = ComplianceDeadline(
            title="Snoozed Out",
            type=DeadlineType.IRB,
            due_date=date.today() + timedelta(days=5),
            snoozed_until=date.today() + timedelta(days=90),
        )
        s.upsert_deadline(dl)
        upcoming = get_upcoming_deadlines(s, days=30)
        assert all(d.id != dl.id for d in upcoming)


def test_booking_recurring_utilization_suggest():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "test.db"
        s = Storage(db)
        r = Resource(name="Test Rig", type=ResourceType.EQUIPMENT)
        s.upsert_resource(r)
        now = datetime.now().replace(microsecond=0)
        base = Booking(
            resource_id=r.id,
            booked_by="Tech",
            start=now - timedelta(days=14),
            end=now - timedelta(days=14, hours=-1),
            purpose="Recurring test",
            status=BookingStatus.CONFIRMED,
        )
        series = create_recurring_bookings(s, base, "weekly", 3)
        assert len(series) == 3

        future = Booking(
            resource_id=r.id,
            booked_by="Tech",
            start=now + timedelta(days=7),
            end=now + timedelta(days=7, hours=1),
            purpose="Future booking should not count as past utilization",
            status=BookingStatus.CONFIRMED,
        )
        s.upsert_booking(future)
        util = get_resource_utilization(s, r.id, 30)
        assert util["booking_count"] == 3
        assert util["total_hours"] > 0

        desired = now + timedelta(hours=3)
        alts = suggest_alternative_slots(s, r.id, desired, 60, 2)
        assert all(a >= desired for a in alts)


def test_recurring_bookings_reject_conflicting_later_occurrence_atomically():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "test.db"
        s = Storage(db)
        r = Resource(name="Conflict Rig", type=ResourceType.EQUIPMENT)
        s.upsert_resource(r)
        existing = Booking(
            resource_id=r.id,
            booked_by="Alice",
            start=datetime(2026, 1, 8, 9, 0),
            end=datetime(2026, 1, 8, 10, 0),
            purpose="Existing booking",
            status=BookingStatus.CONFIRMED,
        )
        s.upsert_booking(existing)
        base = Booking(
            resource_id=r.id,
            booked_by="Bert",
            start=datetime(2026, 1, 1, 9, 0),
            end=datetime(2026, 1, 1, 10, 0),
            purpose="Would conflict on second occurrence",
            status=BookingStatus.CONFIRMED,
        )

        with pytest.raises(ValueError, match="Occurrence 2 conflicts"):
            create_recurring_bookings(s, base, "weekly", 2)

        bookings = s.list_bookings(resource_id=r.id)
        assert [b.id for b in bookings] == [existing.id]


def test_report_templates_versioning_pdf():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "test.db"
        s = Storage(db)
        p = Project(title="Report Test Proj", principal_investigator="Dr. R", start_date=date(2026, 1, 1), budget_usd=50000)
        s.upsert_project(p)

        # Create with template
        d1 = create_report_draft(s, p.id, "Test Progress", report_type="progress")
        assert d1.report_type == "progress"
        assert d1.version == 1

        # Edit via save (new version)
        updated = save_edited_report(s, d1, d1.content_markdown + "\n\n## Added section", "Tester")
        assert updated.version == 2
        assert len(updated.history) == 1

        # PDF export
        pdf = export_report_as_pdf_bytes(updated, p.title)
        assert isinstance(pdf, (bytes, bytearray)) and len(pdf) > 100

        # Unicode-heavy titles/content must not crash Helvetica PDF path
        unicode_draft = create_report_draft(s, p.id, "Progress — café £ €", report_type="progress")
        unicode_draft.content_markdown = "# Smart quotes “hello” and em—dash and café\n"
        pdf_u = export_report_as_pdf_bytes(unicode_draft, "Café Lab")
        assert isinstance(pdf_u, (bytes, bytearray)) and len(pdf_u) > 100

        # LLM stub (no ollama expected)
        narr = llm_assist_narrative("test context")
        assert narr is None


def test_search_exports_pack():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "test.db"
        s = Storage(db)
        p = Project(title="Searchable Project Alpha", principal_investigator="Dr. Search", start_date=date(2026, 1, 1))
        s.upsert_project(p)
        d = ComplianceDeadline(title="Search Deadline", type=DeadlineType.SAFETY, due_date=date(2026, 12, 1))
        s.upsert_deadline(d)
        r = Resource(name="Search Rig", type=ResourceType.EQUIPMENT)
        s.upsert_resource(r)

        res = search_all(s, "search")
        assert len(res["projects"]) >= 1
        assert len(res["deadlines"]) >= 1 or len(res["resources"]) >= 1

        csv_d = export_deadlines_csv(s.list_deadlines())
        assert "Search Deadline" in csv_d
        csv_r = export_resources_csv(s.list_resources())
        assert "Search Rig" in csv_r

        s.upsert_expenditure(ProjectExpenditure(project_id=p.id, amount_usd=42, date=date(2026, 2, 1), category="supplies"))
        snapshot = json.loads(export_all_to_json(s))
        assert snapshot["settings"]["org_name"]
        assert any(e["project_id"] == p.id for e in snapshot["expenditures"])

        pack = create_institutional_pack(s)
        assert isinstance(pack, (bytes, bytearray)) and len(pack) > 100
        with zipfile.ZipFile(io.BytesIO(pack)) as zf:
            assert "expenditures.csv" in zf.namelist()
            assert "42" in zf.read("expenditures.csv").decode("utf-8")


def test_project_csv_export_import_roundtrip_updates_existing_project():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "test.db"
        s = Storage(db)
        p = Project(
            title="CSV Roundtrip",
            principal_investigator="Dr. CSV",
            start_date=date(2026, 1, 1),
            sponsor="NIH",
            grant_number="R01-CSV",
            budget_usd=12345,
            notes="keep these notes",
            links=["https://example.edu/a", "file:///tmp/b"],
        )
        s.upsert_project(p)
        csv_text = export_projects_csv([p], {p.id: []}).replace("CSV Roundtrip", "CSV Roundtrip Updated")

        imported = import_projects_from_csv(s, csv_text)

        assert len(imported) == 1
        assert imported[0].id == p.id
        loaded = s.get_project(p.id)
        assert loaded.title == "CSV Roundtrip Updated"
        assert loaded.notes == "keep these notes"
        assert loaded.links == ["https://example.edu/a", "file:///tmp/b"]


def test_csv_exports_neutralise_formulas_and_import_restores_them():
    import csv
    import io

    with tempfile.TemporaryDirectory() as td:
        s = Storage(Path(td) / "csv.db")
        p = Project(title="=HYPERLINK(\"http://x\")", principal_investigator="@admin", start_date=date(2026, 1, 1))
        # Text that already starts with an apostrophe, alone or before a formula character.
        q = Project(title="'=SUM(A1:A2)", principal_investigator="'quoted", start_date=date(2026, 1, 1))
        s.upsert_project(p)
        s.upsert_project(q)
        out = export_projects_csv([p, q], {p.id: [], q.id: []})
        rows = {r["id"]: r for r in csv.DictReader(io.StringIO(out))}
        assert rows[p.id]["title"].startswith("'=")
        assert rows[p.id]["principal_investigator"] == "'@admin"
        assert rows[q.id]["title"] == "''=SUM(A1:A2)"
        assert rows[q.id]["principal_investigator"] == "''quoted"
        assert "'=" not in export_deadlines_csv([]) and "'=" not in export_resources_csv([])
        imported = {x.id: x for x in import_projects_from_csv(s, out)}
        assert imported[p.id].title == p.title
        assert imported[p.id].principal_investigator == "@admin"
        assert imported[q.id].title == "'=SUM(A1:A2)"
        assert imported[q.id].principal_investigator == "'quoted"

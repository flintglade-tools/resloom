"""Local-first SQLite storage for Resloom using stdlib + Pydantic.

- Single file DB (data/resloom.db by default)
- All rows round-trip through Pydantic models
- Seed demo data on first run if empty
- Simple, auditable, easy to export/backup
"""

import json
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Type, TypeVar

from .models import (
    Project, ComplianceDeadline, Resource, Booking, ReportDraft, AppSettings,
    ProjectStatus, DeadlineStatus, BookingStatus, ProjectExpenditure,
)

T = TypeVar("T")

DB_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deadlines (
    id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    due_date TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resources (
    id TEXT PRIMARY KEY,
    data TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bookings (
    id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    resource_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY,
    data TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    data TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS expenditures (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    data TEXT NOT NULL,
    date TEXT NOT NULL
);
"""


class Storage:
    """Thread-safe enough for single-process Streamlit use."""

    def __init__(self, db_path: str | Path = "data/resloom.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()
        self._ensure_seed()

    def _init_db(self):
        self._conn.executescript(DB_SCHEMA)
        self._conn.commit()

    def _ensure_seed(self):
        # Seed only if no projects
        cur = self._conn.execute("SELECT COUNT(*) as c FROM projects")
        if cur.fetchone()["c"] == 0:
            self._seed_demo()
        else:
            # Add demo expenditures to seeded projects that predate expenditure tracking (one-time)
            cur = self._conn.execute("SELECT COUNT(*) as c FROM expenditures")
            if cur.fetchone()["c"] == 0:
                self._seed_sample_expenditures()

    def _seed_demo(self):
        today = date.today()
        # Projects
        p1 = Project(
            title="Neuroplasticity in Aging Cohorts",
            principal_investigator="Dr. Elena Vargas",
            status=ProjectStatus.ACTIVE,
            start_date=date(2025, 3, 1),
            end_date=date(2027, 2, 28),
            sponsor="NIH NIA",
            grant_number="DEMO-0001",
            budget_usd=1250000.0,
            description="Longitudinal study of cognitive reserve and plasticity biomarkers.",
        )
        p2 = Project(
            title="CRISPR Safety Screening Platform",
            principal_investigator="Prof. Marcus Hale",
            status=ProjectStatus.ACTIVE,
            start_date=date(2026, 1, 15),
            end_date=date(2026, 12, 31),
            sponsor="NSF",
            grant_number="DEMO-0002",
            budget_usd=485000.0,
        )
        p3 = Project(
            title="Community Air Quality Sensor Network",
            principal_investigator="Dr. Priya Singh",
            status=ProjectStatus.PLANNED,
            start_date=date(2026, 9, 1),
            end_date=date(2028, 8, 31),
            sponsor="EPA",
        )
        for p in (p1, p2, p3):
            self.upsert_project(p)

        # Sample expenditures for budget tracking
        exps_p1 = [
            ProjectExpenditure(project_id=p1.id, amount_usd=245000.0, date=date(2025, 4, 15), category="personnel", description="Q1 salaries + benefits"),
            ProjectExpenditure(project_id=p1.id, amount_usd=87200.0, date=date(2025, 6, 1), category="scans", description="MRI cohort baseline"),
            ProjectExpenditure(project_id=p1.id, amount_usd=18300.0, date=date(2026, 3, 20), category="supplies", description="Consumables + assays"),
        ]
        for e in exps_p1:
            self.upsert_expenditure(e)

        exps_p2 = [
            ProjectExpenditure(project_id=p2.id, amount_usd=125000.0, date=date(2026, 2, 10), category="personnel", description="Postdoc + tech"),
            ProjectExpenditure(project_id=p2.id, amount_usd=42000.0, date=date(2026, 4, 5), category="reagents", description="CRISPR reagents batch 1"),
        ]
        for e in exps_p2:
            self.upsert_expenditure(e)

        # Deadlines
        d1 = ComplianceDeadline(
            project_id=p1.id,
            title="IRB Annual Continuation",
            type="irb",
            due_date=today + timedelta(days=12),
            status=DeadlineStatus.PENDING,
            reminder_days_before=21,
            notes="Submit progress report + adverse events summary.",
        )
        d2 = ComplianceDeadline(
            project_id=p2.id,
            title="Biosafety Cabinet Recertification",
            type="safety",
            due_date=today + timedelta(days=25),
            status=DeadlineStatus.PENDING,
        )
        d3 = ComplianceDeadline(
            project_id=p1.id,
            title="IACUC Protocol Renewal",
            type="iacuc",
            due_date=today + timedelta(days=38),
            status=DeadlineStatus.IN_REVIEW,
        )
        d4 = ComplianceDeadline(
            title="Lab Safety Training Refresh (All Staff)",
            type="safety",
            due_date=today - timedelta(days=3),
            status=DeadlineStatus.PENDING,
        )
        for d in (d1, d2, d3, d4):
            self.upsert_deadline(d)

        # Resources
        r1 = Resource(name="MRI Scanner (3T)", type="equipment", location="Core Imaging Facility, Rm 102", capacity=1, notes="Requires certified operator")
        r2 = Resource(name="BSL-2 Tissue Culture Suite", type="facility", location="Life Sciences Bldg, 3rd floor", capacity=4)
        r3 = Resource(name="Conference Room A (12 pax)", type="room", location="Admin Wing", capacity=12)
        r4 = Resource(name="High-Performance Compute Node (GPU)", type="instrument", location="HPC Cluster", capacity=2)
        for r in (r1, r2, r3, r4):
            self.upsert_resource(r)

        # Sample bookings (one conflict demo)
        b1 = Booking(
            resource_id=r1.id,
            project_id=p1.id,
            booked_by="Elena Vargas",
            start=datetime.combine(today + timedelta(days=1), time(9, 0)),
            end=datetime.combine(today + timedelta(days=1), time(11, 30)),
            purpose="Baseline scan cohort wave 3",
        )
        b2 = Booking(
            resource_id=r3.id,
            project_id=p2.id,
            booked_by="Marcus Hale",
            start=datetime.combine(today + timedelta(days=2), time(14, 0)),
            end=datetime.combine(today + timedelta(days=2), time(16, 0)),
            purpose="CRISPR safety protocol review meeting",
        )
        self.upsert_booking(b1)
        self.upsert_booking(b2)

        # Sample blackout / maintenance
        blackout = Booking(
            resource_id=r1.id,
            booked_by="Facilities",
            start=datetime.combine(today + timedelta(days=7), time(8, 0)),
            end=datetime.combine(today + timedelta(days=7), time(17, 0)),
            purpose="Quarterly maintenance & calibration",
            is_blackout=True,
            status=BookingStatus.CONFIRMED,
        )
        self.upsert_booking(blackout)

        # One report draft stub
        rep = ReportDraft(
            project_id=p1.id,
            title="Q2 2026 Progress Summary - Neuroplasticity",
            content_markdown="# Neuroplasticity in Aging Cohorts\n\n**PI:** Dr. Elena Vargas\n**Period:** Q2 2026\n\n## Key Activities\n- Recruited 42 new participants\n- Completed 38 baseline MRI scans\n- IRB annual continuation submitted\n\n## Budget Status\nSpent: $312k of $1.25M (24.9%)\n\n## Risks / Next Steps\n- Staff turnover in imaging core\n- Next milestone: mid-study analysis (Aug 2026)\n\n*This draft was auto-generated from current records. Please edit before finalizing.*",
        )
        self.upsert_report(rep)

        # Settings
        self.save_settings(AppSettings(org_name="University Research Lab - Cognitive Neuroscience"))

    def _seed_sample_expenditures(self):
        """One-time helper: add sample expenditures to demo projects created before expenditure tracking."""
        projs = self.list_projects()
        if not projs:
            return
        p1 = projs[0]
        p2 = projs[1] if len(projs) > 1 else None
        for e in [
            ProjectExpenditure(project_id=p1.id, amount_usd=245000.0, date=date(2025, 4, 15), category="personnel", description="Q1 salaries"),
            ProjectExpenditure(project_id=p1.id, amount_usd=87200.0, date=date(2025, 6, 1), category="scans", description="MRI baseline"),
            ProjectExpenditure(project_id=p1.id, amount_usd=18300.0, date=date(2026, 3, 20), category="supplies", description="Assays"),
        ]:
            self.upsert_expenditure(e)
        if p2:
            for e in [
                ProjectExpenditure(project_id=p2.id, amount_usd=125000.0, date=date(2026, 2, 10), category="personnel", description="Postdoc"),
                ProjectExpenditure(project_id=p2.id, amount_usd=42000.0, date=date(2026, 4, 5), category="reagents", description="CRISPR batch"),
            ]:
                self.upsert_expenditure(e)

    # --- Projects ---
    def upsert_project(self, project: Project) -> Project:
        project.updated_at = datetime.now(timezone.utc)
        data = project.model_dump_json()
        self._conn.execute(
            "INSERT OR REPLACE INTO projects (id, data, updated_at) VALUES (?, ?, ?)",
            (project.id, data, project.updated_at.isoformat()),
        )
        self._conn.commit()
        return project

    def get_project(self, pid: str) -> Optional[Project]:
        row = self._conn.execute("SELECT data FROM projects WHERE id=?", (pid,)).fetchone()
        return Project.model_validate_json(row["data"]) if row else None

    def list_projects(self, status: Optional[str] = None) -> List[Project]:
        q = "SELECT data FROM projects ORDER BY updated_at DESC"
        rows = self._conn.execute(q).fetchall()
        projs = [Project.model_validate_json(r["data"]) for r in rows]
        if status:
            projs = [p for p in projs if p.status.value == status]
        return projs

    def delete_project(self, pid: str) -> bool:
        """Delete project, cascade expenditures, and unlink deadlines/bookings."""
        self._conn.execute("DELETE FROM expenditures WHERE project_id=?", (pid,))
        # Unlink compliance items (keep history, drop dangling project refs)
        for d in self.list_deadlines():
            if d.project_id == pid:
                d.project_id = None
                data = d.model_dump_json()
                self._conn.execute(
                    "INSERT OR REPLACE INTO deadlines (id, data, due_date) VALUES (?, ?, ?)",
                    (d.id, data, d.due_date.isoformat()),
                )
        for b in self.list_bookings():
            if b.project_id == pid:
                b.project_id = None
                data = b.model_dump_json()
                self._conn.execute(
                    "INSERT OR REPLACE INTO bookings (id, data, start, end, resource_id) VALUES (?, ?, ?, ?, ?)",
                    (b.id, data, b.start.isoformat(), b.end.isoformat(), b.resource_id),
                )
        # Unlink reports (keep draft content)
        for rep in self.list_reports():
            if rep.project_id == pid:
                rep.project_id = None
                data = rep.model_dump_json()
                self._conn.execute(
                    "INSERT OR REPLACE INTO reports (id, data) VALUES (?, ?)",
                    (rep.id, data),
                )
        cur = self._conn.execute("DELETE FROM projects WHERE id=?", (pid,))
        self._conn.commit()
        return cur.rowcount > 0

    # --- Deadlines ---
    def upsert_deadline(self, dl: ComplianceDeadline) -> ComplianceDeadline:
        dl.last_updated = datetime.now(timezone.utc)
        data = dl.model_dump_json()
        self._conn.execute(
            "INSERT OR REPLACE INTO deadlines (id, data, due_date) VALUES (?, ?, ?)",
            (dl.id, data, dl.due_date.isoformat()),
        )
        self._conn.commit()
        return dl

    def list_deadlines(self, include_overdue: bool = True) -> List[ComplianceDeadline]:
        rows = self._conn.execute("SELECT data FROM deadlines ORDER BY due_date ASC").fetchall()
        dls = [ComplianceDeadline.model_validate_json(r["data"]) for r in rows]
        if not include_overdue:
            dls = [d for d in dls if not d.is_overdue()]
        return dls

    def get_deadline(self, did: str) -> Optional[ComplianceDeadline]:
        row = self._conn.execute("SELECT data FROM deadlines WHERE id=?", (did,)).fetchone()
        return ComplianceDeadline.model_validate_json(row["data"]) if row else None

    def delete_deadline(self, did: str) -> bool:
        cur = self._conn.execute("DELETE FROM deadlines WHERE id=?", (did,))
        self._conn.commit()
        return cur.rowcount > 0

    def bulk_update_deadline_status(self, ids: list[str], new_status: DeadlineStatus) -> int:
        updated = 0
        for did in ids:
            dl = self.get_deadline(did)
            if dl:
                dl.status = new_status
                self.upsert_deadline(dl)
                updated += 1
        return updated

    # --- Resources ---
    def upsert_resource(self, res: Resource) -> Resource:
        data = res.model_dump_json()
        self._conn.execute("INSERT OR REPLACE INTO resources (id, data) VALUES (?, ?)", (res.id, data))
        self._conn.commit()
        return res

    def list_resources(self, active_only: bool = True) -> List[Resource]:
        rows = self._conn.execute("SELECT data FROM resources").fetchall()
        rs = [Resource.model_validate_json(r["data"]) for r in rows]
        if active_only:
            rs = [r for r in rs if r.is_active]
        return rs

    # --- Bookings ---
    def upsert_booking(self, b: Booking) -> Booking:
        data = b.model_dump_json()
        self._conn.execute(
            "INSERT OR REPLACE INTO bookings (id, data, start, end, resource_id) VALUES (?, ?, ?, ?, ?)",
            (b.id, data, b.start.isoformat(), b.end.isoformat(), b.resource_id),
        )
        self._conn.commit()
        return b

    def list_bookings(self, resource_id: Optional[str] = None) -> List[Booking]:
        if resource_id:
            rows = self._conn.execute("SELECT data FROM bookings WHERE resource_id=? ORDER BY start", (resource_id,)).fetchall()
        else:
            rows = self._conn.execute("SELECT data FROM bookings ORDER BY start").fetchall()
        return [Booking.model_validate_json(r["data"]) for r in rows]

    def get_bookings_for_resource(self, rid: str) -> List[Booking]:
        return self.list_bookings(resource_id=rid)

    def delete_booking(self, bid: str) -> bool:
        cur = self._conn.execute("DELETE FROM bookings WHERE id=?", (bid,))
        self._conn.commit()
        return cur.rowcount > 0

    # --- Reports ---
    def upsert_report(self, rep: ReportDraft) -> ReportDraft:
        data = rep.model_dump_json()
        self._conn.execute("INSERT OR REPLACE INTO reports (id, data) VALUES (?, ?)", (rep.id, data))
        self._conn.commit()
        return rep

    def list_reports(self) -> List[ReportDraft]:
        rows = self._conn.execute("SELECT data FROM reports").fetchall()
        reps = [ReportDraft.model_validate_json(r["data"]) for r in rows]
        reps.sort(key=lambda r: r.generated_at, reverse=True)
        return reps

    def get_report(self, rid: str) -> Optional[ReportDraft]:
        row = self._conn.execute("SELECT data FROM reports WHERE id=?", (rid,)).fetchone()
        return ReportDraft.model_validate_json(row["data"]) if row else None

    # --- Expenditures (budget tracking) ---
    def upsert_expenditure(self, exp: ProjectExpenditure) -> ProjectExpenditure:
        data = exp.model_dump_json()
        self._conn.execute(
            "INSERT OR REPLACE INTO expenditures (id, project_id, data, date) VALUES (?, ?, ?, ?)",
            (exp.id, exp.project_id, data, exp.date.isoformat()),
        )
        self._conn.commit()
        return exp

    def list_expenditures(self, project_id: Optional[str] = None) -> List[ProjectExpenditure]:
        if project_id:
            rows = self._conn.execute(
                "SELECT data FROM expenditures WHERE project_id=? ORDER BY date DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT data FROM expenditures ORDER BY date DESC").fetchall()
        return [ProjectExpenditure.model_validate_json(r["data"]) for r in rows]

    def delete_expenditure(self, eid: str) -> bool:
        cur = self._conn.execute("DELETE FROM expenditures WHERE id=?", (eid,))
        self._conn.commit()
        return cur.rowcount > 0

    # --- Settings ---
    def save_settings(self, settings: AppSettings) -> AppSettings:
        data = settings.model_dump_json()
        self._conn.execute("INSERT OR REPLACE INTO settings (key, data) VALUES ('main', ?)", (data,))
        self._conn.commit()
        return settings

    def get_settings(self) -> AppSettings:
        row = self._conn.execute("SELECT data FROM settings WHERE key='main'").fetchone()
        if row:
            return AppSettings.model_validate_json(row["data"])
        s = AppSettings()
        return self.save_settings(s)

    def close(self):
        self._conn.close()


# Convenience factory for app use
def get_storage(db_path: Optional[str] = None) -> Storage:
    if db_path:
        return Storage(db_path)
    # Default: relative to cwd (works in Docker + local)
    return Storage()

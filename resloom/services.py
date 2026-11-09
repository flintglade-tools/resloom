"""Lightweight service layer for Resloom.

Keeps business rules (conflict detection, upcoming filtering, report scaffolding)
separate from storage and UI. All functions are pure or take Storage.
"""

from datetime import date, datetime, timedelta, timezone
from datetime import timedelta as dt_timedelta
import json
import uuid
from typing import List, Optional

from .models import (
    Booking, ComplianceDeadline, Project, Resource, ReportDraft, DeadlineStatus,
    ProjectExpenditure, ProjectStatus, DeadlineType, BookingStatus, AppSettings,
)
from .storage import Storage


def _naive(dt: datetime) -> datetime:
    """Normalize datetimes for comparisons across Streamlit/user-created values."""
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


def _bookings_overlap(a: Booking, b: Booking) -> bool:
    return not (_naive(a.end) <= _naive(b.start) or _naive(a.start) >= _naive(b.end))


def get_upcoming_deadlines(storage: Storage, days: int = 30) -> List[ComplianceDeadline]:
    """Return deadlines due within N days or overdue, sorted by effective due date."""
    all_dls = storage.list_deadlines(include_overdue=True)
    today = date.today()
    cutoff = today + timedelta(days=days)
    upcoming = []
    for d in all_dls:
        if d.status in (DeadlineStatus.APPROVED, DeadlineStatus.SUBMITTED):
            continue
        if d.effective_due <= cutoff:
            upcoming.append(d)
    upcoming.sort(key=lambda x: x.effective_due)
    return upcoming


def detect_booking_conflicts(storage: Storage, new_booking: Booking, exclude_id: Optional[str] = None) -> List[Booking]:
    """Return existing overlapping confirmed bookings for the same resource.
    Blackouts block everything. Normal bookings block each other.
    """
    existing = storage.get_bookings_for_resource(new_booking.resource_id)
    conflicts = []
    for b in existing:
        if exclude_id and b.id == exclude_id:
            continue
        if b.status == BookingStatus.CANCELLED:
            continue
        if _bookings_overlap(new_booking, b):
            conflicts.append(b)
    return conflicts


def get_project_summary(storage: Storage, project_id: str) -> dict:
    p = storage.get_project(project_id)
    if not p:
        return {}
    deadlines = [d for d in storage.list_deadlines() if d.project_id == project_id]
    bookings = [b for b in storage.list_bookings() if b.project_id == project_id]
    return {
        "project": p,
        "active_deadlines": [d for d in deadlines if d.status not in (DeadlineStatus.APPROVED, DeadlineStatus.SUBMITTED)],
        "upcoming_bookings": sorted(bookings, key=lambda x: x.start)[:5],
    }


def create_report_draft(
    storage: Storage,
    project_id: Optional[str],
    title: str,
    author: str = "User",
    report_type: str = "progress",
    use_llm: bool = False,
) -> ReportDraft:
    """Create human-first editable draft. Supports multiple templates.

    LLM assist is optional, always produces editable output, never auto-saved.
    Falls back gracefully if Ollama unavailable.
    """
    p = storage.get_project(project_id) if project_id else None
    summary = get_project_summary(storage, project_id) if project_id else {}
    deadlines = [d for d in storage.list_deadlines() if d.project_id == project_id] if project_id else []
    bookings = [b for b in storage.list_bookings() if b.project_id == project_id] if project_id else []
    exps = storage.list_expenditures(project_id) if project_id else []

    lines = []
    lines.append(f"# {title}\n")
    lines.append(f"**Report Type:** {report_type}\n")
    if p:
        lines.append(f"**Project:** {p.title}")
        lines.append(f"**PI:** {p.principal_investigator}")
        lines.append(f"**Status:** {p.status.value}")
        if p.grant_number:
            lines.append(f"**Grant:** {p.grant_number} ({p.sponsor or 'N/A'})")
        if p.budget_usd:
            lines.append(f"**Budget:** ${p.budget_usd:,.0f}")
        lines.append(f"**Period:** {p.start_date} → {p.end_date or 'open'}")
        lines.append("")

    if report_type == "progress":
        if deadlines:
            lines.append("## Compliance Deadlines\n")
            for d in sorted(deadlines, key=lambda x: x.due_date)[:5]:
                lines.append(f"- **{d.due_date}** — {d.title} ({d.type.value}, {d.status.value})")
            lines.append("")
        if bookings:
            lines.append("## Resource Usage\n")
            for b in sorted(bookings, key=lambda x: x.start)[:5]:
                lines.append(f"- {b.start.date()} {b.start.strftime('%H:%M')}–{b.end.strftime('%H:%M')}: {b.purpose}")
            lines.append("")
        if exps:
            total = sum(e.amount_usd for e in exps)
            lines.append(f"**Expenditures to date:** ${total:,.0f}\n")
        lines.append("## Progress Narrative\n")
        narrative = "*[Summarize key results, challenges, milestones.]*"
        if use_llm and p:
            ctx = f"Project {p.title} status {p.status.value} budget {p.budget_usd} deadlines {len(deadlines)} bookings {len(bookings)}"
            llm_text = llm_assist_narrative(ctx)
            narrative = llm_text
        lines.append(narrative)
        lines.append("\n## Next Steps\n*[List action items.]*\n")

    elif report_type == "annual":
        lines.append("## Executive Summary\n*[High-level overview.]*\n")
        if p and p.budget_usd:
            lines.append(f"**Total Budget:** ${p.budget_usd:,.0f}\n")
        lines.append("## Achievements\n*[Major outcomes this period.]*\n")
        lines.append("## Challenges & Mitigations\n")
        lines.append("## Compliance Status\n")
        for d in deadlines[:3]:
            lines.append(f"- {d.title}: {d.status.value}")
        lines.append("")

    elif report_type == "compliance":
        lines.append("## Compliance Overview\n")
        for d in sorted(deadlines, key=lambda x: x.due_date):
            overdue = "OVERDUE" if d.is_overdue() else ""
            lines.append(f"- {d.due_date} {d.title} [{d.type.value}] {d.status.value} {overdue}")
        lines.append("\n## Recommendations\n*[Human: add required actions.]*\n")

    elif report_type == "resource":
        lines.append("## Resource Utilization\n")
        if bookings:
            lines.append(f"Total bookings tracked: {len(bookings)}\n")
            for b in bookings[:5]:
                lines.append(f"- {b.purpose} on {b.start.date()}")
        lines.append("\n## Recommendations\n*[Edit to add insights.]*\n")

    else:  # custom or fallback
        lines.append("## Summary\n*[Populate from data or edit.]*\n")

    from . import __version__ as _ver
    lines.append(
        "\n---\n_Generated on "
        + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        + f" by Resloom v{_ver}. Always review and edit before use._"
    )

    draft = ReportDraft(
        project_id=project_id,
        title=title,
        report_type=report_type,
        content_markdown="\n".join(lines),
        version=1,
        human_edited=False,
        last_edited_by=author,
    )
    return storage.upsert_report(draft)


def llm_assist_narrative(context: str) -> str:
    """Optional local LLM assist via Ollama (http://localhost:11434).

    Returns narrative text that is ALWAYS editable by human.
    Graceful fallback if no Ollama running. Never auto-commits.
    """
    try:
        import urllib.request
        import json as jsonlib
        payload = {
            "model": "llama3.2",
            "prompt": (
                "Based ONLY on the following structured research data, write 3-5 concise factual sentences "
                "for a progress narrative section. Include 1-2 specific insights or risks if evident. "
                "Do not invent numbers or add boilerplate disclaimers. Keep professional tone.\n\n"
                f"DATA: {context[:1500]}"
            ),
            "stream": False,
            "options": {"temperature": 0.2, "num_predict": 250}
        }
        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=jsonlib.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = jsonlib.loads(r.read().decode("utf-8"))
            text = resp.get("response", "").strip()
            if text:
                return text + "\n\n_(LLM-assisted draft — human review and edit required)_"
    except Exception:
        pass
    return (
        "*[LLM assist not available (Ollama not running or unreachable). "
        "Populate this section manually from source data.]*\n\n"
        "Key observations from records:\n- ...\n- ..."
    )


def save_edited_report(storage: Storage, report: ReportDraft, new_markdown: str, editor: str = "User") -> ReportDraft:
    """Save edit with versioning. Appends previous to history. Human action only."""
    if new_markdown != report.content_markdown:
        report.history.append({
            "version": report.version,
            "content": report.content_markdown[:2000],  # truncate for size
            "edited_at": datetime.now(timezone.utc).isoformat(),
        })
        report.version += 1
        report.content_markdown = new_markdown
        report.human_edited = True
        report.last_edited_by = editor
    return storage.upsert_report(report)


def _pdf_safe_text(text: str) -> str:
    """Helvetica is Latin-1 only; normalize common Unicode so PDF export never crashes."""
    if not text:
        return ""
    replacements = {
        "\u2014": "-",  # em dash
        "\u2013": "-",  # en dash
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2026": "...",
        "\u00a0": " ",
        "\u2022": "*",
        "\u00b7": "*",
        "£": "GBP",
        "€": "EUR",
        "¥": "JPY",
    }
    out = text
    for src, dst in replacements.items():
        out = out.replace(src, dst)
    # Drop anything still outside Latin-1 so FPDF never raises
    return out.encode("latin-1", errors="replace").decode("latin-1")


def export_report_as_pdf_bytes(draft: ReportDraft, project_name: str = "Research Group") -> bytes:
    """Generate simple PDF for download. Requires fpdf2. Unicode-safe (Latin-1 fallback)."""
    from fpdf import FPDF, XPos, YPos
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, _pdf_safe_text(draft.title[:60]), new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
    pdf.set_font("Helvetica", size=8)
    meta = (
        f"Resloom Report | Type: {draft.report_type} | v{draft.version} | "
        f"{draft.generated_at.strftime('%Y-%m-%d %H:%M UTC')}"
    )
    pdf.cell(0, 6, _pdf_safe_text(meta), new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
    pdf.cell(0, 6, _pdf_safe_text(f"For: {project_name}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
    pdf.ln(4)
    pdf.set_font("Helvetica", size=9)
    for raw_line in draft.content_markdown.splitlines():
        line = raw_line.strip()
        if not line:
            pdf.ln(1)
            continue
        line = line.replace("**", "").replace("*", "").replace("#", " ").strip()[:85]
        if line.startswith("- "):
            line = "- " + line[2:]
        try:
            pdf.cell(0, 5, _pdf_safe_text(line), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        except Exception:
            pass
    pdf.ln(3)
    pdf.set_font("Helvetica", "I", 7)
    pdf.cell(
        0,
        5,
        "Human-edited Resloom report. Review before distribution.",
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
        align="C",
    )
    return bytes(pdf.output())


def export_all_to_json(storage: Storage) -> str:
    """Full portable snapshot (for institutional handoff or backup)."""
    return json.dumps({
        "settings": storage.get_settings().model_dump(),
        "projects": [p.model_dump() for p in storage.list_projects()],
        "expenditures": [e.model_dump() for e in storage.list_expenditures()],
        "deadlines": [d.model_dump() for d in storage.list_deadlines()],
        "resources": [r.model_dump() for r in storage.list_resources()],
        "bookings": [b.model_dump() for b in storage.list_bookings()],
        "reports": [r.model_dump() for r in storage.list_reports()],
        "exported_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2, default=str)


def get_project_budget_status(storage: Storage, project: Project) -> dict:
    """Compute spent vs budget for a project. Core for grant tracking."""
    exps = storage.list_expenditures(project.id)
    spent = sum(e.amount_usd for e in exps)
    budget = project.budget_usd or 0.0
    remaining = budget - spent
    pct = (spent / budget * 100) if budget > 0 else 0.0
    return {
        "budget": budget,
        "spent": spent,
        "remaining": remaining,
        "pct_used": round(pct, 1),
        "num_expenditures": len(exps),
    }


def export_projects_csv(projects: list[Project], expenditures_by_project: dict[str, list[ProjectExpenditure]]) -> str:
    """Return CSV string for projects + simple spent summary. Stdlib only."""
    import csv
    import io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id", "title", "principal_investigator", "status", "start_date", "end_date",
        "sponsor", "grant_number", "budget_usd", "spent_usd", "notes", "links"
    ])
    for p in projects:
        exps = expenditures_by_project.get(p.id, [])
        spent = sum(e.amount_usd for e in exps)
        writer.writerow([
            p.id,
            p.title,
            p.principal_investigator,
            p.status.value,
            p.start_date.isoformat(),
            p.end_date.isoformat() if p.end_date else "",
            p.sponsor or "",
            p.grant_number or "",
            p.budget_usd or "",
            round(spent, 2),
            (p.notes or "").replace("\n", " ")[:200],
            ";".join(p.links),
        ])
    return output.getvalue()


def import_projects_from_csv(storage: Storage, csv_text: str) -> list[Project]:
    """Parse simple CSV and upsert projects. Returns created/updated projects.
    Expected headers (flexible): title, principal_investigator, status, start_date, ...
    grant_number, budget_usd, sponsor, description optional.
    """
    import csv
    import io
    from datetime import date
    reader = csv.DictReader(io.StringIO(csv_text))
    created = []
    for row in reader:
        normalized = {
            (k or "").strip().lower().replace(" ", "_"): (v or "")
            for k, v in row.items()
        }

        def field(*names: str) -> str:
            for name in names:
                value = normalized.get(name.strip().lower().replace(" ", "_"))
                if value:
                    return value
            return ""

        title = field("title")
        pi = field("principal_investigator", "pi")
        if not title or not pi:
            continue
        try:
            start = date.fromisoformat(field("start_date")) if field("start_date") else date.today()
        except Exception:
            start = date.today()
        try:
            end = date.fromisoformat(field("end_date")) if field("end_date") else None
        except Exception:
            end = None
        status_val = (field("status") or "active").lower()
        try:
            status = ProjectStatus(status_val)
        except Exception:
            status = ProjectStatus.ACTIVE
        budget = None
        try:
            if field("budget_usd"):
                budget = float(field("budget_usd"))
        except Exception:
            pass
        raw_links = field("links")
        split_links = raw_links.split(";") if ";" in raw_links else raw_links.split(",")
        project_kwargs = {}
        if field("id"):
            project_kwargs["id"] = field("id")
        try:
            p = Project(
                **project_kwargs,
                title=title.strip(),
                principal_investigator=pi.strip(),
                status=status,
                start_date=start,
                end_date=end,
                sponsor=field("sponsor").strip() or None,
                grant_number=field("grant_number", "grant").strip() or None,
                budget_usd=budget,
                description=field("description").strip() or None,
                notes=field("notes").strip() or None,
                links=[x.strip() for x in split_links if x.strip()],
            )
        except Exception:
            continue
        storage.upsert_project(p)
        created.append(p)
    return created


# --- Compliance helpers ---

DEADLINE_TEMPLATES = [
    {"title": "IRB Annual Continuation", "type": DeadlineType.IRB, "reminder_days_before": 21, "notes": "Include progress report, enrollment numbers, and any adverse events."},
    {"title": "IACUC Protocol Renewal", "type": DeadlineType.IACUC, "reminder_days_before": 30, "notes": "Protocol # required. Submit animal use summary."},
    {"title": "Annual Lab Safety Inspection", "type": DeadlineType.SAFETY, "reminder_days_before": 14, "notes": "Schedule with EHS. Review chemical inventory."},
    {"title": "Biosafety Cabinet Recertification", "type": DeadlineType.BIOHAZARD, "reminder_days_before": 14, "notes": "Contact certified vendor."},
    {"title": "Export Control Training Refresh", "type": DeadlineType.EXPORT_CONTROL, "reminder_days_before": 30, "notes": "Required for international collaborations."},
]

def get_deadline_templates() -> list[dict]:
    return DEADLINE_TEMPLATES


def snooze_deadline(storage: Storage, deadline_id: str, days: int, note: str = "") -> ComplianceDeadline | None:
    """Snooze by shifting due date forward. Logs in notes. Human action."""
    dl = storage.get_deadline(deadline_id)
    if not dl:
        return None
    old_due = dl.effective_due
    new_due = old_due + dt_timedelta(days=days)
    dl.due_date = new_due   # shift base due; snoozed_until can be used for temp
    dl.snoozed_until = new_due
    dl.status = DeadlineStatus.PENDING  # reset to pending on snooze
    snooze_note = f"[Snoozed +{days}d on {date.today().isoformat()}] {note}".strip()
    dl.notes = (dl.notes or "") + ("\n" + snooze_note if dl.notes else snooze_note)
    return storage.upsert_deadline(dl)


def get_compliance_calendar(storage: Storage, year: int, month: int) -> dict:
    """Return dict date -> list of deadlines for a month grid. Uses stdlib calendar."""
    import calendar
    from datetime import date as dt_date
    cal = calendar.Calendar(firstweekday=6)  # Sunday start for US labs
    month_days = list(cal.itermonthdates(year, month))
    dls = storage.list_deadlines(include_overdue=True)
    by_date: dict = {}
    for d in dls:
        ed = d.effective_due
        if ed.year == year and ed.month == month:
            if ed not in by_date:
                by_date[ed] = []
            by_date[ed].append(d)
    # Also return full day list for grid
    return {"days": month_days, "deadlines_by_date": by_date, "month": month, "year": year}


def get_overdue_deadlines(storage: Storage) -> list[ComplianceDeadline]:
    return [d for d in storage.list_deadlines() if d.is_overdue()]


# --- Resource booking ---

def create_recurring_bookings(
    storage: Storage, base: Booking, repeat: str, occurrences: int
) -> list[Booking]:
    """Create a non-conflicting recurring series atomically.

    Supports 'weekly'. Raises ValueError before writing if any occurrence would
    overlap existing active bookings or another occurrence in the same series.
    """
    group_id = base.recurrence_group_id or str(uuid.uuid4())
    base.recurrence_group_id = group_id
    candidates = [base]

    if repeat != "weekly" or occurrences < 2:
        conflicts = detect_booking_conflicts(storage, base)
        if conflicts:
            raise ValueError(f"Booking conflicts with {len(conflicts)} existing booking(s).")
        storage.upsert_booking(base)
        return [base]

    delta = timedelta(days=7)
    for i in range(1, max(1, occurrences)):
        candidates.append(
            Booking(
                resource_id=base.resource_id,
                project_id=base.project_id,
                booked_by=base.booked_by,
                start=base.start + (delta * i),
                end=base.end + (delta * i),
                purpose=base.purpose,
                status=base.status,
                is_blackout=base.is_blackout,
                recurrence_group_id=group_id,
            )
        )

    for i, candidate in enumerate(candidates):
        conflicts = detect_booking_conflicts(storage, candidate)
        if conflicts:
            raise ValueError(
                f"Occurrence {i + 1} conflicts with {len(conflicts)} existing booking(s): "
                f"{candidate.start:%Y-%m-%d %H:%M}."
            )
        for prior in candidates[:i]:
            if candidate.resource_id == prior.resource_id and _bookings_overlap(candidate, prior):
                raise ValueError(f"Occurrence {i + 1} overlaps another booking in this series.")

    for candidate in candidates:
        storage.upsert_booking(candidate)
    return candidates


def get_resource_utilization(storage: Storage, resource_id: str, days_back: int = 30) -> dict:
    """Simple usage report: count, total hours, recent bookings.

    All comparisons use naive datetimes so mixed tz-aware seed/UI values never crash.
    """
    from datetime import timedelta as td
    now = datetime.now()
    cutoff = now - td(days=days_back)
    bookings = [
        b for b in storage.list_bookings(resource_id=resource_id)
        if cutoff <= _naive(b.start) <= now and b.status != BookingStatus.CANCELLED
    ]
    total_hours = 0.0
    for b in bookings:
        delta = _naive(b.end) - _naive(b.start)
        total_hours += delta.total_seconds() / 3600.0
    return {
        "resource_id": resource_id,
        "period_days": days_back,
        "booking_count": len(bookings),
        "total_hours": round(total_hours, 1),
        "recent": sorted(bookings, key=lambda x: _naive(x.start), reverse=True)[:5],
    }


def suggest_alternative_slots(
    storage: Storage, resource_id: str, desired_start: datetime, duration_minutes: int = 60, max_suggestions: int = 3
) -> list[datetime]:
    """Suggest next available start times after desired (simple greedy, same day then next days).
    Suggests 1-hour slots.
    """
    from datetime import timedelta as td
    suggestions = []
    # Start search from desired or now (naive for compat)
    search_start = max(_naive(desired_start), datetime.now())
    # Look up to 5 days
    for day_offset in range(0, 6):
        day_base = search_start.replace(hour=8, minute=0, second=0) + td(days=day_offset)  # assume 8am start
        for slot in range(10):  # 8am-6pm slots
            candidate = day_base + td(minutes=slot * 60)
            if candidate < search_start:
                continue
            end_cand = candidate + td(minutes=duration_minutes)
            temp_b = Booking(
                resource_id=resource_id,
                booked_by="suggest",
                start=candidate,
                end=end_cand,
                purpose="suggestion",
            )
            if not detect_booking_conflicts(storage, temp_b):
                suggestions.append(candidate)
                if len(suggestions) >= max_suggestions:
                    return suggestions
    return suggestions


# --- Search and exports ---

def search_all(storage: Storage, query: str) -> dict:
    """Simple global search across projects, deadlines, resources, bookings. Returns matches."""
    q = query.lower().strip()
    if not q:
        return {"projects": [], "deadlines": [], "resources": [], "bookings": []}
    results = {"projects": [], "deadlines": [], "resources": [], "bookings": []}
    for p in storage.list_projects():
        if q in p.title.lower() or q in p.principal_investigator.lower() or (p.grant_number and q in p.grant_number.lower()):
            results["projects"].append(p)
    for d in storage.list_deadlines():
        if q in d.title.lower() or q in d.type.value:
            results["deadlines"].append(d)
    for r in storage.list_resources():
        if q in r.name.lower() or (r.location and q in r.location.lower()):
            results["resources"].append(r)
    for b in storage.list_bookings():
        if q in b.purpose.lower() or q in b.booked_by.lower():
            results["bookings"].append(b)
    return results


def export_deadlines_csv(deadlines: list) -> str:
    """CSV for deadlines."""
    import csv
    import io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "title", "type", "due_date", "status", "project_id", "reminder_days"])
    for d in deadlines:
        writer.writerow([d.id, d.title, d.type.value, d.due_date, d.status.value, d.project_id or "", d.reminder_days_before])
    return output.getvalue()


def export_resources_csv(resources: list) -> str:
    """CSV for resources."""
    import csv
    import io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "name", "type", "location", "capacity", "is_active"])
    for r in resources:
        writer.writerow([r.id, r.name, r.type.value, r.location or "", r.capacity or "", r.is_active])
    return output.getvalue()


def export_expenditures_csv(expenditures: list[ProjectExpenditure]) -> str:
    """CSV for project expenditure records."""
    import csv
    import io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "project_id", "amount_usd", "date", "category", "description"])
    for e in expenditures:
        writer.writerow([e.id, e.project_id, e.amount_usd, e.date, e.category, e.description or ""])
    return output.getvalue()


def create_institutional_pack(storage: Storage) -> bytes:
    """Create zip bytes with JSON snapshot + CSVs + sample report MD. For institutional handoff."""
    import zipfile
    import io
    mem_zip = io.BytesIO()
    with zipfile.ZipFile(mem_zip, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Full JSON
        zf.writestr("resloom_full_snapshot.json", export_all_to_json(storage))
        # CSVs
        zf.writestr("projects.csv", export_projects_csv(storage.list_projects(), {p.id: storage.list_expenditures(p.id) for p in storage.list_projects()}))
        zf.writestr("expenditures.csv", export_expenditures_csv(storage.list_expenditures()))
        zf.writestr("deadlines.csv", export_deadlines_csv(storage.list_deadlines()))
        zf.writestr("resources.csv", export_resources_csv(storage.list_resources()))
        # Sample report if any
        reps = storage.list_reports()
        if reps:
            zf.writestr("sample_report.md", reps[0].content_markdown)
        zf.writestr("manifest.txt", f"Resloom Institutional Pack\nGenerated: {datetime.now(timezone.utc).isoformat()}\n")
    mem_zip.seek(0)
    return mem_zip.read()

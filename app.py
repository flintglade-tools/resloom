"""Resloom — Streamlit UI

Multi-view workspace for research groups:
- Overview
- Projects & Grants
- Compliance Deadlines
- Resources & Bookings
- Reports

Local-first. All edits are immediate.
"""

import os
import calendar
from datetime import date, datetime, timedelta

import html

import streamlit as st

from resloom.access import host_allowed
from resloom.models import (
    Project, ComplianceDeadline, Resource, Booking, ProjectStatus, DeadlineType, DeadlineStatus, ResourceType, BookingStatus,
    ProjectExpenditure,
)
from resloom.storage import get_storage
from resloom.services import (
    get_upcoming_deadlines, detect_booking_conflicts, create_report_draft,
    export_all_to_json, get_project_summary, get_project_budget_status,
    export_projects_csv, import_projects_from_csv,
    get_deadline_templates, snooze_deadline, get_compliance_calendar, get_overdue_deadlines,
    create_recurring_bookings, get_resource_utilization, suggest_alternative_slots,
    save_edited_report, export_report_as_pdf_bytes,
    search_all, export_deadlines_csv, export_resources_csv, create_institutional_pack,
)

st.set_page_config(
    page_title="Resloom",
    page_icon="🔬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Serve only pages opened as 127.0.0.1, localhost or [::1]. Requests from a page that reached the
# server under another host name (DNS rebinding) stop here, before any data is read.
if not host_allowed(st.context.headers.get("Host")):
    st.error("Resloom only answers on 127.0.0.1, localhost or [::1].")
    st.stop()

# --- Storage (cached for session) ---
@st.cache_resource
def _get_storage():
    db_path = os.environ.get("RESLOOM_DB", "data/resloom.db")
    return get_storage(db_path)


storage = _get_storage()
settings = storage.get_settings()

# --- Sidebar (global controls + info) ---
with st.sidebar:
    st.title("Resloom")
    st.caption("Lightweight research workspace")

    with st.expander("⚙️ Settings", expanded=False):
        new_org = st.text_input("Organization", value=settings.org_name)
        new_remind = st.number_input("Default reminder days", min_value=0, max_value=90, value=settings.default_reminder_days)
        if st.button("Save Settings", use_container_width=True):
            settings.org_name = new_org.strip() or "Research Group"
            settings.default_reminder_days = int(new_remind)
            storage.save_settings(settings)
            st.success("Settings saved.")
            st.rerun()

    st.markdown(f"**Organization:** {settings.org_name}")

    search_q = st.text_input("🔍 Global Search", placeholder="project, deadline, resource...", key="global_search")
    if search_q:
        matches = search_all(storage, search_q)
        with st.expander("Search Results", expanded=True):
            for cat, items in matches.items():
                if items:
                    st.markdown(f"**{cat.title()}** ({len(items)})")
                    for item in items[:3]:
                        if cat == "projects":
                            st.write(f"- {item.title} ({item.principal_investigator})")
                        elif cat == "deadlines":
                            st.write(f"- {item.title} ({item.due_date})")
                        elif cat == "resources":
                            st.write(f"- {item.name}")
                        else:
                            st.write(f"- {item.purpose[:40]}")

    st.divider()
    st.markdown("### Data & Exports")
    st.caption(f"Database: `{storage.db_path}`")

    if st.button("Export full snapshot (JSON)", use_container_width=True):
        snap = export_all_to_json(storage)
        st.download_button("Download resloom-export.json", data=snap, file_name=f"resloom-export-{date.today().isoformat()}.json", mime="application/json", use_container_width=True)

    if st.button("Export Projects CSV", use_container_width=True):
        csv = export_projects_csv(storage.list_projects(), {p.id: storage.list_expenditures(p.id) for p in storage.list_projects()})
        st.download_button("projects.csv", data=csv, file_name="projects.csv", mime="text/csv", use_container_width=True)
    if st.button("Export Deadlines CSV", use_container_width=True):
        csv = export_deadlines_csv(storage.list_deadlines())
        st.download_button("deadlines.csv", data=csv, file_name="deadlines.csv", mime="text/csv", use_container_width=True)
    if st.button("Export Resources CSV", use_container_width=True):
        csv = export_resources_csv(storage.list_resources())
        st.download_button("resources.csv", data=csv, file_name="resources.csv", mime="text/csv", use_container_width=True)

    if st.button("Export Institutional Pack (ZIP)", use_container_width=True):
        pack = create_institutional_pack(storage)
        st.download_button("resloom-institutional-pack.zip", data=pack, file_name=f"resloom-pack-{date.today().isoformat()}.zip", mime="application/zip", use_container_width=True)

    st.divider()
    st.markdown("### Quick Help")
    st.caption("All data is local. Edits save immediately.")
    if st.button("Reload data (clear cache)", use_container_width=True):
        st.cache_resource.clear()
        st.rerun()

    st.caption("Resloom 0.5.2")

# --- Main header ---
st.header("Resloom — Research Group Workspace")
st.caption("Track projects, deadlines, shared resources, and generate auditable reports.")

# --- Tabs (core multi-view) ---
tab_overview, tab_projects, tab_compliance, tab_resources, tab_reports = st.tabs([
    "📊 Overview",
    "📁 Projects & Grants",
    "📅 Compliance",
    "🔧 Resources & Bookings",
    "📝 Reports",
])

# ============================================================
# OVERVIEW
# ============================================================
with tab_overview:
    st.subheader("At a Glance")

    projects = storage.list_projects()
    deadlines = get_upcoming_deadlines(storage, days=30)
    resources = storage.list_resources()
    bookings = storage.list_bookings()

    overdue_dls = get_overdue_deadlines(storage)
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Active Projects", len([p for p in projects if p.is_active()]))
    col2.metric("Upcoming Deadlines (30d)", len(deadlines))
    col3.metric("⚠️ Overdue Deadlines", len(overdue_dls), delta="action needed" if overdue_dls else None, delta_color="inverse")
    col4.metric("Resources", len(resources))
    col5.metric("Open Bookings", len([b for b in bookings if b.status == BookingStatus.CONFIRMED]))

    st.divider()

    st.markdown("### 🔥 Upcoming Deadlines (next 30 days + overdue)")
    if deadlines:
        for d in deadlines[:8]:
            proj = storage.get_project(d.project_id) if d.project_id else None
            proj_str = f" ({proj.title})" if proj else ""
            overdue = "⚠️ OVERDUE" if d.is_overdue() else ""
            due_label = d.effective_due
            snooze_note = " (snoozed)" if d.snoozed_until and d.snoozed_until > d.due_date else ""
            st.write(f"- **{due_label}** — {d.title}{proj_str} [{d.type.value}] {overdue}{snooze_note}")
    else:
        st.info("No upcoming deadlines.")

    st.markdown("### Recent Projects")
    for p in projects[:4]:
        st.write(f"- **{p.title}** — {p.principal_investigator} • {p.status.value}")

    st.caption("Use the other tabs to manage data. All changes are persisted locally.")

# ============================================================
# PROJECTS & GRANTS
# ============================================================
with tab_projects:
    st.subheader("Projects & Grants")

    # Quick CSV tools for institutional workflows
    c_imp, c_exp = st.columns(2)
    with c_imp:
        st.markdown("**CSV Import** (title, principal_investigator, status, start_date, ...)")
        up = st.file_uploader("Upload projects CSV", type=["csv"], key="proj_csv", label_visibility="collapsed")
        # The uploaded file stays in the widget across reruns, so import each upload only once.
        if up is not None and st.session_state.get("proj_csv_imported") != up.file_id:
            st.session_state["proj_csv_imported"] = up.file_id
            try:
                text = up.read().decode("utf-8")
                new_ps = import_projects_from_csv(storage, text)
                if new_ps:
                    st.session_state["proj_csv_message"] = f"Imported/updated {len(new_ps)} project(s)"
                    st.rerun()
                else:
                    st.warning("No valid rows found.")
            except Exception as e:
                st.error(f"Import error: {e}")
        if "proj_csv_message" in st.session_state:
            st.success(st.session_state.pop("proj_csv_message"))

    with c_exp:
        if st.button("Export All Projects (CSV)", use_container_width=True):
            projs = storage.list_projects()
            exp_map = {p.id: storage.list_expenditures(p.id) for p in projs}
            csv_str = export_projects_csv(projs, exp_map)
            st.download_button(
                "Download projects.csv",
                data=csv_str,
                file_name=f"resloom-projects-{date.today().isoformat()}.csv",
                mime="text/csv",
                use_container_width=True,
            )

    with st.expander("➕ Add New Project", expanded=False):
        with st.form("new_project", clear_on_submit=True):
            c1, c2 = st.columns(2)
            title = c1.text_input("Title*", max_chars=200)
            pi = c2.text_input("Principal Investigator*", max_chars=120)
            c3, c4 = st.columns(2)
            status = c3.selectbox("Status", [s.value for s in ProjectStatus], index=1)
            start = c4.date_input("Start Date", value=date.today())
            c5, c6 = st.columns(2)
            has_end = c5.checkbox("Set end date", value=False)
            end = c5.date_input("End Date", value=date.today() + timedelta(days=365), disabled=not has_end)
            sponsor = c6.text_input("Sponsor / Funder")
            c7, c8 = st.columns(2)
            grant_no = c7.text_input("Grant Number")
            budget = c8.number_input("Budget (USD)", min_value=0.0, step=1000.0, value=0.0)
            desc = st.text_area("Description", max_chars=2000)

            submitted = st.form_submit_button("Create Project")
            if submitted and title and pi:
                try:
                    p = Project(
                        title=title.strip(),
                        principal_investigator=pi.strip(),
                        status=ProjectStatus(status),
                        start_date=start,
                        end_date=end if has_end else None,
                        sponsor=sponsor.strip() or None,
                        grant_number=grant_no.strip() or None,
                        budget_usd=budget if budget > 0 else None,
                        description=desc.strip() or None,
                    )
                    storage.upsert_project(p)
                    st.success(f"Created: {p.title}")
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not create project: {e}")

    st.divider()
    st.markdown("### Current Projects — Edit, Track Budget & Expenditures")

    projs = storage.list_projects()
    if projs:
        for p in projs:
            budget_status = get_project_budget_status(storage, p)
            with st.container(border=True):
                header_cols = st.columns([4, 2, 1.5, 1])
                header_cols[0].markdown(f"**{p.title}**")
                header_cols[1].write(f"PI: {p.principal_investigator}")
                header_cols[2].write(f"{p.status.value}")
                if header_cols[3].button("Delete", key=f"delp-{p.id}"):
                    # Cascades expenditures; unlinks deadlines, bookings, reports
                    storage.delete_project(p.id)
                    st.rerun()

                meta = []
                if p.grant_number:
                    meta.append(f"Grant: {p.grant_number}")
                if p.sponsor:
                    meta.append(p.sponsor or "")
                if p.budget_usd:
                    meta.append(f"Budget ${p.budget_usd:,.0f}")
                if meta:
                    st.caption(" | ".join(meta))

                # Budget summary row
                b1, b2, b3, b4 = st.columns(4)
                b1.metric("Budget", f"${budget_status['budget']:,.0f}" if budget_status['budget'] else "—")
                b2.metric("Spent", f"${budget_status['spent']:,.2f}")
                b3.metric("Remaining", f"${budget_status['remaining']:,.2f}")
                b4.metric("% Used", f"{budget_status['pct_used']}%")

                # Quick status change (workflow)
                new_status = st.selectbox(
                    "Change status",
                    [s.value for s in ProjectStatus],
                    index=[s.value for s in ProjectStatus].index(p.status.value),
                    key=f"stat-{p.id}",
                )
                if new_status != p.status.value:
                    if st.button("Save Status", key=f"savestat-{p.id}"):
                        p.status = ProjectStatus(new_status)
                        storage.upsert_project(p)
                        st.rerun()

                # Rich edit + expenditures in expander
                with st.expander("Edit details, notes, links + manage expenditures", expanded=False):
                    with st.form(f"edit_project_{p.id}", clear_on_submit=False):
                        ec1, ec2 = st.columns(2)
                        e_title = ec1.text_input("Title", value=p.title, max_chars=200)
                        e_pi = ec2.text_input("PI", value=p.principal_investigator, max_chars=120)
                        ec3, ec4 = st.columns(2)
                        e_status = ec3.selectbox("Status", [s.value for s in ProjectStatus], index=[s.value for s in ProjectStatus].index(p.status.value))
                        e_start = ec4.date_input("Start", value=p.start_date)
                        ec5, ec6 = st.columns(2)
                        e_has_end = ec5.checkbox("Has end date", value=p.end_date is not None, key=f"hasend-{p.id}")
                        e_end = ec5.date_input(
                            "End",
                            value=p.end_date or (p.start_date + timedelta(days=365)),
                            disabled=not e_has_end,
                            key=f"end-{p.id}",
                        )
                        e_sponsor = ec6.text_input("Sponsor", value=p.sponsor or "")
                        ec7, ec8 = st.columns(2)
                        e_grant = ec7.text_input("Grant #", value=p.grant_number or "")
                        e_budget = ec8.number_input("Budget USD", min_value=0.0, value=float(p.budget_usd or 0.0), step=1000.0)
                        e_desc = st.text_area("Description", value=p.description or "", max_chars=2000)
                        e_notes = st.text_area("Notes (internal)", value=p.notes or "", max_chars=4000)
                        e_links = st.text_input("Links (comma separated)", value=",".join(p.links))

                        if st.form_submit_button("Save Project Changes"):
                            try:
                                p.title = e_title.strip()
                                p.principal_investigator = e_pi.strip()
                                p.status = ProjectStatus(e_status)
                                p.start_date = e_start
                                p.end_date = e_end if e_has_end else None
                                p.sponsor = e_sponsor.strip() or None
                                p.grant_number = e_grant.strip() or None
                                p.budget_usd = e_budget if e_budget > 0 else None
                                p.description = e_desc.strip() or None
                                p.notes = e_notes.strip() or None
                                p.links = [x.strip() for x in e_links.split(",") if x.strip()]
                                storage.upsert_project(p)
                                st.success("Project updated.")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Could not save project: {e}")

                    st.markdown("**Expenditures**")
                    # Add expenditure
                    with st.form(f"add_exp_{p.id}", clear_on_submit=True):
                        a1, a2, a3 = st.columns([2, 1, 2])
                        amt = a1.number_input("Amount USD", min_value=0.0, step=100.0, value=500.0)
                        edate = a2.date_input("Date", value=date.today())
                        cat = a3.text_input("Category", value="personnel")
                        edesc = st.text_input("Description (optional)")
                        if st.form_submit_button("Add Expenditure"):
                            if amt > 0:
                                exp = ProjectExpenditure(
                                    project_id=p.id,
                                    amount_usd=amt,
                                    date=edate,
                                    category=cat.strip() or "other",
                                    description=edesc.strip() or None,
                                )
                                storage.upsert_expenditure(exp)
                                st.success("Expenditure added.")
                                st.rerun()

                    # List expenditures for this project
                    exps = storage.list_expenditures(p.id)
                    if exps:
                        for e in exps[:6]:
                            ecol = st.columns([1, 3, 1])
                            ecol[0].write(f"${e.amount_usd:,.2f}")
                            ecol[1].write(f"{e.date} • {e.category} {e.description or ''}")
                            if ecol[2].button("Delete", key=f"delexp-{e.id}"):
                                storage.delete_expenditure(e.id)
                                st.rerun()
                    else:
                        st.caption("No expenditures yet.")

                if p.notes:
                    st.caption(f"Notes: {p.notes[:120]}...")
                if p.links:
                    st.caption("Links: " + ", ".join(p.links[:2]))
    else:
        st.info("No projects yet. Add one above or import CSV.")

# ============================================================
# COMPLIANCE
# ============================================================
with tab_compliance:
    st.subheader("Compliance Deadlines & Calendar")

    # Prominent overdue + quick actions
    overdue = get_overdue_deadlines(storage)
    upcoming = get_upcoming_deadlines(storage, days=30)
    if overdue:
        st.error(f"⚠️ {len(overdue)} overdue deadline(s) require immediate attention. See list below.")

    proj_opts = {f"{p.title} ({p.id[:8]})": p.id for p in storage.list_projects()}
    proj_opts["(None / org-wide)"] = None

    # Templates
    with st.expander("📋 Add from Template (common compliance items)", expanded=False):
        templates = get_deadline_templates()
        tcol1, tcol2 = st.columns([3, 1])
        t_choice = tcol1.selectbox("Choose template", [t["title"] for t in templates], index=0)
        t_proj = tcol2.selectbox("Link to project", list(proj_opts.keys()), key="tmpl_proj")
        if st.button("Add from Template"):
            tmpl = next((t for t in templates if t["title"] == t_choice), None)
            if tmpl:
                dl = ComplianceDeadline(
                    project_id=proj_opts[t_proj],
                    title=tmpl["title"],
                    type=tmpl["type"],
                    due_date=date.today() + timedelta(days=30),
                    reminder_days_before=tmpl["reminder_days_before"],
                    notes=tmpl["notes"],
                )
                storage.upsert_deadline(dl)
                st.success(f"Added: {tmpl['title']}")
                st.rerun()

    # Add custom
    with st.expander("➕ Add Custom Deadline", expanded=False):
        with st.form("new_deadline"):
            c1, c2 = st.columns(2)
            proj_label = c1.selectbox("Link to Project (optional)", list(proj_opts.keys()), index=0)
            pid = proj_opts[proj_label]
            title = c2.text_input("Title*", max_chars=200)
            c3, c4 = st.columns(2)
            dtype = c3.selectbox("Type", [t.value for t in DeadlineType])
            due = c4.date_input("Due Date", value=date.today() + timedelta(days=30))
            c5, c6 = st.columns(2)
            dstatus = c5.selectbox("Status", [s.value for s in DeadlineStatus], index=0)
            rem = c6.number_input("Reminder days before", min_value=0, max_value=90, value=settings.default_reminder_days)
            notes = st.text_area("Notes", max_chars=2000)
            if st.form_submit_button("Save Deadline"):
                if title:
                    dl = ComplianceDeadline(project_id=pid, title=title.strip(), type=DeadlineType(dtype), due_date=due, status=DeadlineStatus(dstatus), reminder_days_before=int(rem), notes=notes.strip() or None)
                    storage.upsert_deadline(dl)
                    st.success("Deadline saved.")
                    st.rerun()

    st.divider()

    # Filters + Calendar
    st.markdown("### Filters")
    f1, f2, f3 = st.columns(3)
    all_types = [t.value for t in DeadlineType]
    type_filter = f1.multiselect("Type", all_types, default=all_types)
    status_filter = f2.multiselect("Status", [s.value for s in DeadlineStatus], default=[s.value for s in DeadlineStatus if s not in (DeadlineStatus.APPROVED, DeadlineStatus.SUBMITTED)])
    show_overdue_only = f3.checkbox("Overdue only", value=False)

    all_dls = storage.list_deadlines()
    filtered = []
    for d in all_dls:
        if d.type.value not in type_filter:
            continue
        if d.status.value not in status_filter:
            continue
        if show_overdue_only and not d.is_overdue():
            continue
        filtered.append(d)

    # Simple Calendar Grid (current + next month)
    st.markdown("### Calendar View (current month + next)")
    today = date.today()
    for mo_offset in [0, 1]:
        y, m = today.year, today.month + mo_offset
        if m > 12:
            y += 1
            m = 1
        cal_data = get_compliance_calendar(storage, y, m)
        st.markdown(f"**{calendar.month_name[m]} {y}**")
        # Build 7-col week grid
        weeks = []
        week = []
        for day in cal_data["days"]:
            week.append(day)
            if len(week) == 7:
                weeks.append(week)
                week = []
        if week:
            weeks.append(week)
        for week_days in weeks[:6]:  # limit rows
            cols = st.columns(7)
            for i, day in enumerate(week_days):
                dls_on_day = cal_data["deadlines_by_date"].get(day, [])
                label = f"{day.day}"
                if dls_on_day:
                    label += f" ({len(dls_on_day)})"
                    with cols[i]:
                        st.caption(label)
                        for dd in dls_on_day[:2]:
                            color = "red" if dd.is_overdue() else "orange"
                            st.markdown(f"<span style='color:{color}; font-size:0.8em'>{html.escape(dd.title[:18])}</span>", unsafe_allow_html=True)
                else:
                    cols[i].caption(label)
        st.caption(" ")

    # Bulk actions
    st.divider()
    st.markdown("### Deadlines List + Bulk Actions")
    if filtered:
        # Selection state
        if "selected_dl_ids" not in st.session_state:
            st.session_state.selected_dl_ids = set()

        # Select all / none
        sa, sn = st.columns(2)
        if sa.button("Select All Filtered"):
            st.session_state.selected_dl_ids = {d.id for d in filtered}
            st.rerun()
        if sn.button("Clear Selection"):
            st.session_state.selected_dl_ids = set()
            st.rerun()

        selected = st.session_state.selected_dl_ids
        st.caption(f"Selected: {len(selected)}")

        # Bulk buttons
        b1, b2, b3, b4, b5 = st.columns(5)
        bulk_ids = list(selected)
        if b1.button("Mark Submitted", disabled=len(bulk_ids)==0):
            storage.bulk_update_deadline_status(bulk_ids, DeadlineStatus.SUBMITTED)
            st.session_state.selected_dl_ids = set()
            st.rerun()
        if b2.button("Mark Approved", disabled=len(bulk_ids)==0):
            storage.bulk_update_deadline_status(bulk_ids, DeadlineStatus.APPROVED)
            st.session_state.selected_dl_ids = set()
            st.rerun()
        if b3.button("Snooze +7d", disabled=len(bulk_ids)==0):
            for bid in bulk_ids:
                snooze_deadline(storage, bid, 7, "Bulk snooze")
            st.session_state.selected_dl_ids = set()
            st.rerun()
        if b4.button("Snooze +14d", disabled=len(bulk_ids)==0):
            for bid in bulk_ids:
                snooze_deadline(storage, bid, 14, "Bulk snooze")
            st.session_state.selected_dl_ids = set()
            st.rerun()
        if b5.button("Delete Selected", disabled=len(bulk_ids)==0):
            for bid in bulk_ids:
                storage.delete_deadline(bid)
            st.session_state.selected_dl_ids = set()
            st.rerun()

        # List with per-item controls + checkbox
        for d in sorted(filtered, key=lambda x: x.effective_due):
            proj = storage.get_project(d.project_id) if d.project_id else None
            pstr = f" • {proj.title}" if proj else ""
            overdue_flag = "⚠️ OVERDUE" if d.is_overdue() else ""
            snooze_info = f" (snoozed to {d.snoozed_until})" if d.snoozed_until else ""
            checked = d.id in selected
            cbox_col, main_col = st.columns([0.8, 9])
            new_checked = cbox_col.checkbox(f"Select {d.title}", value=checked, key=f"sel-{d.id}", label_visibility="collapsed")
            if new_checked != checked:
                if new_checked:
                    st.session_state.selected_dl_ids.add(d.id)
                else:
                    st.session_state.selected_dl_ids.discard(d.id)
                # No rerun to allow multi
            with main_col:
                with st.expander(f"{d.effective_due} — {d.title}{pstr} [{d.type.value}] — {d.status.value} {overdue_flag}{snooze_info}", expanded=False):
                    st.write(f"**Days until due (effective):** {d.days_until_due}")
                    st.write(f"**Reminder lead:** {d.reminder_days_before} days")
                    if d.notes:
                        st.text(d.notes)
                    # Per item actions
                    ns = st.selectbox("Status", [s.value for s in DeadlineStatus], index=[s.value for s in DeadlineStatus].index(d.status.value), key=f"ns-{d.id}")
                    if st.button("Save status", key=f"sv-{d.id}"):
                        d.status = DeadlineStatus(ns)
                        storage.upsert_deadline(d)
                        st.rerun()
                    s1, s2, s3 = st.columns(3)
                    if s1.button("Snooze +7d", key=f"sn7-{d.id}"):
                        snooze_deadline(storage, d.id, 7)
                        st.rerun()
                    if s2.button("Snooze +14d", key=f"sn14-{d.id}"):
                        snooze_deadline(storage, d.id, 14)
                        st.rerun()
                    if s3.button("Delete", key=f"del-{d.id}"):
                        storage.delete_deadline(d.id)
                        st.rerun()
    else:
        st.info("No deadlines match filters.")

# ============================================================
# RESOURCES & BOOKINGS
# ============================================================
with tab_resources:
    st.subheader("Shared Resources & Booking")

    # Resources list + add (enhanced with blackout note)
    with st.expander("➕ Add Resource", expanded=False):
        with st.form("new_resource"):
            c1, c2 = st.columns(2)
            rname = c1.text_input("Name*", max_chars=120)
            rtype = c2.selectbox("Type", [t.value for t in ResourceType])
            c3, c4 = st.columns(2)
            rloc = c3.text_input("Location")
            rcap = c4.number_input("Capacity (0 = unset)", min_value=0, step=1, value=0)
            rnotes = st.text_area("Notes")
            if st.form_submit_button("Add Resource"):
                if rname:
                    res = Resource(
                        name=rname.strip(),
                        type=ResourceType(rtype),
                        location=rloc.strip() or None,
                        capacity=int(rcap) if rcap and rcap > 0 else None,
                        notes=rnotes.strip() or None,
                    )
                    storage.upsert_resource(res)
                    st.success("Resource added.")
                    st.rerun()

    st.markdown("### Resources")
    res_list = storage.list_resources()
    for r in res_list:
        st.write(f"- **{r.name}** ({r.type.value}) — {r.location or 'no location'} | cap: {r.capacity or 'n/a'}")

    st.divider()

    # Pending approvals section (approval workflow stub)
    st.markdown("### Pending Approvals (needs confirmation)")
    pending = [b for b in storage.list_bookings() if b.status == BookingStatus.PENDING]
    if pending:
        for b in pending:
            res = next((rr for rr in res_list if rr.id == b.resource_id), None)
            rname = res.name if res else b.resource_id
            st.write(f"- {b.start.strftime('%Y-%m-%d %H:%M')} | {rname} | {b.booked_by}: {b.purpose}")
            if st.button("Approve (confirm)", key=f"approve-{b.id}"):
                b.status = BookingStatus.CONFIRMED
                storage.upsert_booking(b)
                st.success("Approved.")
                st.rerun()
    else:
        st.caption("No pending bookings.")

    st.divider()

    st.markdown("### Book a Resource")
    with st.form("new_booking"):
        c1, c2 = st.columns(2)
        res_map = {f"{r.name} ({r.type.value})": r.id for r in res_list}
        if not res_map:
            st.warning("Add resources first.")
        else:
            chosen_res = c1.selectbox("Resource", list(res_map.keys()))
            rid = res_map[chosen_res]

            proj_map = {"(none)": None}
            proj_map.update({f"{p.title}": p.id for p in storage.list_projects()})
            chosen_proj = c2.selectbox("Link to Project", list(proj_map.keys()))
            pid = proj_map[chosen_proj]

            c3, c4, c5 = st.columns(3)
            bdate = c3.date_input("Date", value=date.today() + timedelta(days=1))
            bstart = c4.time_input("Start time", value=datetime(2026, 7, 9, 9, 0).time())
            bend = c5.time_input("End time", value=datetime(2026, 7, 9, 11, 0).time())

            booked_by = st.text_input("Booked by*", value="Lab Member")
            purpose = st.text_input("Purpose*", value="Research activity")

            c_rep, c_blk = st.columns(2)
            repeat = c_rep.selectbox("Repeat", ["none", "weekly"], index=0)
            occ = c_rep.number_input("Occurrences (incl. first)", min_value=1, max_value=12, value=1, step=1, disabled=(repeat=="none"))
            is_blackout = c_blk.checkbox("Maintenance / Blackout (blocks all)", value=False)
            if is_blackout:
                purpose = st.text_input("Purpose (maintenance)", value="Maintenance / calibration", key="blk_purp")

            if st.form_submit_button("Create Booking"):
                if booked_by and purpose:
                    start_dt = datetime.combine(bdate, bstart)
                    end_dt = datetime.combine(bdate, bend)
                    if end_dt <= start_dt:
                        st.error("End time must be after start time.")
                    else:
                        try:
                            new_b = Booking(
                                resource_id=rid,
                                project_id=pid,
                                booked_by=booked_by.strip(),
                                start=start_dt,
                                end=end_dt,
                                purpose=purpose.strip(),
                                is_blackout=is_blackout,
                                status=BookingStatus.PENDING if not is_blackout else BookingStatus.CONFIRMED,
                            )
                        except Exception as e:
                            st.error(f"Invalid booking: {e}")
                            new_b = None
                        if new_b is not None:
                            conflicts = detect_booking_conflicts(storage, new_b)
                            if conflicts:
                                st.error("Conflict detected with existing booking(s):")
                                for c in conflicts:
                                    st.write(f"- {c.start} → {c.end} by {c.booked_by}: {c.purpose}")
                                duration_m = max(15, int((end_dt - start_dt).total_seconds() / 60))
                                alts = suggest_alternative_slots(storage, rid, start_dt, duration_minutes=duration_m)
                                if alts:
                                    st.info("Suggested alternatives (first available):")
                                    for a in alts:
                                        st.write(f"  • {a.strftime('%Y-%m-%d %H:%M')}")
                            else:
                                created_ok = False
                                if repeat != "none" and occ > 1:
                                    try:
                                        created = create_recurring_bookings(storage, new_b, repeat, int(occ))
                                        st.success(f"Created recurring series ({len(created)} bookings).")
                                        created_ok = True
                                    except ValueError as e:
                                        st.error(str(e))
                                else:
                                    storage.upsert_booking(new_b)
                                    st.success("Booking created (pending approval if normal).")
                                    created_ok = True
                                if created_ok:
                                    st.rerun()

    st.divider()

    st.markdown("### Resource Usage (last 30 days)")
    if res_list:
        util_res = st.selectbox("Resource for stats", [r.name for r in res_list], key="util_sel")
        sel_r = next((r for r in res_list if r.name == util_res), None)
        if sel_r:
            util = get_resource_utilization(storage, sel_r.id, 30)
            st.write(f"**{util['booking_count']}** bookings • **{util['total_hours']}** hours")
            if util["recent"]:
                st.caption("Recent:")
                for rb in util["recent"]:
                    st.write(f"- {rb.start.strftime('%m/%d %H:%M')}–{rb.end.strftime('%H:%M')}: {rb.purpose[:40]}")

    st.divider()
    st.markdown("### Current Bookings")
    for b in storage.list_bookings():
        res = next((r for r in res_list if r.id == b.resource_id), None)
        rname = res.name if res else b.resource_id
        blk = " [BLACKOUT]" if b.is_blackout else ""
        stat = b.status.value
        rec = " (recurring)" if b.recurrence_group_id else ""
        st.write(f"- **{b.start.strftime('%Y-%m-%d %H:%M')}–{b.end.strftime('%H:%M')}** | {rname}{blk} | {stat}{rec} | {b.booked_by}: {b.purpose}")
        if st.button("Cancel", key=f"cancelb-{b.id}"):
            b.status = BookingStatus.CANCELLED
            storage.upsert_booking(b)
            st.rerun()

# ============================================================
# REPORTS
# ============================================================
with tab_reports:
    st.subheader("Progress & Summary Reports")

    st.markdown("### Create Draft Report")
    with st.form("create_report"):
        proj_opts = {"(Organization-wide summary)": None}
        proj_opts.update({f"{p.title}": p.id for p in storage.list_projects()})
        proj_label = st.selectbox("For Project", list(proj_opts.keys()))
        pid = proj_opts[proj_label]

        c1, c2 = st.columns(2)
        title = c1.text_input("Report Title*", value=f"Progress Summary - {date.today().strftime('%B %Y')}")
        report_type = c2.selectbox("Template Type", ["progress", "annual", "compliance", "resource"], index=0)

        author = st.text_input("Prepared by", value="Research Coordinator")
        use_llm = st.checkbox(
            "Draft the narrative with a local LLM (Ollama at localhost:11434)",
            value=False,
            help="Falls back to the template if Ollama is not running."
        )

        if st.form_submit_button("Generate Draft"):
            if title:
                draft = create_report_draft(
                    storage, pid, title.strip(), author.strip(), report_type=report_type, use_llm=use_llm
                )
                st.success(f"Draft created: {draft.title} (v{draft.version})")
                st.rerun()

    st.divider()
    st.markdown("### Edit, Version History & Export Reports")
    reports = storage.list_reports()
    if reports:
        for rep in reports:
            proj = storage.get_project(rep.project_id) if rep.project_id else None
            ptitle = proj.title if proj else "Org-wide"
            with st.expander(f"{rep.title} (v{rep.version} • {rep.report_type} • {rep.generated_at.strftime('%Y-%m-%d')})", expanded=not rep.human_edited):
                st.caption(f"Project: {ptitle} | Last edited by: {rep.last_edited_by or '—'}")

                # Side-by-side: source data | editor
                left, right = st.columns([1, 2])
                with left:
                    st.markdown("**Source Data Summary**")
                    if proj:
                        s = get_project_summary(storage, rep.project_id)
                        st.write(f"Status: {proj.status.value}")
                        if proj.budget_usd:
                            bs = get_project_budget_status(storage, proj)
                            st.write(f"Budget: ${proj.budget_usd:,.0f} | Spent ~${bs.get('spent',0):,.0f}")
                        if s.get("active_deadlines"):
                            st.write(f"Active deadlines: {len(s['active_deadlines'])}")
                    st.caption("(Data from live records at generation time)")

                with right:
                    edited = st.text_area(
                        "Report content (Markdown — fully editable)",
                        value=rep.content_markdown,
                        height=260,
                        key=f"rep-{rep.id}",
                    )

                # Version history
                if rep.history:
                    with st.expander(f"Version history ({len(rep.history)} prior)", expanded=False):
                        for h in reversed(rep.history[-5:]):
                            st.caption(f"v{h['version']} @ {h['edited_at'][:16]}")
                            st.text(h['content'][:300] + ("..." if len(h['content'])>300 else ""))

                c1, c2, c3 = st.columns(3)
                if c1.button("Save edits (new version)", key=f"save-rep-{rep.id}"):
                    updated = save_edited_report(storage, rep, edited, "User")
                    st.success(f"Saved v{updated.version}.")
                    st.rerun()

                safe_name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in rep.title)[:60]
                c2.download_button(
                    "Export .md",
                    data=rep.content_markdown,
                    file_name=f"{safe_name}_v{rep.version}.md",
                    mime="text/markdown",
                    key=f"dlbtn-md-{rep.id}",
                )
                try:
                    pdf_bytes = export_report_as_pdf_bytes(rep, ptitle)
                except Exception as e:
                    pdf_bytes = None
                    c3.error(f"PDF failed: {e}")
                if pdf_bytes is not None:
                    c3.download_button(
                        "Export PDF",
                        data=pdf_bytes,
                        file_name=f"{safe_name}_v{rep.version}.pdf",
                        mime="application/pdf",
                        key=f"dlbtn-pdf-{rep.id}",
                    )
    else:
        st.info("Generate a report draft above to get started.")


# --- Footer ---
st.divider()
st.caption("Resloom • Data stays in the local SQLite database")

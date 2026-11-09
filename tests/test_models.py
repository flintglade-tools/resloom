"""Model validation and behavior tests."""

from datetime import date, datetime, timedelta

import pytest
from pydantic import ValidationError

from resloom.models import (
    Project, ProjectStatus,
    ComplianceDeadline, DeadlineType, DeadlineStatus,
    Resource, ResourceType,
    Booking, BookingStatus,
    ProjectExpenditure,
)


def test_project_dates_valid():
    p = Project(
        title="Test Study",
        principal_investigator="Dr. X",
        start_date=date(2026, 1, 1),
        end_date=date(2027, 12, 31),
    )
    assert p.status == ProjectStatus.ACTIVE
    assert p.is_active()  # within range


def test_project_end_before_start_fails():
    with pytest.raises(ValidationError):
        Project(
            title="Bad",
            principal_investigator="Dr. Y",
            start_date=date(2025, 6, 1),
            end_date=date(2025, 1, 1),
        )


def test_deadline_overdue_logic():
    past = date.today() - timedelta(days=5)
    d = ComplianceDeadline(
        title="Old IRB",
        type=DeadlineType.IRB,
        due_date=past,
        status=DeadlineStatus.PENDING,
    )
    assert d.is_overdue() is True
    assert d.days_until_due < 0


def test_resource_and_booking_validation():
    r = Resource(name="Centrifuge", type=ResourceType.EQUIPMENT)
    b = Booking(
        resource_id=r.id,
        booked_by="Tech",
        start=datetime(2026, 8, 1, 10, 0),
        end=datetime(2026, 8, 1, 12, 0),
        purpose="Sample processing",
    )
    assert b.status == BookingStatus.CONFIRMED


def test_project_expenditure_and_links():
    p = Project(
        title="Budgeted Project",
        principal_investigator="Dr. Test",
        start_date=date(2026, 1, 1),
        budget_usd=100000.0,
        notes="Some notes here",
        links=["https://example.edu/grant", "file:///local/notes.pdf"],
    )
    assert p.links == ["https://example.edu/grant", "file:///local/notes.pdf"]
    exp = ProjectExpenditure(
        project_id=p.id,
        amount_usd=12500.50,
        date=date(2026, 2, 15),
        category="personnel",
        description="RA salary Q1",
    )
    assert exp.amount_usd == 12500.50


def test_booking_end_must_be_after_start():
    with pytest.raises(ValidationError):
        Booking(
            resource_id="r1",
            booked_by="X",
            start=datetime(2026, 8, 1, 14, 0),
            end=datetime(2026, 8, 1, 13, 0),
            purpose="Bad",
        )


def test_booking_blackout_and_recurrence():
    b = Booking(
        resource_id="r1",
        booked_by="Facilities",
        start=datetime(2026, 8, 1, 8, 0),
        end=datetime(2026, 8, 1, 17, 0),
        purpose="Maintenance",
        is_blackout=True,
        status=BookingStatus.CONFIRMED,
    )
    assert b.is_blackout is True
    assert b.status == BookingStatus.CONFIRMED

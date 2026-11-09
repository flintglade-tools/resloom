"""Core Pydantic data models for Resloom.

All models are local-first, serializable, and designed to support
future feature tiers (e.g. advanced reporting, multi-group) without
breaking changes.
"""

from datetime import date, datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


def _new_id() -> str:
    return str(uuid4())


class ProjectStatus(str, Enum):
    PLANNED = "planned"
    ACTIVE = "active"
    COMPLETED = "completed"
    ON_HOLD = "on_hold"


class DeadlineType(str, Enum):
    IRB = "irb"
    IACUC = "iacuc"
    SAFETY = "safety"
    BIOHAZARD = "biohazard"
    EXPORT_CONTROL = "export_control"
    ETHICS_REVIEW = "ethics_review"
    OTHER = "other"


class DeadlineStatus(str, Enum):
    PENDING = "pending"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    SUBMITTED = "submitted"
    OVERDUE = "overdue"


class ResourceType(str, Enum):
    EQUIPMENT = "equipment"
    ROOM = "room"
    FACILITY = "facility"
    INSTRUMENT = "instrument"


class BookingStatus(str, Enum):
    CONFIRMED = "confirmed"
    PENDING = "pending"
    CANCELLED = "cancelled"


class Project(BaseModel):
    """Core project/grant tracking entity.

    Future: add tags, collaborators (list), custom fields.
    """
    id: str = Field(default_factory=_new_id)
    title: str = Field(min_length=3, max_length=200)
    principal_investigator: str = Field(min_length=2, max_length=120)
    status: ProjectStatus = ProjectStatus.ACTIVE
    start_date: date
    end_date: Optional[date] = None
    sponsor: Optional[str] = None
    grant_number: Optional[str] = None
    budget_usd: Optional[float] = Field(default=None, ge=0)
    description: Optional[str] = Field(default=None, max_length=2000)
    notes: Optional[str] = Field(default=None, max_length=4000)
    links: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("end_date")
    @classmethod
    def end_after_start(cls, v: Optional[date], info):
        start = info.data.get("start_date")
        if v and start and v < start:
            raise ValueError("end_date must be on or after start_date")
        return v

    def is_active(self) -> bool:
        today = date.today()
        if self.status != ProjectStatus.ACTIVE:
            return False
        if self.end_date and self.end_date < today:
            return False
        return True


class ComplianceDeadline(BaseModel):
    """Compliance / regulatory deadline.

    Status and notes are edited in the app. Alerts are shown in the app; no email is sent.
    """
    id: str = Field(default_factory=_new_id)
    project_id: Optional[str] = None
    title: str = Field(min_length=3, max_length=200)
    type: DeadlineType
    due_date: date
    status: DeadlineStatus = DeadlineStatus.PENDING
    reminder_days_before: int = Field(default=14, ge=0, le=90)
    notes: Optional[str] = Field(default=None, max_length=2000)
    snoozed_until: Optional[date] = None
    last_updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def effective_due(self) -> date:
        if self.snoozed_until and self.snoozed_until > self.due_date:
            return self.snoozed_until
        return self.due_date

    @property
    def days_until_due(self) -> int:
        return (self.effective_due - date.today()).days

    def is_overdue(self) -> bool:
        if self.status in (DeadlineStatus.APPROVED, DeadlineStatus.SUBMITTED):
            return False
        return self.effective_due < date.today()


class Resource(BaseModel):
    """Shared equipment or room available for booking."""
    id: str = Field(default_factory=_new_id)
    name: str = Field(min_length=2, max_length=120)
    type: ResourceType
    location: Optional[str] = None
    capacity: Optional[int] = Field(default=None, ge=1)
    notes: Optional[str] = Field(default=None, max_length=1000)
    is_active: bool = True


class Booking(BaseModel):
    """Booking record for a resource.

    Conflict detection is performed at service layer.
    """
    id: str = Field(default_factory=_new_id)
    resource_id: str
    project_id: Optional[str] = None
    booked_by: str = Field(min_length=2)
    start: datetime
    end: datetime
    purpose: str = Field(min_length=3, max_length=500)
    status: BookingStatus = BookingStatus.CONFIRMED
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_blackout: bool = False
    recurrence_group_id: Optional[str] = None  # links series for recurring

    @field_validator("end")
    @classmethod
    def end_after_start(cls, v: datetime, info):
        start = info.data.get("start")
        if start and v <= start:
            raise ValueError("end must be after start")
        return v


class ReportDraft(BaseModel):
    """Human-editable report draft.

    Always starts as a simple template + user data. LLM assistance is
    opt-in and post-editable. Supports versioning for history.
    """
    id: str = Field(default_factory=_new_id)
    project_id: Optional[str] = None
    title: str
    report_type: str = "progress"  # progress, annual, compliance, resource, custom
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    content_markdown: str
    version: int = 1
    history: list[dict] = Field(default_factory=list)  # [{"version": int, "content": str, "edited_at": iso}]
    human_edited: bool = False
    last_edited_by: Optional[str] = None


class AppSettings(BaseModel):
    """Global settings for this Resloom instance."""
    org_name: str = "Research Group"
    default_reminder_days: int = 14


class ProjectExpenditure(BaseModel):
    """Simple expenditure record for budget vs actuals tracking.

    Linked to a project. Supports grant financial oversight.
    """
    id: str = Field(default_factory=_new_id)
    project_id: str
    amount_usd: float = Field(ge=0)
    date: date
    category: str = Field(default="other", max_length=50)
    description: Optional[str] = Field(default=None, max_length=500)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

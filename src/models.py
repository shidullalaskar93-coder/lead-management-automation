"""Data Models and Schema Definitions for Lead Management Automation.

Contains:
1. Enumerations for pipeline stages, tiers, sources, and signals.
2. Pydantic validation schemas for raw ingestion, cleaning, scoring, and routing.
3. SQLAlchemy ORM models for relational persistence in SQLite/PostgreSQL.
"""

from datetime import datetime, timezone
from enum import Enum
import json
import re
from typing import Any, Dict, List, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


# ============================================================================
# Domain Enumerations
# ============================================================================

class LeadStage(str, Enum):
    """Pipeline progression stages."""
    NEW = "NEW"
    VALIDATED = "VALIDATED"
    ASSIGNED = "ASSIGNED"
    CONTACTED = "CONTACTED"
    CONVERTED = "CONVERTED"
    DISQUALIFIED = "DISQUALIFIED"


class ScoreTier(str, Enum):
    """Lead qualification priority tiers."""
    HOT = "HOT"
    WARM = "WARM"
    COLD = "COLD"


class LeadSource(str, Enum):
    """Inbound lead acquisition channels."""
    WEBSITE = "WEBSITE"
    LINKEDIN = "LINKEDIN"
    REFERRAL = "REFERRAL"
    PARTNER = "PARTNER"
    INBOUND_CALL = "INBOUND_CALL"
    WEBINAR = "WEBINAR"
    EVENT = "EVENT"
    ORGANIC_SEARCH = "ORGANIC_SEARCH"
    PAID_ADS = "PAID_ADS"
    OTHER = "OTHER"


class IntentSignal(str, Enum):
    """Behavioral intent signals."""
    REQUEST_DEMO = "REQUEST_DEMO"
    CONTACT_SALES = "CONTACT_SALES"
    PRICING_PAGE = "PRICING_PAGE"
    TRIAL_SIGNUP = "TRIAL_SIGNUP"
    WHITEPAPER_DOWNLOAD = "WHITEPAPER_DOWNLOAD"
    WEBINAR_ATTENDEE = "WEBINAR_ATTENDEE"
    NEWSLETTER_SIGNUP = "NEWSLETTER_SIGNUP"
    GENERAL_INQUIRY = "GENERAL_INQUIRY"


class AssignmentRule(str, Enum):
    """Strategy used for SDR allocation."""
    ROUND_ROBIN = "ROUND_ROBIN"
    WEIGHTED = "WEIGHTED"
    TIER_BASED = "TIER_BASED"
    MANUAL = "MANUAL"


# ============================================================================
# Pydantic Schemas (Ingestion, Validation & DTOs)
# ============================================================================

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}$")
PHONE_DIGITS_REGEX = re.compile(r"[^\d+]")


class RawLeadInput(BaseModel):
    """Pydantic model for inbound lead ingestion with robust normalization.

    Supports webhooks, CSV rows, and REST API payloads with varying key formats.
    """
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    email: str = Field(..., description="Lead primary contact email address")
    first_name: str = Field(..., description="Contact first name")
    last_name: Optional[str] = Field(default="", description="Contact last name")
    company: str = Field(..., description="Lead company name")
    phone: Optional[str] = Field(default=None, description="Contact phone number")
    industry: Optional[str] = Field(default="Other", description="Business industry sector")
    company_size: Optional[str] = Field(default="1-10", description="Number of employees range")
    budget: Optional[float] = Field(default=0.0, description="Estimated annual budget in USD")
    source: str = Field(default="WEBSITE", description="Lead origination source")
    intent_signal: str = Field(default="GENERAL_INQUIRY", description="Primary behavioral signal")
    custom_metadata: Dict[str, Any] = Field(default_factory=dict, description="Flexible metadata")

    @model_validator(mode="before")
    @classmethod
    def standardize_input_keys(cls, data: Any) -> Any:
        """Map common alias variations and clean raw key-value pairs."""
        if not isinstance(data, dict):
            return data

        # Canonical key map
        alias_map = {
            "first_name": ["firstname", "first name", "fname", "given_name"],
            "last_name": ["lastname", "last name", "lname", "surname", "family_name"],
            "company": ["company_name", "companyname", "organization", "account_name"],
            "email": ["email_address", "work_email", "mail"],
            "phone": ["phone_number", "telephone", "mobile", "contact_number"],
            "company_size": ["size", "employees", "employee_count", "team_size"],
            "intent_signal": ["intent", "signal", "action", "event_type"],
            "budget": ["annual_budget", "estimated_budget", "revenue"],
        }

        normalized = {}
        for k, v in data.items():
            k_clean = str(k).strip().lower().replace("-", "_")
            # Map aliases
            mapped_key = k_clean
            for canonical, aliases in alias_map.items():
                if k_clean == canonical or k_clean in aliases:
                    mapped_key = canonical
                    break
            normalized[mapped_key] = v

        return normalized

    @field_validator("email", mode="before")
    @classmethod
    def validate_and_clean_email(cls, v: Any) -> str:
        """Ensure email format validity and standardize to lowercase."""
        if not v or not isinstance(v, str):
            raise ValueError("Email is mandatory and must be a non-empty string.")
        cleaned = v.strip().lower()
        if not EMAIL_REGEX.match(cleaned):
            raise ValueError(f"Invalid email address format: '{v}'")
        return cleaned

    @field_validator("first_name", "company", mode="before")
    @classmethod
    def validate_mandatory_strings(cls, v: Any) -> str:
        """Ensure mandatory string fields are non-empty after trimming."""
        if not v or not isinstance(v, str) or not v.strip():
            raise ValueError("Field is mandatory and cannot be empty or whitespace.")
        return v.strip().title()

    @field_validator("last_name", mode="before")
    @classmethod
    def clean_last_name(cls, v: Any) -> str:
        """Trim whitespace and standardize casing on optional last name."""
        if not v or not isinstance(v, str):
            return ""
        return v.strip().title()

    @field_validator("phone", mode="before")
    @classmethod
    def normalize_phone_number(cls, v: Any) -> Optional[str]:
        """Normalize phone numbers according to E.164 guidelines."""
        if not v:
            return None
        v_str = str(v).strip()
        if not v_str:
            return None

        has_plus = v_str.startswith("+")
        # Strip all formatting characters: spaces, parentheses, dashes, dots
        digits_only = re.sub(r"\D", "", v_str)

        if len(digits_only) < 7:
            raise ValueError(f"Phone number '{v}' is too short (min 7 digits).")
        if len(digits_only) > 15:
            raise ValueError(f"Phone number '{v}' exceeds max 15 digits (ITU-T E.164).")

        return f"+{digits_only}" if has_plus else f"+1{digits_only}" if len(digits_only) == 10 else f"+{digits_only}"

    @field_validator("budget", mode="before")
    @classmethod
    def clean_budget(cls, v: Any) -> float:
        """Convert string currency like '$120,000' or float to standard float."""
        if v is None:
            return 0.0
        if isinstance(v, (int, float)):
            return float(v)
        v_str = str(v).replace("$", "").replace(",", "").replace(" ", "").strip()
        try:
            return float(v_str) if v_str else 0.0
        except ValueError:
            return 0.0

    @field_validator("source", mode="before")
    @classmethod
    def clean_source(cls, v: Any) -> str:
        if not v:
            return LeadSource.WEBSITE.value
        clean = str(v).strip().upper().replace(" ", "_")
        try:
            return LeadSource(clean).value
        except ValueError:
            return LeadSource.OTHER.value

    @field_validator("intent_signal", mode="before")
    @classmethod
    def clean_intent_signal(cls, v: Any) -> str:
        if not v:
            return IntentSignal.GENERAL_INQUIRY.value
        clean = str(v).strip().upper().replace(" ", "_").replace("-", "_")
        # Handle common mappings
        mapping = {
            "DEMO": IntentSignal.REQUEST_DEMO.value,
            "DEMO_REQUEST": IntentSignal.REQUEST_DEMO.value,
            "CONTACT": IntentSignal.CONTACT_SALES.value,
            "PRICING": IntentSignal.PRICING_PAGE.value,
            "WHITEPAPER": IntentSignal.WHITEPAPER_DOWNLOAD.value,
            "SIGNUP": IntentSignal.TRIAL_SIGNUP.value,
            "TRIAL": IntentSignal.TRIAL_SIGNUP.value,
        }
        if clean in mapping:
            return mapping[clean]
        try:
            return IntentSignal(clean).value
        except ValueError:
            return IntentSignal.GENERAL_INQUIRY.value


class ScoredLead(RawLeadInput):
    """Lead populated with qualification scoring output."""
    lead_uuid: str = Field(default_factory=lambda: str(uuid.uuid4()))
    score: int = Field(default=0, ge=0, le=100)
    score_tier: ScoreTier = Field(default=ScoreTier.COLD)
    score_breakdown: Dict[str, int] = Field(default_factory=dict)
    status: LeadStage = Field(default=LeadStage.VALIDATED)


class AssignedLead(ScoredLead):
    """Lead populated with SDR assignment details and SLA schedule."""
    sdr_id: str
    sdr_name: str
    sdr_email: str
    assignment_rule: AssignmentRule
    assigned_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    sla_deadline: datetime
    sla_action: str
    status: LeadStage = Field(default=LeadStage.ASSIGNED)


# ============================================================================
# SQLAlchemy ORM Database Models
# ============================================================================

class LeadModel(Base):
    """Persistent storage for qualified and pipeline leads."""
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, autoincrement=True)
    lead_uuid = Column(String(36), unique=True, nullable=False, index=True)
    email = Column(String(255), nullable=False, index=True)
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=True)
    company = Column(String(255), nullable=False, index=True)
    phone = Column(String(30), nullable=True)
    industry = Column(String(100), nullable=True)
    company_size = Column(String(50), nullable=True)
    budget = Column(Float, default=0.0)
    source = Column(String(50), default="WEBSITE")
    intent_signal = Column(String(50), default="GENERAL_INQUIRY")
    score = Column(Integer, default=0)
    score_tier = Column(String(20), default="COLD")
    status = Column(String(30), default="NEW", index=True)
    custom_metadata = Column(Text, default="{}")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    assignments = relationship("AssignmentModel", back_populates="lead", cascade="all, delete-orphan")
    audit_logs = relationship("AuditLogModel", back_populates="lead", cascade="all, delete-orphan")

    def to_dict(self) -> Dict[str, Any]:
        """Serialize LeadModel to Python dictionary."""
        return {
            "id": self.id,
            "lead_uuid": self.lead_uuid,
            "email": self.email,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "company": self.company,
            "phone": self.phone,
            "industry": self.industry,
            "company_size": self.company_size,
            "budget": self.budget,
            "source": self.source,
            "intent_signal": self.intent_signal,
            "score": self.score,
            "score_tier": self.score_tier,
            "status": self.status,
            "custom_metadata": json.loads(self.custom_metadata or "{}"),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class AssignmentModel(Base):
    """Record of SDR assignment and SLA obligation."""
    __tablename__ = "assignments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    lead_id = Column(Integer, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True)
    sdr_id = Column(String(50), nullable=False, index=True)
    sdr_name = Column(String(100), nullable=False)
    sdr_email = Column(String(255), nullable=False)
    assigned_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    assignment_rule = Column(String(50), default="ROUND_ROBIN")
    sla_deadline = Column(DateTime, nullable=False)
    sla_action = Column(String(255), nullable=False)
    status = Column(String(50), default="PENDING")

    # Relationship
    lead = relationship("LeadModel", back_populates="assignments")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "lead_id": self.lead_id,
            "sdr_id": self.sdr_id,
            "sdr_name": self.sdr_name,
            "sdr_email": self.sdr_email,
            "assigned_at": self.assigned_at.isoformat() if self.assigned_at else None,
            "assignment_rule": self.assignment_rule,
            "sla_deadline": self.sla_deadline.isoformat() if self.sla_deadline else None,
            "sla_action": self.sla_action,
            "status": self.status,
        }


class AuditLogModel(Base):
    """Full lifecycle audit log of state transitions and events."""
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    lead_id = Column(Integer, ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True)
    action = Column(String(100), nullable=False)
    stage_from = Column(String(50), nullable=True)
    stage_to = Column(String(50), nullable=True)
    actor = Column(String(100), default="SYSTEM")
    details = Column(Text, default="{}")
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)

    # Relationship
    lead = relationship("LeadModel", back_populates="audit_logs")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "lead_id": self.lead_id,
            "action": self.action,
            "stage_from": self.stage_from,
            "stage_to": self.stage_to,
            "actor": self.actor,
            "details": json.loads(self.details or "{}"),
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


class DeadLetterQueueModel(Base):
    """Persistent storage for unprocessable, invalid, or failed leads."""
    __tablename__ = "dead_letter_queue"

    id = Column(Integer, primary_key=True, autoincrement=True)
    payload = Column(Text, nullable=False)
    error_type = Column(String(100), nullable=False)
    error_message = Column(Text, nullable=False)
    stack_trace = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0)
    status = Column(String(30), default="OPEN", index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    resolved_at = Column(DateTime, nullable=True)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "payload": self.payload,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "retry_count": self.retry_count,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
        }


# Helpful composite indexes for high-throughput pipeline queries
Index("idx_lead_email_company", LeadModel.email, LeadModel.company)
Index("idx_lead_tier_status", LeadModel.score_tier, LeadModel.status)
Index("idx_assignment_sdr_status", AssignmentModel.sdr_id, AssignmentModel.status)

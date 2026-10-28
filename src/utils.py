"""Utility Helpers for Lead Management Automation.

Includes:
- Database connection lifecycle management.
- Deduplication detection within time windows.
- Inbound payload parsing (CSV, JSON, Webhook dictionaries).
"""

import csv
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

try:
    from config.settings import Settings, get_settings
except ImportError:
    from ..config.settings import Settings, get_settings

from .models import Base, LeadModel

logger = logging.getLogger("lead_pipeline.utils")


def get_db_engine(db_url: Optional[str] = None):
    """Create SQLAlchemy engine with appropriate pooling and thread flags."""
    settings = get_settings()
    url = db_url or settings.database_url

    connect_args = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    return create_engine(url, connect_args=connect_args, echo=False)


def init_db(engine) -> None:
    """Initialize database tables according to declared ORM metadata."""
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables initialized successfully.")


def get_db_session_factory(engine) -> sessionmaker:
    """Create thread-safe sessionmaker factory."""
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


def check_duplicate_lead(
    session: Session,
    email: str,
    company: str,
    phone: Optional[str] = None,
    window_days: Optional[int] = None,
) -> Tuple[bool, Optional[LeadModel]]:
    """Inspect database for active duplicates within configured timeframe.

    Checks:
    1. Exact match on normalized email.
    2. Exact match on company AND phone (if phone provided).

    Args:
        session: Active SQLAlchemy session
        email: Normalized email string
        company: Normalized company string
        phone: Optional normalized phone string
        window_days: Lookback window in days (defaults to settings)

    Returns:
        Tuple of (is_duplicate: bool, existing_record: Optional[LeadModel])
    """
    settings = get_settings()
    days = window_days if window_days is not None else settings.deduplication_window_days
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # 1. Email-based check
    stmt_email = (
        select(LeadModel)
        .where(LeadModel.email == email)
        .where(LeadModel.created_at >= cutoff)
        .order_by(LeadModel.created_at.desc())
    )
    existing_by_email = session.execute(stmt_email).scalars().first()
    if existing_by_email:
        return True, existing_by_email

    # 2. Company + Phone check
    if phone:
        stmt_phone = (
            select(LeadModel)
            .where(LeadModel.company == company)
            .where(LeadModel.phone == phone)
            .where(LeadModel.created_at >= cutoff)
            .order_by(LeadModel.created_at.desc())
        )
        existing_by_phone = session.execute(stmt_phone).scalars().first()
        if existing_by_phone:
            return True, existing_by_phone

    return False, None


def load_leads_from_csv(file_path: Union[str, Path]) -> List[Dict[str, Any]]:
    """Parse inbound leads from a CSV file into list of normalized dictionaries."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"CSV file not found at path: {path}")

    leads = []
    with open(path, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cleaned_row = {k.strip(): v.strip() for k, v in row.items() if k}
            if cleaned_row:
                leads.append(cleaned_row)
    return leads


def load_leads_from_json(file_path: Union[str, Path]) -> List[Dict[str, Any]]:
    """Parse inbound leads from a JSON or JSONL file."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"JSON file not found at path: {path}")

    with open(path, mode="r", encoding="utf-8") as f:
        content = f.read().strip()
        if not content:
            return []
        if content.startswith("["):
            return json.loads(content)
        else:
            # Assume JSONL format
            leads = []
            for line in content.splitlines():
                if line.strip():
                    leads.append(json.loads(line))
            return leads

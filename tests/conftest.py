"""Pytest Fixtures and Test Environment Configuration."""

from pathlib import Path
import sys
from typing import Any, Dict

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import SDRConfig, ScoringCriteria, Settings
from src.crm_client import MockCRMClient
from src.errors import DeadLetterQueueHandler
from src.models import Base
from src.pipeline import LeadAutomationPipeline
from src.routing import SDRRouter
from src.scheduler import FollowUpScheduler
from src.scoring import LeadScoringEngine


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    """Provide isolated Settings instance with temporary database and DLQ paths."""
    db_file = tmp_path / "test_pipeline.db"
    dlq_file = tmp_path / "test_failed_leads.jsonl"

    sdrs = [
        SDRConfig(id="sdr_hot_1", name="Senior Hot SDR 1", email="hot1@test.io", tier="HOT", weight=2, active=True),
        SDRConfig(id="sdr_hot_2", name="Senior Hot SDR 2", email="hot2@test.io", tier="HOT", weight=1, active=True),
        SDRConfig(id="sdr_warm_1", name="Mid Warm SDR 1", email="warm1@test.io", tier="WARM", weight=1, active=True),
        SDRConfig(id="sdr_cold_1", name="Inbound SDR 1", email="cold1@test.io", tier="COLD", weight=1, active=True),
    ]

    return Settings(
        app_env="test",
        debug=True,
        database_url=f"sqlite:///{db_file}",
        dlq_storage_file=dlq_file,
        score_hot_threshold=75,
        score_warm_threshold=45,
        hot_sla_minutes=15,
        warm_sla_hours=4,
        cold_sla_days=1,
        deduplication_window_days=30,
        crm_sync_max_retries=3,
        crm_sync_base_delay=0.01,  # Fast retries for testing
        crm_sync_max_delay=0.05,
        crm_simulate_failure_rate=0.0,
        sdrs=sdrs,
        scoring=ScoringCriteria(),
    )


@pytest.fixture
def memory_db_engine():
    """Provide clean in-memory SQLite database engine."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session(memory_db_engine):
    """Provide clean SQLAlchemy database session."""
    session_factory = sessionmaker(bind=memory_db_engine)
    session = session_factory()
    yield session
    session.close()


@pytest.fixture
def mock_crm_client(test_settings: Settings) -> MockCRMClient:
    """Mock CRM client with zero delay for instant test execution."""
    return MockCRMClient(settings=test_settings)


@pytest.fixture
def sdr_router(test_settings: Settings) -> SDRRouter:
    """Clean SDRRouter instance with test roster."""
    router = SDRRouter(sdr_roster=test_settings.sdrs)
    router.reset_state()
    return router


@pytest.fixture
def test_pipeline(test_settings: Settings, memory_db_engine, mock_crm_client, sdr_router) -> LeadAutomationPipeline:
    """Ready-to-use LeadAutomationPipeline backed by in-memory DB and test configuration."""
    return LeadAutomationPipeline(
        settings=test_settings,
        db_engine=memory_db_engine,
        crm_client=mock_crm_client,
        sdr_router=sdr_router,
    )


@pytest.fixture
def valid_hot_lead_payload() -> Dict[str, Any]:
    return {
        "first_name": "Tony",
        "last_name": "Stark",
        "email": "tony.stark@starkindustries.com",
        "company": "Stark Industries",
        "phone": "+1 (555) 300-4000",
        "industry": "Enterprise Software",
        "company_size": "1000+",
        "budget": 250000,
        "source": "WEBSITE",
        "intent_signal": "REQUEST_DEMO",
    }


@pytest.fixture
def valid_warm_lead_payload() -> Dict[str, Any]:
    return {
        "first_name": "Bruce",
        "last_name": "Banner",
        "email": "bruce.banner@gamma-labs.org",
        "company": "Gamma Labs",
        "phone": "555-400-5000",
        "industry": "Healthcare",
        "company_size": "201-500",
        "budget": 45000,
        "source": "LINKEDIN",
        "intent_signal": "PRICING_PAGE",
    }


@pytest.fixture
def valid_cold_lead_payload() -> Dict[str, Any]:
    return {
        "first_name": "Peter",
        "last_name": "Parker",
        "email": "peter.parker@freelance-photo.com",
        "company": "Parker Photography",
        "phone": "555-123-4567",
        "industry": "Other",
        "company_size": "1-10",
        "budget": 1000,
        "source": "ORGANIC_SEARCH",
        "intent_signal": "NEWSLETTER_SIGNUP",
    }

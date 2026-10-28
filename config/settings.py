"""Enterprise Configuration Management for Lead Management Automation.

Supports dynamic configuration via environment variables, .env files,
and programmatic overrides with typed validation and default fallback values.
"""

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv

# Base Directory Resolution
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


@dataclass
class SDRConfig:
    """Configuration for an active Sales Development Representative (SDR)."""
    id: str
    name: str
    email: str
    tier: str  # "HOT", "WARM", "COLD", "ALL"
    weight: int = 1  # For weighted routing
    active: bool = True


@dataclass
class ScoringCriteria:
    """Weight and scoring points definition for lead qualification."""
    # Industry Score (Max 25 pts)
    industry_scores: Dict[str, int] = field(
        default_factory=lambda: {
            "enterprise software": 25,
            "saas": 25,
            "fintech": 25,
            "financial services": 20,
            "healthcare": 20,
            "cybersecurity": 25,
            "ecommerce": 15,
            "manufacturing": 15,
            "consulting": 10,
            "retail": 10,
            "education": 5,
            "other": 5,
        }
    )

    # Company Size Score (Max 25 pts)
    size_scores: Dict[str, int] = field(
        default_factory=lambda: {
            "1000+": 25,
            "501-1000": 22,
            "201-500": 18,
            "51-200": 14,
            "11-50": 8,
            "1-10": 4,
        }
    )

    # Intent Signals Score (Max 30 pts)
    intent_scores: Dict[str, int] = field(
        default_factory=lambda: {
            "request_demo": 30,
            "demo_request": 30,
            "contact_sales": 28,
            "pricing_page": 20,
            "trial_signup": 22,
            "whitepaper_download": 12,
            "webinar_attendee": 15,
            "newsletter_signup": 6,
            "general_inquiry": 8,
        }
    )

    # Budget Tier Score (Max 20 pts)
    # Evaluated as minimum threshold (points awarded for reaching tier)
    budget_tiers: List[Dict[str, Any]] = field(
        default_factory=lambda: [
            {"min": 100000.0, "score": 20},
            {"min": 50000.0, "score": 15},
            {"min": 25000.0, "score": 10},
            {"min": 10000.0, "score": 6},
            {"min": 0.0, "score": 2},
        ]
    )


@dataclass
class Settings:
    """Master application configuration container."""

    # Environment & Diagnostics
    app_env: str = os.getenv("APP_ENV", "development")
    debug: bool = os.getenv("DEBUG", "true").lower() in ("true", "1", "yes")

    # Project Directories
    root_dir: Path = PROJECT_ROOT
    data_dir: Path = PROJECT_ROOT / "data"

    # Database
    database_url: str = os.getenv(
        "DATABASE_URL",
        f"sqlite:///{PROJECT_ROOT / 'data' / 'pipeline.db'}"
    )

    # Dead Letter Queue
    dlq_storage_file: Path = PROJECT_ROOT / os.getenv("DLQ_STORAGE_FILE", "data/failed_leads.jsonl")
    dlq_max_retries: int = int(os.getenv("DLQ_MAX_RETRIES", "3"))

    # Qualification & Routing Tiers
    score_hot_threshold: int = int(os.getenv("SCORE_HOT_THRESHOLD", "75"))
    score_warm_threshold: int = int(os.getenv("SCORE_WARM_THRESHOLD", "45"))

    # Follow-Up SLA Settings
    hot_sla_minutes: int = int(os.getenv("HOT_SLA_MINUTES", "15"))
    warm_sla_hours: int = int(os.getenv("WARM_SLA_HOURS", "4"))
    cold_sla_days: int = int(os.getenv("COLD_SLA_DAYS", "1"))

    # Deduplication Rules
    deduplication_window_days: int = int(os.getenv("DEDUPLICATION_WINDOW_DAYS", "30"))

    # External CRM Simulation / Sync
    crm_sync_max_retries: int = int(os.getenv("CRM_SYNC_MAX_RETRIES", "3"))
    crm_sync_base_delay: float = float(os.getenv("CRM_SYNC_BASE_DELAY_SECONDS", "0.2"))
    crm_sync_max_delay: float = float(os.getenv("CRM_SYNC_MAX_DELAY_SECONDS", "2.0"))
    crm_simulate_failure_rate: float = float(os.getenv("CRM_SIMULATE_FAILURE_RATE", "0.0"))

    # Scoring Matrix
    scoring: ScoringCriteria = field(default_factory=ScoringCriteria)

    # Standard SDR Team Roster
    sdrs: List[SDRConfig] = field(
        default_factory=lambda: [
            SDRConfig(
                id="sdr_01",
                name="Sarah Connor",
                email="sarah.connor@enterprise.io",
                tier="HOT",
                weight=3,
                active=True,
            ),
            SDRConfig(
                id="sdr_02",
                name="Marcus Wright",
                email="marcus.wright@enterprise.io",
                tier="HOT",
                weight=2,
                active=True,
            ),
            SDRConfig(
                id="sdr_03",
                name="Elena Fisher",
                email="elena.fisher@enterprise.io",
                tier="WARM",
                weight=2,
                active=True,
            ),
            SDRConfig(
                id="sdr_04",
                name="Nathan Drake",
                email="nathan.drake@enterprise.io",
                tier="WARM",
                weight=1,
                active=True,
            ),
            SDRConfig(
                id="sdr_05",
                name="Chloe Frazer",
                email="chloe.frazer@enterprise.io",
                tier="COLD",
                weight=1,
                active=True,
            ),
        ]
    )

    def __post_init__(self) -> None:
        """Ensure necessary local directories exist."""
        self.data_dir.mkdir(parents=True, exist_ok=True)


def get_settings(**overrides: Any) -> Settings:
    """Factory function to acquire typed Settings instance with optional overrides."""
    return Settings(**overrides)

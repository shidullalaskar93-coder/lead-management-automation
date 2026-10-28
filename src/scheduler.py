"""Task and Follow-up SLA Scheduler.

Calculates strict SLA deadlines and generates actionable sales tasks
based on lead tier:
- HOT: 15-minute SLA -> Immediate Phone Call & Slack Escalation.
- WARM: 4-hour SLA -> Tailored Email & Account Research.
- COLD: 24-hour SLA -> Automated Drip Sequence Flag.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

try:
    from config.settings import Settings, get_settings
except ImportError:
    from ..config.settings import Settings, get_settings

from .models import ScoreTier, ScoredLead


@dataclass
class FollowUpSchedule:
    """Actionable follow-up SLA task container."""
    sla_deadline: datetime
    sla_action: str
    priority: str
    is_automated: bool
    escalation_channel: str


class FollowUpScheduler:
    """Enterprise SLA scheduler calculating exact deadlines and action blueprints."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()

    def schedule(self, lead: ScoredLead, base_time: Optional[datetime] = None) -> FollowUpSchedule:
        """Calculate SLA deadline and task directive for the scored lead.

        Args:
            lead: ScoredLead instance
            base_time: Base UTC timestamp (defaults to now)

        Returns:
            FollowUpSchedule with exact deadline and priority details
        """
        now = base_time or datetime.now(timezone.utc)

        if lead.score_tier == ScoreTier.HOT:
            deadline = now + timedelta(minutes=self.settings.hot_sla_minutes)
            action = (
                f"URGENT: 15-min SLA Call & Slack ping to SDR. High-intent inquiry "
                f"from {lead.company} (Score: {lead.score})."
            )
            return FollowUpSchedule(
                sla_deadline=deadline,
                sla_action=action,
                priority="CRITICAL",
                is_automated=False,
                escalation_channel="#urgent-inbound-leads",
            )

        elif lead.score_tier == ScoreTier.WARM:
            deadline = now + timedelta(hours=self.settings.warm_sla_hours)
            action = (
                f"PRIORITY: 4-hour SLA Outreach. Send personalized email and research "
                f"{lead.company} (Score: {lead.score})."
            )
            return FollowUpSchedule(
                sla_deadline=deadline,
                sla_action=action,
                priority="HIGH",
                is_automated=False,
                escalation_channel="#sales-inbound-warm",
            )

        else:  # ScoreTier.COLD
            deadline = now + timedelta(days=self.settings.cold_sla_days)
            action = (
                f"AUTOMATED: Enroll contact into Marketing Nurture Drip Sequence Stage 1 "
                f"(Score: {lead.score})."
            )
            return FollowUpSchedule(
                sla_deadline=deadline,
                sla_action=action,
                priority="NORMAL",
                is_automated=True,
                escalation_channel="marketing-automation-bus",
            )

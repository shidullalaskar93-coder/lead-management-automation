"""Lead Management Automation Core Package.

Enterprise-grade pipeline capturing, qualifying, routing, and scheduling inbound leads.
"""

from .crm_client import MockCRMClient
from .errors import (
    DatabaseLockError,
    DeadLetterQueueHandler,
    DuplicateLeadError,
    EnrichmentTimeoutError,
    LeadAutomationError,
    LeadValidationError,
    PipelineProcessingError,
    RoutingError,
)
from .models import (
    AssignmentModel,
    AssignmentRule,
    AuditLogModel,
    DeadLetterQueueModel,
    IntentSignal,
    LeadModel,
    LeadSource,
    LeadStage,
    RawLeadInput,
    ScoreTier,
    ScoredLead,
)
from .pipeline import BatchPipelineSummary, LeadAutomationPipeline, PipelineResult
from .routing import SDRRouter
from .scheduler import FollowUpSchedule, FollowUpScheduler
from .scoring import LeadScoringEngine

__all__ = [
    "LeadAutomationPipeline",
    "PipelineResult",
    "BatchPipelineSummary",
    "RawLeadInput",
    "ScoredLead",
    "LeadModel",
    "AssignmentModel",
    "AuditLogModel",
    "DeadLetterQueueModel",
    "LeadStage",
    "ScoreTier",
    "LeadSource",
    "IntentSignal",
    "AssignmentRule",
    "LeadScoringEngine",
    "SDRRouter",
    "FollowUpScheduler",
    "FollowUpSchedule",
    "DeadLetterQueueHandler",
    "MockCRMClient",
    "LeadAutomationError",
    "LeadValidationError",
    "DuplicateLeadError",
    "EnrichmentTimeoutError",
    "DatabaseLockError",
    "RoutingError",
    "PipelineProcessingError",
]

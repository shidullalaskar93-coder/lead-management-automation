"""Main Orchestration Pipeline for Lead Management Automation.

Orchestrates the entire lifecycle:
Capture -> Validate & Clean -> Deduplicate -> Score -> Route -> Schedule -> Store -> CRM Sync.
Provides robust fallback to Dead-Letter Queue (DLQ) on any processing error.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
from typing import Any, Dict, List, Optional

from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

try:
    from config.settings import Settings, get_settings
except ImportError:
    from ..config.settings import Settings, get_settings

from .crm_client import MockCRMClient
from .errors import (
    DeadLetterQueueHandler,
    DuplicateLeadError,
    LeadAutomationError,
    LeadValidationError,
    PipelineProcessingError,
)
from .models import (
    AssignmentModel,
    AssignmentRule,
    AuditLogModel,
    LeadModel,
    LeadStage,
    RawLeadInput,
    ScoreTier,
    ScoredLead,
)
from .routing import SDRRouter
from .scheduler import FollowUpScheduler
from .scoring import LeadScoringEngine
from .utils import (
    check_duplicate_lead,
    get_db_engine,
    get_db_session_factory,
    init_db,
)

logger = logging.getLogger("lead_pipeline.core")


@dataclass
class PipelineResult:
    """Outcome report for an ingested lead."""
    status: str  # "SUCCESS", "DUPLICATE", "VALIDATION_ERROR", "PROCESSING_ERROR"
    success: bool
    lead_id: Optional[int] = None
    lead_uuid: Optional[str] = None
    email: Optional[str] = None
    company: Optional[str] = None
    score: int = 0
    score_tier: Optional[str] = None
    assigned_sdr: Optional[str] = None
    sdr_email: Optional[str] = None
    sla_deadline: Optional[str] = None
    sla_action: Optional[str] = None
    crm_external_id: Optional[str] = None
    error_message: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BatchPipelineSummary:
    """Consolidated summary for batch execution."""
    total_ingested: int = 0
    total_success: int = 0
    total_duplicates: int = 0
    total_failed: int = 0
    results: List[PipelineResult] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        return (self.total_success / self.total_ingested * 100.0) if self.total_ingested > 0 else 0.0


class LeadAutomationPipeline:
    """Enterprise lead processing orchestrator."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        db_engine=None,
        crm_client: Optional[MockCRMClient] = None,
        sdr_router: Optional[SDRRouter] = None,
    ):
        self.settings = settings or get_settings()
        self.engine = db_engine or get_db_engine(self.settings.database_url)
        init_db(self.engine)
        self.session_factory: sessionmaker = get_db_session_factory(self.engine)

        self.scoring_engine = LeadScoringEngine(self.settings.scoring)
        self.router = sdr_router or SDRRouter()
        self.scheduler = FollowUpScheduler(self.settings)
        self.crm_client = crm_client or MockCRMClient(self.settings)
        self.dlq = DeadLetterQueueHandler(self.settings.dlq_storage_file)

    def process_lead(
        self,
        raw_payload: Dict[str, Any],
        assignment_rule: AssignmentRule = AssignmentRule.TIER_BASED,
        sync_crm: bool = True,
        strict_dedup: bool = True,
    ) -> PipelineResult:
        """Process a single inbound lead through the automated pipeline.

        Args:
            raw_payload: Inbound dictionary (from webhook or parsed CSV)
            assignment_rule: SDR routing strategy
            sync_crm: Whether to push to mock CRM with retries
            strict_dedup: If True, rejects duplicates into DLQ / error status

        Returns:
            PipelineResult with processing details
        """
        session: Session = self.session_factory()
        now = datetime.now(timezone.utc)

        try:
            # ---------------------------------------------------------
            # 1. Validation & Data Cleaning (Pydantic)
            # ---------------------------------------------------------
            try:
                validated_input = RawLeadInput(**raw_payload)
            except (ValidationError, ValueError) as val_err:
                err_msg = f"Validation failed: {val_err}"
                logger.error("Lead validation error for payload %s: %s", raw_payload, err_msg)
                self.dlq.log_failure(
                    payload=raw_payload,
                    error=LeadValidationError(err_msg),
                    db_session=session,
                    context={"stage": "VALIDATION"},
                )
                return PipelineResult(
                    status="VALIDATION_ERROR",
                    success=False,
                    email=raw_payload.get("email"),
                    company=raw_payload.get("company"),
                    error_message=err_msg,
                )

            # ---------------------------------------------------------
            # 2. Deduplication Detection
            # ---------------------------------------------------------
            is_dup, existing_lead = check_duplicate_lead(
                session=session,
                email=validated_input.email,
                company=validated_input.company,
                phone=validated_input.phone,
                window_days=self.settings.deduplication_window_days,
            )

            if is_dup and existing_lead is not None:
                dup_msg = (
                    f"Duplicate lead detected: email '{validated_input.email}' / "
                    f"company '{validated_input.company}' matches existing lead #{existing_lead.id}"
                )
                logger.warning(dup_msg)

                # Record deduplication audit log entry
                audit_entry = AuditLogModel(
                    lead_id=existing_lead.id,
                    action="DUPLICATE_ATTEMPT_IGNORED",
                    stage_from=existing_lead.status,
                    stage_to=existing_lead.status,
                    actor="PIPELINE_DEDUP",
                    details=json.dumps({"attempted_payload": raw_payload}),
                    timestamp=now,
                )
                session.add(audit_entry)
                session.commit()

                if strict_dedup:
                    self.dlq.log_failure(
                        payload=raw_payload,
                        error=DuplicateLeadError(dup_msg),
                        db_session=session,
                        context={"existing_lead_id": existing_lead.id},
                    )
                    return PipelineResult(
                        status="DUPLICATE",
                        success=False,
                        lead_id=existing_lead.id,
                        email=validated_input.email,
                        company=validated_input.company,
                        error_message=dup_msg,
                    )

            # ---------------------------------------------------------
            # 3. Dynamic Lead Scoring
            # ---------------------------------------------------------
            scored_lead: ScoredLead = self.scoring_engine.score_lead(validated_input)

            # ---------------------------------------------------------
            # 4. Intelligent SDR Routing
            # ---------------------------------------------------------
            assigned_sdr = self.router.route_lead(scored_lead, rule=assignment_rule)

            # ---------------------------------------------------------
            # 5. Task & SLA Scheduling
            # ---------------------------------------------------------
            schedule = self.scheduler.schedule(scored_lead, base_time=now)

            # ---------------------------------------------------------
            # 6. Database Persistence & Audit Logging
            # ---------------------------------------------------------
            lead_record = LeadModel(
                lead_uuid=scored_lead.lead_uuid,
                email=scored_lead.email,
                first_name=scored_lead.first_name,
                last_name=scored_lead.last_name,
                company=scored_lead.company,
                phone=scored_lead.phone,
                industry=scored_lead.industry,
                company_size=scored_lead.company_size,
                budget=scored_lead.budget,
                source=scored_lead.source,
                intent_signal=scored_lead.intent_signal,
                score=scored_lead.score,
                score_tier=scored_lead.score_tier.value,
                status=LeadStage.ASSIGNED.value,
                custom_metadata=json.dumps(scored_lead.custom_metadata or {}),
                created_at=now,
                updated_at=now,
            )
            session.add(lead_record)
            session.flush()  # Populate lead_record.id

            # Create Assignment Record
            assignment_record = AssignmentModel(
                lead_id=lead_record.id,
                sdr_id=assigned_sdr.id,
                sdr_name=assigned_sdr.name,
                sdr_email=assigned_sdr.email,
                assigned_at=now,
                assignment_rule=assignment_rule.value,
                sla_deadline=schedule.sla_deadline,
                sla_action=schedule.sla_action,
                status="PENDING",
            )
            session.add(assignment_record)

            # Create Lifecycle Audit Logs
            audit_creation = AuditLogModel(
                lead_id=lead_record.id,
                action="LEAD_INGESTED_AND_VALIDATED",
                stage_from=LeadStage.NEW.value,
                stage_to=LeadStage.VALIDATED.value,
                actor="SYSTEM_PIPELINE",
                details=json.dumps({"score": scored_lead.score, "tier": scored_lead.score_tier.value}),
                timestamp=now,
            )
            audit_assignment = AuditLogModel(
                lead_id=lead_record.id,
                action="LEAD_ASSIGNED_TO_SDR",
                stage_from=LeadStage.VALIDATED.value,
                stage_to=LeadStage.ASSIGNED.value,
                actor="SDR_ROUTER",
                details=json.dumps({
                    "sdr_id": assigned_sdr.id,
                    "sdr_name": assigned_sdr.name,
                    "sla_deadline": schedule.sla_deadline.isoformat(),
                    "priority": schedule.priority,
                }),
                timestamp=now,
            )
            session.add(audit_creation)
            session.add(audit_assignment)
            session.commit()

            # ---------------------------------------------------------
            # 7. Upstream CRM Synchronization (with Retries)
            # ---------------------------------------------------------
            crm_ext_id = None
            if sync_crm:
                from .models import AssignedLead

                assigned_data = scored_lead.model_dump()
                assigned_data.update({
                    "sdr_id": assigned_sdr.id,
                    "sdr_name": assigned_sdr.name,
                    "sdr_email": assigned_sdr.email,
                    "assignment_rule": assignment_rule,
                    "assigned_at": now,
                    "sla_deadline": schedule.sla_deadline,
                    "sla_action": schedule.sla_action,
                    "status": LeadStage.ASSIGNED,
                })
                assigned_dto = AssignedLead(**assigned_data)
                try:
                    crm_sync_result = self.crm_client.sync_lead(assigned_dto)
                    crm_ext_id = crm_sync_result.get("external_id")

                    # Audit CRM push success
                    audit_crm = AuditLogModel(
                        lead_id=lead_record.id,
                        action="CRM_SYNC_COMPLETED",
                        stage_from=LeadStage.ASSIGNED.value,
                        stage_to=LeadStage.ASSIGNED.value,
                        actor="MOCK_CRM_CLIENT",
                        details=json.dumps(crm_sync_result),
                        timestamp=datetime.now(timezone.utc),
                    )
                    session.add(audit_crm)
                    session.commit()

                except Exception as sync_err:
                    logger.error("CRM push failed for lead %s: %s", lead_record.id, sync_err)
                    self.dlq.log_failure(
                        payload=raw_payload,
                        error=sync_err,
                        db_session=session,
                        context={"lead_id": lead_record.id, "stage": "CRM_SYNC"},
                    )

            return PipelineResult(
                status="SUCCESS",
                success=True,
                lead_id=lead_record.id,
                lead_uuid=lead_record.lead_uuid,
                email=lead_record.email,
                company=lead_record.company,
                score=lead_record.score,
                score_tier=lead_record.score_tier,
                assigned_sdr=assigned_sdr.name,
                sdr_email=assigned_sdr.email,
                sla_deadline=schedule.sla_deadline.isoformat(),
                sla_action=schedule.sla_action,
                crm_external_id=crm_ext_id,
                details={
                    "score_breakdown": scored_lead.score_breakdown,
                    "priority": schedule.priority,
                    "is_automated": schedule.is_automated,
                },
            )

        except Exception as unexpected_err:
            logger.exception("Unexpected exception during pipeline execution: %s", unexpected_err)
            session.rollback()
            self.dlq.log_failure(
                payload=raw_payload,
                error=PipelineProcessingError(str(unexpected_err)),
                db_session=session,
                context={"stage": "ORCHESTRATION"},
            )
            return PipelineResult(
                status="PROCESSING_ERROR",
                success=False,
                email=raw_payload.get("email"),
                company=raw_payload.get("company"),
                error_message=str(unexpected_err),
            )
        finally:
            session.close()

    def process_batch(
        self,
        leads: List[Dict[str, Any]],
        assignment_rule: AssignmentRule = AssignmentRule.TIER_BASED,
        sync_crm: bool = True,
        strict_dedup: bool = True,
    ) -> BatchPipelineSummary:
        """Process a batch of inbound leads sequentially."""
        summary = BatchPipelineSummary(total_ingested=len(leads))

        for item in leads:
            res = self.process_lead(
                raw_payload=item,
                assignment_rule=assignment_rule,
                sync_crm=sync_crm,
                strict_dedup=strict_dedup,
            )
            summary.results.append(res)
            if res.status == "SUCCESS":
                summary.total_success += 1
            elif res.status == "DUPLICATE":
                summary.total_duplicates += 1
            else:
                summary.total_failed += 1

        return summary

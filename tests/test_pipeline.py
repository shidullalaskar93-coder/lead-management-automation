"""Comprehensive Integration Tests for the Core Lead Automation Pipeline."""

from datetime import datetime, timezone
import pytest
from sqlalchemy import select

from src.models import AssignmentModel, AuditLogModel, LeadModel, LeadStage
from src.pipeline import LeadAutomationPipeline, PipelineResult


class TestPipelineOrchestration:
    """End-to-end integration tests for ingestion, scoring, routing, and storage."""

    def test_hot_lead_full_pipeline(self, test_pipeline: LeadAutomationPipeline, valid_hot_lead_payload):
        """Verify Hot lead completes pipeline with 15-min SLA and database persistence."""
        res: PipelineResult = test_pipeline.process_lead(valid_hot_lead_payload)

        assert res.success is True
        assert res.status == "SUCCESS"
        assert res.score >= 75
        assert res.score_tier == "HOT"
        assert res.assigned_sdr in ["Senior Hot SDR 1", "Senior Hot SDR 2"]
        assert "15-min SLA" in res.sla_action
        assert res.lead_id is not None
        assert res.crm_external_id is not None

        # Verify Database State
        session = test_pipeline.session_factory()
        try:
            lead = session.get(LeadModel, res.lead_id)
            assert lead is not None
            assert lead.email == "tony.stark@starkindustries.com"
            assert lead.company == "Stark Industries"
            assert lead.status == LeadStage.ASSIGNED.value

            # Verify Assignment
            assignment = session.execute(
                select(AssignmentModel).where(AssignmentModel.lead_id == lead.id)
            ).scalars().first()
            assert assignment is not None
            assert assignment.status == "PENDING"
            assert assignment.sla_deadline is not None

            # Verify Audit Logs
            audits = session.execute(
                select(AuditLogModel).where(AuditLogModel.lead_id == lead.id).order_by(AuditLogModel.id)
            ).scalars().all()
            assert len(audits) >= 2
            actions = [a.action for a in audits]
            assert "LEAD_INGESTED_AND_VALIDATED" in actions
            assert "LEAD_ASSIGNED_TO_SDR" in actions
        finally:
            session.close()

    def test_warm_lead_sla_and_assignment(self, test_pipeline: LeadAutomationPipeline, valid_warm_lead_payload):
        """Verify Warm lead receives 4-hour SLA and Mid SDR assignment."""
        res: PipelineResult = test_pipeline.process_lead(valid_warm_lead_payload)

        assert res.success is True
        assert res.score_tier == "WARM"
        assert res.assigned_sdr == "Mid Warm SDR 1"
        assert "4-hour SLA" in res.sla_action

    def test_cold_lead_automated_drip_flag(self, test_pipeline: LeadAutomationPipeline, valid_cold_lead_payload):
        """Verify Cold lead receives automated nurture flag and Inbound SDR."""
        res: PipelineResult = test_pipeline.process_lead(valid_cold_lead_payload)

        assert res.success is True
        assert res.score_tier == "COLD"
        assert res.assigned_sdr == "Inbound SDR 1"
        assert "Automated Drip" in res.sla_action or "Marketing Nurture" in res.sla_action
        assert res.details.get("is_automated") is True

    def test_duplicate_lead_detection(self, test_pipeline: LeadAutomationPipeline, valid_hot_lead_payload):
        """Submitting the same lead twice should detect duplicate and avoid duplicate persistence."""
        # 1. First submission succeeds
        first_res = test_pipeline.process_lead(valid_hot_lead_payload, strict_dedup=True)
        assert first_res.success is True

        # 2. Second submission should be flagged as DUPLICATE
        dup_res = test_pipeline.process_lead(valid_hot_lead_payload, strict_dedup=True)
        assert dup_res.success is False
        assert dup_res.status == "DUPLICATE"
        assert dup_res.lead_id == first_res.lead_id

        # Verify only 1 record exists in 'leads' table
        session = test_pipeline.session_factory()
        try:
            leads_count = session.query(LeadModel).filter(
                LeadModel.email == valid_hot_lead_payload["email"].lower()
            ).count()
            assert leads_count == 1

            # Verify audit trail contains duplicate attempt
            dup_audits = session.query(AuditLogModel).filter(
                AuditLogModel.action == "DUPLICATE_ATTEMPT_IGNORED"
            ).all()
            assert len(dup_audits) == 1
        finally:
            session.close()

    def test_batch_ingestion_summary(
        self,
        test_pipeline: LeadAutomationPipeline,
        valid_hot_lead_payload,
        valid_warm_lead_payload,
    ):
        """Batch execution should correctly aggregate success, duplicate, and failed counts."""
        batch = [
            valid_hot_lead_payload,
            valid_warm_lead_payload,
            # Duplicate of hot lead
            valid_hot_lead_payload,
            # Invalid lead missing company
            {"email": "bad@lead.com", "first_name": "Bad", "company": ""},
        ]

        summary = test_pipeline.process_batch(batch, strict_dedup=True)
        assert summary.total_ingested == 4
        assert summary.total_success == 2
        assert summary.total_duplicates == 1
        assert summary.total_failed == 1
        assert summary.success_rate == 50.0

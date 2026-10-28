"""Tests for Dead-Letter Queue (DLQ), Error Handling, and Exponential Backoff Retries."""

import json
from pathlib import Path
import pytest
from sqlalchemy import select

from config.settings import Settings
from src.crm_client import MockCRMClient
from src.errors import DeadLetterQueueHandler, PipelineProcessingError
from src.models import AssignedLead, AssignmentRule, DeadLetterQueueModel, ScoreTier
from src.pipeline import LeadAutomationPipeline, PipelineResult


class TestDeadLetterQueueAndResilience:
    """Validate fault isolation, dead-letter persistence, and retry mechanics."""

    def test_validation_failure_dlq_persistence(self, test_settings: Settings, memory_db_engine):
        """Invalid lead must be captured in both the database DLQ table and JSONL file."""
        pipeline = LeadAutomationPipeline(settings=test_settings, db_engine=memory_db_engine)

        invalid_lead = {
            "first_name": "NoCompany",
            "last_name": "Test",
            "email": "invalid-email-format",
            "company": "",  # Missing mandatory company
        }

        res: PipelineResult = pipeline.process_lead(invalid_lead)
        assert res.success is False
        assert res.status == "VALIDATION_ERROR"

        # 1. Verify JSONL File Entry
        dlq_file = Path(test_settings.dlq_storage_file)
        assert dlq_file.exists()

        with open(dlq_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
            assert len(lines) >= 1
            entry = json.loads(lines[-1])
            assert entry["error_type"] == "LeadValidationError"
            assert "Validation failed" in entry["error_message"]
            assert entry["status"] == "OPEN"
            assert entry["payload"]["email"] == "invalid-email-format"

        # 2. Verify Database DLQ Table Record
        session = pipeline.session_factory()
        try:
            db_entries = session.execute(select(DeadLetterQueueModel)).scalars().all()
            assert len(db_entries) >= 1
            latest_db = db_entries[-1]
            assert latest_db.error_type == "LeadValidationError"
            assert latest_db.status == "OPEN"
        finally:
            session.close()

    def test_crm_sync_transient_retry_success(self, test_settings: Settings, memory_db_engine):
        """CRM sync experiencing 2 transient drops should succeed on 3rd attempt via exponential backoff."""
        # Force 2 transient drops (attempts 1 & 2 fail, attempt 3 succeeds)
        crm_client = MockCRMClient(settings=test_settings, force_failure_count=2)
        pipeline = LeadAutomationPipeline(
            settings=test_settings,
            db_engine=memory_db_engine,
            crm_client=crm_client,
        )

        lead_payload = {
            "first_name": "Thor",
            "last_name": "Odinson",
            "email": "thor@asgard.realm",
            "company": "Asgard Energy",
            "industry": "Enterprise Software",
            "company_size": "1000+",
            "budget": 300000,
            "intent_signal": "REQUEST_DEMO",
        }

        res: PipelineResult = pipeline.process_lead(lead_payload, sync_crm=True)
        assert res.success is True
        assert res.crm_external_id is not None
        assert "CRM-LEAD-ASGA" in res.crm_external_id

    def test_crm_sync_exhaustion_pushes_to_dlq(self, test_settings: Settings, memory_db_engine):
        """When CRM sync retries are completely exhausted, failure must be routed to DLQ."""
        # Force 4 failures when max_retries is 3 -> will fail after 3 attempts
        crm_client = MockCRMClient(settings=test_settings, force_failure_count=5)
        pipeline = LeadAutomationPipeline(
            settings=test_settings,
            db_engine=memory_db_engine,
            crm_client=crm_client,
        )

        lead_payload = {
            "first_name": "Loki",
            "last_name": "Laufeyson",
            "email": "loki@mischief.io",
            "company": "Mischief Ventures",
            "industry": "SaaS",
            "company_size": "51-200",
            "budget": 50000,
            "intent_signal": "CONTACT_SALES",
        }

        # Pipeline handles CRM failure gracefully: lead is saved in DB, but CRM failure logged to DLQ
        res: PipelineResult = pipeline.process_lead(lead_payload, sync_crm=True)

        # Check DLQ recorded the sync failure
        session = pipeline.session_factory()
        try:
            dlq_records = session.execute(
                select(DeadLetterQueueModel).where(DeadLetterQueueModel.error_type == "PipelineProcessingError")
            ).scalars().all()
            assert len(dlq_records) >= 1
            assert "CRM Sync failed after" in dlq_records[-1].error_message
        finally:
            session.close()

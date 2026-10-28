"""Custom Exception Hierarchy and Dead-Letter Queue (DLQ) Handler.

Provides structured, production-grade exception handling, auditability,
and resilient dead-letter persistence (file JSONL + database).
"""

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import traceback
from typing import Any, Dict, Optional, Union

logger = logging.getLogger("lead_pipeline.errors")


# ============================================================================
# Exception Hierarchy
# ============================================================================

class LeadAutomationError(Exception):
    """Base exception for all domain errors in the lead pipeline."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}
        self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error_type": self.__class__.__name__,
            "message": self.message,
            "details": self.details,
            "timestamp": self.timestamp,
        }


class LeadValidationError(LeadAutomationError):
    """Raised when inbound lead data fails schema validation or normalization."""
    pass


class DuplicateLeadError(LeadAutomationError):
    """Raised when an inbound lead is identified as a duplicate of an existing record."""
    pass


class EnrichmentTimeoutError(LeadAutomationError):
    """Raised when an external data enrichment or validation service exceeds SLA."""
    pass


class DatabaseLockError(LeadAutomationError):
    """Raised when database concurrency locks or conflicts prevent record persistence."""
    pass


class RoutingError(LeadAutomationError):
    """Raised when routing rules fail to find an eligible, active SDR."""
    pass


class PipelineProcessingError(LeadAutomationError):
    """Raised when an unrecoverable failure occurs inside the orchestration pipeline."""
    pass


# ============================================================================
# Dead-Letter Queue (DLQ) Handler
# ============================================================================

class DeadLetterQueueHandler:
    """Enterprise Dead-Letter Queue handler.

    Guarantees persistence of failed records to both append-only JSONL files
    and relational SQLite database tables with stack traces and payload metadata.
    """

    def __init__(self, storage_file: Union[str, Path] = "data/failed_leads.jsonl"):
        self.storage_file = Path(storage_file)
        self.storage_file.parent.mkdir(parents=True, exist_ok=True)

    def log_failure(
        self,
        payload: Union[Dict[str, Any], str],
        error: Union[Exception, str],
        db_session: Any = None,
        retry_count: int = 0,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Record a failure event to file storage and database.

        Args:
            payload: Raw inbound lead payload (dict or raw string)
            error: The caught exception or error string
            db_session: Optional active SQLAlchemy session for relational DB logging
            retry_count: Number of failed retry attempts
            context: Additional debugging metadata

        Returns:
            Dict containing the serialized DLQ entry
        """
        now = datetime.now(timezone.utc)
        error_type = error.__class__.__name__ if isinstance(error, Exception) else "UnknownError"
        error_message = str(error)

        stack_trace = ""
        if isinstance(error, Exception):
            stack_trace = "".join(traceback.format_exception(type(error), error, error.__traceback__))

        # Ensure payload is serializable
        if not isinstance(payload, (dict, list, str, int, float, bool)) and payload is not None:
            try:
                serialized_payload = str(payload)
            except Exception:
                serialized_payload = "<unserializable payload>"
        else:
            serialized_payload = payload

        dlq_entry = {
            "timestamp": now.isoformat(),
            "error_type": error_type,
            "error_message": error_message,
            "retry_count": retry_count,
            "status": "OPEN",
            "context": context or {},
            "payload": serialized_payload,
            "stack_trace": stack_trace,
        }

        # 1. Primary Resilient Fallback: JSONL Append
        try:
            with open(self.storage_file, mode="a", encoding="utf-8") as f:
                f.write(json.dumps(dlq_entry, default=str) + "\n")
        except Exception as file_err:
            logger.critical(
                "CRITICAL: Failed to write to DLQ JSONL storage file %s: %s",
                self.storage_file,
                file_err,
            )

        # 2. Relational Database DLQ Table Logging
        if db_session is not None:
            try:
                try:
                    from src.models import DeadLetterQueueModel
                except ImportError:
                    from .models import DeadLetterQueueModel


                payload_str = (
                    json.dumps(serialized_payload, default=str)
                    if not isinstance(serialized_payload, str)
                    else serialized_payload
                )

                db_record = DeadLetterQueueModel(
                    payload=payload_str,
                    error_type=error_type,
                    error_message=error_message,
                    stack_trace=stack_trace,
                    retry_count=retry_count,
                    status="OPEN",
                    created_at=now,
                )
                db_session.add(db_record)
                db_session.commit()
                dlq_entry["db_id"] = db_record.id
            except Exception as db_err:
                logger.error("Failed to commit DLQ record to database: %s", db_err)
                try:
                    db_session.rollback()
                except Exception:
                    pass

        logger.warning(
            "Lead logged to Dead-Letter Queue | Error: %s | Message: %s",
            error_type,
            error_message,
        )
        return dlq_entry

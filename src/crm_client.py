"""Mock Enterprise CRM Integration Client with Exponential Backoff Retries.

Simulates reliable bi-directional syncing with upstream CRMs (Salesforce, HubSpot)
incorporating automated exponential backoff, jitter, and circuit-breaker telemetry.
"""

import logging
import random
import time
from typing import Any, Dict, Optional

try:
    from config.settings import Settings, get_settings
except ImportError:
    from ..config.settings import Settings, get_settings

from .errors import PipelineProcessingError
from .models import AssignedLead

logger = logging.getLogger("lead_pipeline.crm_client")


class MockCRMClient:
    """Production-grade mock CRM API client with resilient retry policies."""

    def __init__(self, settings: Optional[Settings] = None, force_failure_count: int = 0):
        self.settings = settings or get_settings()
        self.max_retries = self.settings.crm_sync_max_retries
        self.base_delay = self.settings.crm_sync_base_delay
        self.max_delay = self.settings.crm_sync_max_delay
        self.simulate_failure_rate = self.settings.crm_simulate_failure_rate

        # Diagnostic harness for deterministic unit test simulations
        self._force_failure_count = force_failure_count
        self._current_failures_induced = 0

    def sync_lead(self, lead: AssignedLead) -> Dict[str, Any]:
        """Push assigned lead to upstream CRM with exponential backoff on transient errors.

        Args:
            lead: AssignedLead instance ready for CRM insertion

        Returns:
            Dict containing upstream CRM confirmation ID and sync status

        Raises:
            PipelineProcessingError: If all retry attempts are exhausted
        """
        attempt = 0
        last_error: Optional[Exception] = None

        while attempt < self.max_retries:
            attempt += 1
            try:
                # Perform simulated HTTP request
                return self._execute_api_push(lead, attempt)
            except Exception as ex:
                last_error = ex
                logger.warning(
                    "CRM Sync attempt %d/%d failed for lead %s (%s): %s",
                    attempt,
                    self.max_retries,
                    lead.email,
                    lead.company,
                    str(ex),
                )
                if attempt >= self.max_retries:
                    break

                # Exponential backoff with jitter
                delay = min(self.max_delay, self.base_delay * (2 ** (attempt - 1)))
                jitter = random.uniform(0.01, 0.05)
                total_sleep = delay + jitter
                logger.info("Backing off for %.3f seconds before retry...", total_sleep)
                time.sleep(total_sleep)

        # Retries exhausted
        error_msg = (
            f"CRM Sync failed after {self.max_retries} attempts for lead "
            f"'{lead.email}' ({lead.company}). Root cause: {last_error}"
        )
        logger.error(error_msg)
        raise PipelineProcessingError(error_msg, details={"lead_email": lead.email, "retries": attempt})

    def _execute_api_push(self, lead: AssignedLead, attempt: int) -> Dict[str, Any]:
        """Simulate single HTTP API call to external CRM endpoint."""
        # Check deterministic test failure harness
        if self._current_failures_induced < self._force_failure_count:
            self._current_failures_induced += 1
            raise ConnectionResetError(
                f"Simulated transient network drop / 503 Service Unavailable (Failure {self._current_failures_induced})"
            )

        # Check probabilistic simulation rate
        if self.simulate_failure_rate > 0.0 and random.random() < self.simulate_failure_rate:
            raise TimeoutError("Simulated upstream CRM Gateway Timeout (504)")

        # Success simulation
        crm_record_id = f"CRM-LEAD-{lead.company[:4].upper()}-{random.randint(100000, 999999)}"
        logger.info(
            "Successfully synced lead %s to CRM (External ID: %s) on attempt %d",
            lead.email,
            crm_record_id,
            attempt,
        )

        return {
            "status": "SYNCED",
            "external_id": crm_record_id,
            "crm_system": "Salesforce_Enterprise",
            "synced_at": lead.assigned_at.isoformat(),
            "attempt": attempt,
        }

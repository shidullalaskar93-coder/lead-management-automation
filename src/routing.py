"""Intelligent SDR Routing and Lead Allocation Engine.

Provides:
1. Thread-safe Round-Robin lead distribution.
2. Weighted SDR distribution based on experience, quota, and capacity.
3. Tier-based specialist routing (e.g. Hot -> Senior SDRs, Warm -> Mid SDRs, Cold -> Inbound).
4. State persistence to ensure deterministic fair-share parity across batches.
"""

from collections import defaultdict
import logging
from threading import Lock
from typing import Dict, List, Optional

try:
    from config.settings import SDRConfig, get_settings
except ImportError:
    from ..config.settings import SDRConfig, get_settings

from .errors import RoutingError
from .models import AssignmentRule, ScoreTier, ScoredLead

logger = logging.getLogger("lead_pipeline.routing")


class SDRRouter:
    """Thread-safe, stateful router managing SDR pools and assignment algorithms."""

    def __init__(self, sdr_roster: Optional[List[SDRConfig]] = None):
        self.settings = get_settings()
        self.sdr_roster: List[SDRConfig] = sdr_roster if sdr_roster is not None else list(self.settings.sdrs)
        self._lock = Lock()

        # State tracking for Round-Robin indices per pool key
        self._rr_indices: Dict[str, int] = defaultdict(int)

        # State tracking for Weighted distributions (virtual queue or counter)
        self._weighted_counters: Dict[str, int] = defaultdict(int)

        # Cumulative assignment counts for auditing & parity inspection
        self.assignment_counts: Dict[str, int] = defaultdict(int)

    def route_lead(
        self,
        lead: ScoredLead,
        rule: AssignmentRule = AssignmentRule.TIER_BASED,
    ) -> SDRConfig:
        """Assign an SDR to the given scored lead according to specified strategy.

        Args:
            lead: ScoredLead instance
            rule: AssignmentRule enum (TIER_BASED, ROUND_ROBIN, or WEIGHTED)

        Returns:
            The selected SDRConfig

        Raises:
            RoutingError: If no active SDRs are available
        """
        with self._lock:
            active_sdrs = [sdr for sdr in self.sdr_roster if sdr.active]
            if not active_sdrs:
                raise RoutingError("No active SDRs configured in the routing pool.")

            if rule == AssignmentRule.TIER_BASED:
                selected_sdr = self._route_by_tier(lead, active_sdrs)
            elif rule == AssignmentRule.WEIGHTED:
                selected_sdr = self._route_weighted(active_sdrs, pool_key="global_weighted")
            elif rule == AssignmentRule.ROUND_ROBIN:
                selected_sdr = self._route_round_robin(active_sdrs, pool_key="global_rr")
            else:
                # Default fallback
                selected_sdr = self._route_round_robin(active_sdrs, pool_key="default")

            self.assignment_counts[selected_sdr.id] += 1
            logger.info(
                "Lead [%s] tier=%s assigned to SDR [%s - %s] via %s",
                lead.company,
                lead.score_tier.value,
                selected_sdr.id,
                selected_sdr.name,
                rule.value,
            )
            return selected_sdr

    def _route_by_tier(self, lead: ScoredLead, active_sdrs: List[SDRConfig]) -> SDRConfig:
        """Route to specialized tier SDRs using weighted selection within tier."""
        tier_str = lead.score_tier.value

        # Filter SDRs matching specific tier or generalist ("ALL")
        candidates = [sdr for sdr in active_sdrs if sdr.tier == tier_str or sdr.tier == "ALL"]

        # Fallback to any active SDR if no specialist matches
        if not candidates:
            logger.warning(
                "No SDR found matching tier %s; falling back to full active SDR pool",
                tier_str,
            )
            candidates = active_sdrs

        # Use weighted selection within the tier pool
        return self._route_weighted(candidates, pool_key=f"tier_{tier_str}")

    def _route_round_robin(self, pool: List[SDRConfig], pool_key: str = "rr_pool") -> SDRConfig:
        """Select next SDR in sequence using modulo arithmetic."""
        if not pool:
            raise RoutingError(f"Cannot perform round-robin on empty SDR pool '{pool_key}'.")

        idx = self._rr_indices[pool_key] % len(pool)
        selected = pool[idx]
        self._rr_indices[pool_key] = (idx + 1) % len(pool)
        return selected

    def _route_weighted(self, pool: List[SDRConfig], pool_key: str = "weighted_pool") -> SDRConfig:
        """Select SDR based on configured capacity weights using interleaved sequence.

        Expands the pool according to SDR weights (e.g., Weight 3 appears 3 times),
        then cycles round-robin over the weighted sequence to guarantee fair proportions.
        """
        if not pool:
            raise RoutingError(f"Cannot perform weighted routing on empty SDR pool '{pool_key}'.")

        # Build expanded weighted sequence
        expanded_sequence: List[SDRConfig] = []
        for sdr in pool:
            expanded_sequence.extend([sdr] * max(1, sdr.weight))

        idx = self._weighted_counters[pool_key] % len(expanded_sequence)
        selected = expanded_sequence[idx]
        self._weighted_counters[pool_key] = (idx + 1) % len(expanded_sequence)
        return selected

    def reset_state(self) -> None:
        """Reset routing counters and assignment history (useful for tests)."""
        with self._lock:
            self._rr_indices.clear()
            self._weighted_counters.clear()
            self.assignment_counts.clear()

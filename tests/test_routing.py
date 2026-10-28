"""Tests for SDR Routing Rules, Round-Robin Parity, and Weighted Allocation."""

import pytest

from config.settings import SDRConfig
from src.errors import RoutingError
from src.models import AssignmentRule, RawLeadInput, ScoreTier, ScoredLead
from src.routing import SDRRouter


class TestSDRRouting:
    """Test allocation algorithms and state persistence."""

    def test_round_robin_parity(self):
        """Sequential leads routed via ROUND_ROBIN should cycle evenly through active SDRs."""
        roster = [
            SDRConfig(id="sdr_1", name="Alice", email="alice@test.io", tier="ALL", weight=1),
            SDRConfig(id="sdr_2", name="Bob", email="bob@test.io", tier="ALL", weight=1),
            SDRConfig(id="sdr_3", name="Charlie", email="charlie@test.io", tier="ALL", weight=1),
        ]
        router = SDRRouter(sdr_roster=roster)
        lead = ScoredLead(
            email="lead@test.com",
            first_name="Test",
            company="Test Corp",
            score=80,
            score_tier=ScoreTier.HOT,
        )

        assigned_sdrs = [
            router.route_lead(lead, rule=AssignmentRule.ROUND_ROBIN).id
            for _ in range(6)
        ]

        expected_sequence = ["sdr_1", "sdr_2", "sdr_3", "sdr_1", "sdr_2", "sdr_3"]
        assert assigned_sdrs == expected_sequence
        assert router.assignment_counts["sdr_1"] == 2
        assert router.assignment_counts["sdr_2"] == 2
        assert router.assignment_counts["sdr_3"] == 2

    def test_weighted_sdr_routing(self):
        """Weighted routing should distribute leads proportionally to SDR weights."""
        roster = [
            SDRConfig(id="sdr_senior", name="Senior", email="sr@test.io", tier="ALL", weight=3),
            SDRConfig(id="sdr_junior", name="Junior", email="jr@test.io", tier="ALL", weight=1),
        ]
        router = SDRRouter(sdr_roster=roster)
        lead = ScoredLead(
            email="lead@test.com",
            first_name="Test",
            company="Test Corp",
            score=80,
            score_tier=ScoreTier.HOT,
        )

        for _ in range(8):
            router.route_lead(lead, rule=AssignmentRule.WEIGHTED)

        # In a 3:1 ratio across 8 assignments:
        # Senior gets 6, Junior gets 2
        assert router.assignment_counts["sdr_senior"] == 6
        assert router.assignment_counts["sdr_junior"] == 2

    def test_tier_based_routing(self):
        """HOT leads should route to HOT SDRs; WARM leads to WARM SDRs."""
        roster = [
            SDRConfig(id="sdr_hot", name="Hot Specialist", email="hot@test.io", tier="HOT", weight=1),
            SDRConfig(id="sdr_warm", name="Warm Specialist", email="warm@test.io", tier="WARM", weight=1),
            SDRConfig(id="sdr_cold", name="Cold Specialist", email="cold@test.io", tier="COLD", weight=1),
        ]
        router = SDRRouter(sdr_roster=roster)

        hot_lead = ScoredLead(
            email="hot@test.com",
            first_name="Hot",
            company="Hot Co",
            score=90,
            score_tier=ScoreTier.HOT,
        )
        warm_lead = ScoredLead(
            email="warm@test.com",
            first_name="Warm",
            company="Warm Co",
            score=55,
            score_tier=ScoreTier.WARM,
        )
        cold_lead = ScoredLead(
            email="cold@test.com",
            first_name="Cold",
            company="Cold Co",
            score=20,
            score_tier=ScoreTier.COLD,
        )

        assert router.route_lead(hot_lead, rule=AssignmentRule.TIER_BASED).id == "sdr_hot"
        assert router.route_lead(warm_lead, rule=AssignmentRule.TIER_BASED).id == "sdr_warm"
        assert router.route_lead(cold_lead, rule=AssignmentRule.TIER_BASED).id == "sdr_cold"

    def test_inactive_sdr_exclusion(self):
        """Inactive SDRs must not receive lead allocations."""
        roster = [
            SDRConfig(id="sdr_active", name="Active", email="active@test.io", tier="ALL", weight=1, active=True),
            SDRConfig(id="sdr_inactive", name="Inactive", email="off@test.io", tier="ALL", weight=1, active=False),
        ]
        router = SDRRouter(sdr_roster=roster)
        lead = ScoredLead(
            email="lead@test.com",
            first_name="Test",
            company="Test Corp",
            score=70,
            score_tier=ScoreTier.WARM,
        )

        for _ in range(4):
            selected = router.route_lead(lead, rule=AssignmentRule.ROUND_ROBIN)
            assert selected.id == "sdr_active"

        assert "sdr_inactive" not in router.assignment_counts

    def test_empty_roster_raises_routing_error(self):
        """Routing with zero active SDRs must raise RoutingError."""
        router = SDRRouter(sdr_roster=[])
        lead = ScoredLead(
            email="lead@test.com",
            first_name="Test",
            company="Test Corp",
            score=50,
            score_tier=ScoreTier.WARM,
        )
        with pytest.raises(RoutingError):
            router.route_lead(lead)

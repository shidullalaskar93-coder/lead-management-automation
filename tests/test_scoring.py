"""Tests for Dynamic Lead Scoring Engine and Tier Classification."""

from config.settings import ScoringCriteria
from src.models import RawLeadInput, ScoreTier
from src.scoring import LeadScoringEngine


class TestLeadScoring:
    """Evaluate scoring criteria, point distribution, and threshold tiers."""

    def test_max_score_hot_lead(self):
        """Top-tier enterprise lead should hit or approach 100 points and score as HOT."""
        lead = RawLeadInput(
            email="ceo@megacorp.io",
            first_name="Arthur",
            company="MegaCorp SaaS",
            industry="Enterprise Software",  # 25 pts
            company_size="1000+",             # 25 pts
            intent_signal="REQUEST_DEMO",     # 30 pts
            budget=150000.0,                  # 20 pts
        )
        engine = LeadScoringEngine()
        score, tier, breakdown = engine.calculate_score(lead)

        assert score == 100
        assert tier == ScoreTier.HOT
        assert breakdown["industry"] == 25
        assert breakdown["company_size"] == 25
        assert breakdown["intent_signal"] == 30
        assert breakdown["budget"] == 20

    def test_mid_tier_warm_lead(self):
        """Mid-market lead should score in WARM range (45-74 points)."""
        lead = RawLeadInput(
            email="lead@healthtech.com",
            first_name="Victor",
            company="HealthTech Systems",
            industry="Healthcare",            # 20 pts
            company_size="51-200",            # 14 pts
            intent_signal="PRICING_PAGE",     # 20 pts
            budget=25000.0,                   # 10 pts
        )
        engine = LeadScoringEngine()
        score, tier, breakdown = engine.calculate_score(lead)

        assert 45 <= score <= 74
        assert tier == ScoreTier.WARM
        assert score == (20 + 14 + 20 + 10)

    def test_low_tier_cold_lead(self):
        """Small boutique with minimal budget and passive signal scores as COLD."""
        lead = RawLeadInput(
            email="owner@cornerstore.com",
            first_name="Barry",
            company="Corner Store",
            industry="Retail",                # 10 pts
            company_size="1-10",              # 4 pts
            intent_signal="NEWSLETTER_SIGNUP",# 6 pts
            budget=1000.0,                    # 2 pts
        )
        engine = LeadScoringEngine()
        score, tier, breakdown = engine.calculate_score(lead)

        assert score < 45
        assert tier == ScoreTier.COLD
        assert score == (10 + 4 + 6 + 2)

    def test_score_clamping_boundary(self):
        """Scores must never exceed 100 or fall below 0."""
        # Custom oversized criteria
        criteria = ScoringCriteria(
            industry_scores={"saas": 60},
            size_scores={"1000+": 60},
            intent_scores={"demo": 60},
        )
        engine = LeadScoringEngine(criteria=criteria)
        lead = RawLeadInput(
            email="overflow@test.com",
            first_name="Sam",
            company="Big Test",
            industry="SaaS",
            company_size="1000+",
            intent_signal="demo",
            budget=200000,
        )
        score, tier, _ = engine.calculate_score(lead)
        assert score == 100
        assert tier == ScoreTier.HOT

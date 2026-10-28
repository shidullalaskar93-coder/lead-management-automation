"""Dynamic Lead Scoring Engine.

Calculates multi-dimensional qualification scores (0-100) based on:
1. Firmographics: Industry Sector (up to 25 pts)
2. Firmographics: Company Size / Employee Count (up to 25 pts)
3. Behavioral Intent Signals (up to 30 pts)
4. Financial Capability: Budget Tier (up to 20 pts)

Classifies leads into prioritized tiers (HOT, WARM, COLD) with full audit transparency.
"""

from typing import Dict, Optional, Tuple

try:
    from config.settings import ScoringCriteria, get_settings
except ImportError:
    from ..config.settings import ScoringCriteria, get_settings

from .models import RawLeadInput, ScoreTier, ScoredLead


class LeadScoringEngine:
    """Enterprise rule-based and configurable lead scoring system."""

    def __init__(self, criteria: Optional[ScoringCriteria] = None):
        self.settings = get_settings()
        self.criteria = criteria or self.settings.scoring

    def calculate_score(self, lead: RawLeadInput) -> Tuple[int, ScoreTier, Dict[str, int]]:
        """Compute the composite qualification score and classify into tier.

        Args:
            lead: Validated RawLeadInput instance

        Returns:
            Tuple of (total_score, score_tier, score_breakdown)
        """
        breakdown: Dict[str, int] = {
            "industry": self._score_industry(lead.industry),
            "company_size": self._score_company_size(lead.company_size),
            "intent_signal": self._score_intent(lead.intent_signal),
            "budget": self._score_budget(lead.budget),
        }

        total_score = sum(breakdown.values())
        # Clamp strictly between 0 and 100
        total_score = max(0, min(100, total_score))

        tier = self._determine_tier(total_score)
        return total_score, tier, breakdown

    def score_lead(self, lead: RawLeadInput) -> ScoredLead:
        """Score a lead and return a populated ScoredLead model."""
        score, tier, breakdown = self.calculate_score(lead)

        data = lead.model_dump()
        return ScoredLead(
            **data,
            score=score,
            score_tier=tier,
            score_breakdown=breakdown,
        )

    def _score_industry(self, industry: Optional[str]) -> int:
        if not industry:
            return self.criteria.industry_scores.get("other", 5)
        clean_ind = industry.strip().lower()
        return self.criteria.industry_scores.get(
            clean_ind,
            self.criteria.industry_scores.get("other", 5),
        )

    def _score_company_size(self, size: Optional[str]) -> int:
        if not size:
            return self.criteria.size_scores.get("1-10", 4)
        clean_size = size.strip()
        # Direct lookup
        if clean_size in self.criteria.size_scores:
            return self.criteria.size_scores[clean_size]

        # Fuzzy match for numeric formats like "150", "2500"
        try:
            num = int("".join(filter(str.isdigit, clean_size)))
            if num > 1000:
                return self.criteria.size_scores.get("1000+", 25)
            elif num > 500:
                return self.criteria.size_scores.get("501-1000", 22)
            elif num > 200:
                return self.criteria.size_scores.get("201-500", 18)
            elif num > 50:
                return self.criteria.size_scores.get("51-200", 14)
            elif num > 10:
                return self.criteria.size_scores.get("11-50", 8)
            else:
                return self.criteria.size_scores.get("1-10", 4)
        except ValueError:
            return self.criteria.size_scores.get("1-10", 4)

    def _score_intent(self, signal: Optional[str]) -> int:
        if not signal:
            return self.criteria.intent_scores.get("general_inquiry", 8)
        clean_sig = signal.strip().lower().replace("-", "_")
        return self.criteria.intent_scores.get(
            clean_sig,
            self.criteria.intent_scores.get("general_inquiry", 8),
        )

    def _score_budget(self, budget: Optional[float]) -> int:
        if budget is None or budget <= 0:
            return 0
        for tier in self.criteria.budget_tiers:
            if budget >= tier["min"]:
                return tier["score"]
        return 0

    def _determine_tier(self, score: int) -> ScoreTier:
        if score >= self.settings.score_hot_threshold:
            return ScoreTier.HOT
        elif score >= self.settings.score_warm_threshold:
            return ScoreTier.WARM
        else:
            return ScoreTier.COLD

"""Tests for Ingestion Schema Validation, Cleaning, and Normalization."""

import pytest
from pydantic import ValidationError

from src.models import RawLeadInput


class TestLeadValidation:
    """Validate inbound lead parsing, normalization, and constraints."""

    def test_valid_lead_normalization(self):
        """Verify trimming, casing, and phone formatting on valid lead."""
        payload = {
            "first_name": "  clark  ",
            "last_name": "  kent  ",
            "email": "  CLARK.KENT@DAILYPLANET.COM  ",
            "company": "  daily planet media  ",
            "phone": "+1 (555) 234-5678",
            "budget": "$125,000.00",
            "source": "website",
            "intent_signal": "demo",
        }
        lead = RawLeadInput(**payload)

        assert lead.first_name == "Clark"
        assert lead.last_name == "Kent"
        assert lead.email == "clark.kent@dailyplanet.com"
        assert lead.company == "Daily Planet Media"
        assert lead.phone == "+15552345678"
        assert lead.budget == 125000.0
        assert lead.source == "WEBSITE"
        assert lead.intent_signal == "REQUEST_DEMO"

    def test_invalid_email_formats(self):
        """Invalid email addresses should raise ValidationError."""
        bad_emails = [
            "plainaddress",
            "@missingusername.com",
            "username@.com",
            "username@domain..com",
            "",
            None,
        ]
        for bad_email in bad_emails:
            with pytest.raises(ValidationError):
                RawLeadInput(
                    email=bad_email,
                    first_name="John",
                    company="Acme Corp",
                )

    def test_mandatory_fields_missing(self):
        """Missing company, first_name, or email must fail validation."""
        # Missing company
        with pytest.raises(ValidationError):
            RawLeadInput(email="test@acme.com", first_name="John", company="")

        # Missing first_name
        with pytest.raises(ValidationError):
            RawLeadInput(email="test@acme.com", first_name="   ", company="Acme")

        # Missing email
        with pytest.raises(ValidationError):
            RawLeadInput(first_name="John", company="Acme")

    def test_phone_normalization_variations(self):
        """Check various valid and invalid phone structures."""
        # 10-digit US number without prefix -> adds +1
        lead1 = RawLeadInput(
            email="test1@test.com",
            first_name="Jane",
            company="Test Inc",
            phone="5554321098",
        )
        assert lead1.phone == "+15554321098"

        # International number with +
        lead2 = RawLeadInput(
            email="test2@test.com",
            first_name="Jane",
            company="Test Inc",
            phone="+44 20 7946 0958",
        )
        assert lead2.phone == "+442079460958"

        # Too short phone (<7 digits)
        with pytest.raises(ValidationError):
            RawLeadInput(
                email="test3@test.com",
                first_name="Jane",
                company="Test Inc",
                phone="12345",
            )

        # None or empty phone is permitted
        lead4 = RawLeadInput(
            email="test4@test.com",
            first_name="Jane",
            company="Test Inc",
            phone=None,
        )
        assert lead4.phone is None

    def test_key_alias_standardization(self):
        """Verify field alias handling for CSV/webhook payloads with varying keys."""
        payload = {
            "given_name": "Diana",
            "family_name": "Prince",
            "work_email": "diana@themyscira.gov",
            "organization": "Themyscira Embassy",
            "contact_number": "+15557778888",
            "annual_budget": 50000,
        }
        lead = RawLeadInput(**payload)
        assert lead.first_name == "Diana"
        assert lead.last_name == "Prince"
        assert lead.email == "diana@themyscira.gov"
        assert lead.company == "Themyscira Embassy"
        assert lead.phone == "+15557778888"
        assert lead.budget == 50000.0

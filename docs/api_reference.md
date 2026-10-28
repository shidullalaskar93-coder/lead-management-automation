# API & Schema Reference: Lead Management Automation

## Ingestion Data Schema (`RawLeadInput`)

Inbound leads can be submitted as JSON payloads, REST webhook requests, or CSV rows. The schema performs automatic key aliasing, trimming, lowercasing, and E.164 phone normalization.

### Supported Field Definitions

| Field Name | Type | Required | Default | Aliases Supported | Description |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `email` | `string` | **Yes** | — | `email_address`, `work_email`, `mail` | Validated RFC email address; auto-lowercased and trimmed. |
| `first_name` | `string` | **Yes** | — | `firstname`, `first name`, `fname`, `given_name` | Contact given name; trimmed and title-cased. |
| `last_name` | `string` | No | `""` | `lastname`, `last name`, `lname`, `surname` | Optional surname; trimmed and title-cased. |
| `company` | `string` | **Yes** | — | `company_name`, `companyname`, `organization` | Organization or account name. |
| `phone` | `string` | No | `null` | `phone_number`, `telephone`, `mobile` | Normalized E.164 string (e.g. `+15552345678`). |
| `industry` | `string` | No | `"Other"` | `industry_sector`, `vertical` | Business industry sector used for scoring. |
| `company_size` | `string` | No | `"1-10"` | `size`, `employees`, `employee_count` | Employee headcount bucket (`1000+`, `501-1000`, etc.). |
| `budget` | `float` | No | `0.0` | `annual_budget`, `estimated_budget`, `revenue` | Currency string or numeric parsed to standard float. |
| `source` | `string` | No | `"WEBSITE"` | `lead_source`, `utm_source` | Acquisition source (`WEBSITE`, `LINKEDIN`, etc.). |
| `intent_signal`| `string` | No | `"GENERAL_INQUIRY"` | `intent`, `signal`, `action`, `event_type` | Primary behavior (`REQUEST_DEMO`, `PRICING_PAGE`, etc.). |
| `custom_metadata`| `dict` | No | `{}` | `metadata`, `extra` | Arbitrary JSON payload for upstream tags and UTMs. |

---

## Example Inbound Payloads

### Webhook JSON Payload
```json
{
  "first_name": "Tony",
  "last_name": "Stark",
  "email": "tony.stark@starkindustries.com",
  "company": "Stark Industries",
  "phone": "+1 (555) 888-9999",
  "industry": "Enterprise Software",
  "company_size": "1000+",
  "budget": "$500,000",
  "source": "WEBSITE",
  "intent_signal": "REQUEST_DEMO",
  "custom_metadata": {
    "campaign": "q4_enterprise_summit",
    "region": "NA-East"
  }
}
```

### Batch CSV Ingestion
```csv
first_name,last_name,email,company,phone,industry,company_size,budget,source,intent_signal
John,Doe,john.doe@acme.com,Acme Corp,+1 (555) 234-5678,Enterprise Software,1000+,125000,WEBSITE,REQUEST_DEMO
Samantha,Vance,s.vance@fintech.io,FinTech Global,+1 (555) 876-5432,Fintech,501-1000,75000,LINKEDIN,CONTACT_SALES
```

---

## Python API Usage

### 1. Ingest Single Lead
```python
from src.pipeline import LeadAutomationPipeline
from src.models import AssignmentRule

pipeline = LeadAutomationPipeline()

payload = {
    "first_name": "Sarah",
    "last_name": "Connor",
    "email": "sarah@cyberdyne.com",
    "company": "Cyberdyne Systems",
    "industry": "Cybersecurity",
    "company_size": "1000+",
    "budget": 200000,
    "intent_signal": "REQUEST_DEMO",
}

result = pipeline.process_lead(payload, assignment_rule=AssignmentRule.TIER_BASED)

print(f"Status: {result.status}")
print(f"Score: {result.score} ({result.score_tier})")
print(f"Assigned SDR: {result.assigned_sdr}")
print(f"SLA Deadline: {result.sla_deadline}")
print(f"SLA Action: {result.sla_action}")
```

### 2. Ingest Batch from CSV
```python
from src.pipeline import LeadAutomationPipeline
from src.utils import load_leads_from_csv

pipeline = LeadAutomationPipeline()
raw_leads = load_leads_from_csv("data/sample_leads.csv")
summary = pipeline.process_batch(raw_leads)

print(f"Ingested: {summary.total_ingested}")
print(f"Succeeded: {summary.total_success}")
print(f"Duplicates: {summary.total_duplicates}")
print(f"Failed: {summary.total_failed}")
```

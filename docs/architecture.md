# System Architecture: Lead Management Automation

## 1. Executive & Architectural Overview

The **Lead Management Automation System** (Project C) is an enterprise-grade pipeline designed for automated inbound lead qualification, intelligent sales representative allocation, and fault-tolerant CRM synchronization. Built adhering to Clean Architecture principles, the system decouples validation, scoring rules, routing strategies, persistence, and external integration.

---

## 2. End-to-End Orchestration Flow

```mermaid
sequenceDiagram
    autonumber
    actor Webhook as Inbound Source (Webhook / CSV)
    participant Pipeline as LeadAutomationPipeline
    participant Validator as Pydantic Validation & Cleaning
    participant Dedup as Deduplication Engine
    participant Scoring as Dynamic Scoring Engine
    participant Router as SDR Router (Round-Robin/Weighted)
    participant Scheduler as SLA & Follow-up Scheduler
    participant DB as SQLite / Relational DB
    participant CRM as Mock Enterprise CRM
    participant DLQ as Dead-Letter Queue (File + DB)

    Webhook->>Pipeline: Inbound lead payload
    Pipeline->>Validator: Validate & normalize fields
    alt Validation Failure
        Validator-->>Pipeline: ValidationError
        Pipeline->>DLQ: Log payload, error & stack trace
        Pipeline-->>Webhook: Return VALIDATION_ERROR result
    else Validation Success
        Validator-->>Pipeline: Cleaned RawLeadInput
    end

    Pipeline->>Dedup: Check duplicate (email / company+phone within 30d)
    alt Duplicate Detected
        Dedup-->>Pipeline: Match found
        Pipeline->>DB: Log DUPLICATE_ATTEMPT_IGNORED to audit_logs
        Pipeline->>DLQ: Route to DLQ (strict mode)
        Pipeline-->>Webhook: Return DUPLICATE result
    else Unique Lead
        Dedup-->>Pipeline: Unique confirmed
    end

    Pipeline->>Scoring: Compute multi-dimensional score (0-100)
    Scoring-->>Pipeline: ScoredLead (HOT / WARM / COLD)

    Pipeline->>Router: Allocate SDR (Tier-Specialist / Weighted / Round-Robin)
    Router-->>Pipeline: Assigned SDRConfig

    Pipeline->>Scheduler: Generate SLA timestamp & directive
    Scheduler-->>Pipeline: FollowUpSchedule (e.g. 15-min SLA)

    Pipeline->>DB: Atomic Transaction [leads + assignments + audit_logs]
    DB-->>Pipeline: Commit Confirmed

    Pipeline->>CRM: Push Lead (Exponential Backoff Retries)
    alt CRM Transient Network Drop
        CRM-->>Pipeline: 503 / Timeout
        Pipeline->>CRM: Retry with Exponential Backoff + Jitter
    end
    CRM-->>Pipeline: Synced Confirmation Receipt (External ID)
    Pipeline->>DB: Log CRM_SYNC_COMPLETED to audit_logs
    Pipeline-->>Webhook: Return SUCCESS result
```

---

## 3. Pipeline State Machine

Leads progress through deterministic lifecycle stages. Transitions are strictly audited in the `audit_logs` table.

```mermaid
stateDiagram-v2
    [*] --> NEW: Inbound Webhook / CSV
    NEW --> VALIDATED: Schema Verified & Normalized
    NEW --> DLQ_FAILED: Validation / Format Failure
    VALIDATED --> ASSIGNED: Score Computed & SDR Routed
    VALIDATED --> DUPLICATE_ARCHIVED: Duplicate Detected
    ASSIGNED --> CONTACTED: SDR Reaches Out Within SLA
    CONTACTED --> CONVERTED: Deal Closed / Opportunity Created
    CONTACTED --> DISQUALIFIED: Unqualified / No Budget
    DLQ_FAILED --> [*]
    DUPLICATE_ARCHIVED --> [*]
    CONVERTED --> [*]
    DISQUALIFIED --> [*]
```

---

## 4. Entity-Relationship Data Model

The persistence layer uses SQLAlchemy ORM mapping to SQLite (or PostgreSQL in production):

```mermaid
erDiagram
    LEADS ||--o{ ASSIGNMENTS : "assigned to"
    LEADS ||--o{ AUDIT_LOGS : "audited by"
    
    LEADS {
        int id PK
        string lead_uuid UK
        string email
        string first_name
        string last_name
        string company
        string phone
        string industry
        string company_size
        float budget
        string source
        string intent_signal
        int score
        string score_tier
        string status
        text custom_metadata
        datetime created_at
        datetime updated_at
    }

    ASSIGNMENTS {
        int id PK
        int lead_id FK
        string sdr_id
        string sdr_name
        string sdr_email
        datetime assigned_at
        string assignment_rule
        datetime sla_deadline
        string sla_action
        string status
    }

    AUDIT_LOGS {
        int id PK
        int lead_id FK
        string action
        string stage_from
        string stage_to
        string actor
        text details
        datetime timestamp
    }

    DEAD_LETTER_QUEUE {
        int id PK
        text payload
        string error_type
        text error_message
        text stack_trace
        int retry_count
        string status
        datetime created_at
        datetime resolved_at
    }
```

---

## 5. Scoring Algorithm & Matrix

The scoring engine calculates qualification scores between 0 and 100 based on four criteria:

| Category | Criterion | Points (Max) | Evaluation Basis |
| :--- | :--- | :--- | :--- |
| **Firmographics** | Industry Sector | **25 pts** | Enterprise Software / SaaS (25), FinTech (25), Cybersecurity (25), Healthcare (20), Manufacturing (15), Retail (10), Other (5) |
| **Firmographics** | Company Size | **25 pts** | 1000+ employees (25), 501-1000 (22), 201-500 (18), 51-200 (14), 11-50 (8), 1-10 (4) |
| **Behavioral** | Intent Signals | **30 pts** | Request Demo (30), Contact Sales (28), Trial Signup (22), Pricing Page (20), Webinar (15), Whitepaper (12), Newsletter (6), Inquiry (8) |
| **Financial** | Budget Tier | **20 pts** | ≥\$100k (20), ≥\$50k (15), ≥\$25k (10), ≥\$10k (6), <\$10k (2) |

### Tier Classification:
- **HOT Tier (Score ≥ 75)**: High-intent enterprise prospects routed to Senior SDRs with a **15-minute SLA**.
- **WARM Tier (Score 45 - 74)**: Mid-market qualified prospects routed to Mid-Market SDRs with a **4-hour SLA**.
- **COLD Tier (Score < 45)**: Low-intent or micro-business leads flagged for **Automated Marketing Drip** sequences.

---

## 6. SDR Routing & Fair-Share Parity

1. **Round-Robin Routing**: Modulo-based cyclic allocation across active SDRs ensuring exact parity:
   $$\text{index} = \text{counter} \pmod N$$
2. **Weighted Routing**: Distributes leads according to SDR capacity, tenure, and quota targets using an interleaved deterministic sequence.
3. **Tier-Based Specialist Routing**: Partitions SDRs into specialized tier pools (`HOT`, `WARM`, `COLD`) and applies weighted distribution within each tier.
4. **Thread Safety**: All state updates are synchronized via re-entrant locks (`threading.Lock`), ensuring concurrency safety in multi-threaded webhook servers.

---

## 7. Fault Tolerance & Dead-Letter Queue (DLQ)

Invalid leads, duplicate collisions, or upstream network failures are isolated to protect pipeline throughput:
- **Dual Persistence**:
  1. Relational database table (`dead_letter_queue`) for administrative querying and reprocessing.
  2. Append-only JSON Lines file (`data/failed_leads.jsonl`) guaranteeing persistence even during database downtime.
- **Exponential Backoff Retries**: Upstream CRM pushes retry up to 3 times with exponential backoff and randomized jitter:
  $$\text{delay} = \min\left(\text{max\_delay}, \text{base\_delay} \times 2^{(\text{attempt}-1)}\right) + \text{jitter}$$

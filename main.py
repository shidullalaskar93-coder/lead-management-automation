"""Main Executable Runner and Demonstration CLI for Lead Management Automation.

Demonstrates the entire lifecycle:
1. Inbound Ingestion (Webhook JSON / CSV).
2. Pydantic Validation & Normalization.
3. Dynamic Lead Scoring (Firmographics, Intent, Budget).
4. Smart SDR Routing (Round-Robin & Weighted Allocation).
5. SLA Follow-Up Scheduling (15-min Hot, 4-hr Warm, 24-hr Drip).
6. Deduplication Detection.
7. Resilience & Dead-Letter Queue (DLQ) Logging.
8. External CRM Sync with Exponential Backoff Retries.
"""

import argparse
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sys
from typing import Any, Dict, List

# Add workspace root to sys.path
WORKSPACE_ROOT = Path(__file__).resolve().parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from config.settings import SDRConfig, get_settings
from src.crm_client import MockCRMClient
from src.models import AssignmentRule, LeadModel, DeadLetterQueueModel, AuditLogModel, AssignmentModel
from src.pipeline import BatchPipelineSummary, LeadAutomationPipeline, PipelineResult
from src.routing import SDRRouter
from src.utils import get_db_session_factory, load_leads_from_csv, load_leads_from_json

# Ensure proper UTF-8 handling on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("lead_pipeline.cli")


# Terminal Colors / Helpers
class Term:
    BLUE = "\033[94m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def print_header(title: str):
    line = "=" * 78
    print(f"\n{Term.BOLD}{Term.CYAN}{line}{Term.RESET}")
    print(f"{Term.BOLD}{Term.CYAN} {title.center(76)} {Term.RESET}")
    print(f"{Term.BOLD}{Term.CYAN}{line}{Term.RESET}\n")


def print_step(step_num: int, step_title: str):
    print(f"\n{Term.BOLD}{Term.YELLOW}[STEP {step_num}] {step_title}{Term.RESET}")
    print("-" * 65)


def display_pipeline_result(res: PipelineResult):
    if res.success:
        tier_color = Term.RED if res.score_tier == "HOT" else Term.YELLOW if res.score_tier == "WARM" else Term.BLUE
        print(
            f"  {Term.GREEN}[OK] [PROCESSED]{Term.RESET} ID: #{res.lead_id:<3} | "
            f"Company: {res.company:<22} | "
            f"Score: {tier_color}{res.score:>3} ({res.score_tier:<4}){Term.RESET} | "
            f"SDR: {res.assigned_sdr:<14} | "
            f"SLA: {res.sla_deadline}"
        )
        print(f"    {Term.CYAN}--> SLA Action:{Term.RESET} {res.sla_action}")
        if res.crm_external_id:
            print(f"    {Term.MAGENTA}--> CRM Sync Receipt:{Term.RESET} {res.crm_external_id}")
    else:
        status_color = Term.YELLOW if res.status == "DUPLICATE" else Term.RED
        print(
            f"  {status_color}[FAIL] [{res.status}]{Term.RESET} "
            f"Company: {res.company or '<unknown>':<18} | "
            f"Email: {res.email or '<unknown>':<24} | "
            f"Reason: {res.error_message}"
        )


def display_batch_summary(summary: BatchPipelineSummary):
    print("\n" + "=" * 65)
    print(f"{Term.BOLD}BATCH EXECUTION SUMMARY{Term.RESET}")
    print("=" * 65)
    print(f"  • Total Ingested : {summary.total_ingested}")
    print(f"  • Succeeded      : {Term.GREEN}{summary.total_success}{Term.RESET}")
    print(f"  • Duplicates     : {Term.YELLOW}{summary.total_duplicates}{Term.RESET}")
    print(f"  • Failed (DLQ)   : {Term.RED}{summary.total_failed}{Term.RESET}")
    print(f"  • Success Rate   : {Term.BOLD}{summary.success_rate:.1f}%{Term.RESET}")
    print("=" * 65)


def run_comprehensive_demo():
    """Run an interactive demonstration of all enterprise requirements."""
    print_header("ENTERPRISE LEAD MANAGEMENT AUTOMATION SYSTEM")
    print(f"{Term.BOLD}System Initialized with SQLite Database & Dead-Letter Queue.{Term.RESET}")

    settings = get_settings()
    pipeline = LeadAutomationPipeline(settings=settings)

    # -------------------------------------------------------------
    # Scenario 1: Processing Valid Inbound Leads of Various Tiers
    # -------------------------------------------------------------
    print_step(1, "Inbound Ingestion, Pydantic Validation & Scoring (Hot, Warm, Cold)")

    sample_leads = [
        {
            "first_name": "Alexander",
            "last_name": "Pierce",
            "email": "a.pierce@worldsecurity.io",
            "company": "World Security Systems",
            "phone": "+1 (555) 789-0123",
            "industry": "Enterprise Software",
            "company_size": "1000+",
            "budget": "$150,000",
            "source": "WEBSITE",
            "intent_signal": "REQUEST_DEMO",
        },
        {
            "first_name": "Natasha",
            "last_name": "Romanoff",
            "email": "natasha.r@shadowfin.com",
            "company": "ShadowFin Technologies",
            "phone": "555-432-8765",
            "industry": "Fintech",
            "company_size": "201-500",
            "budget": "65000",
            "source": "LINKEDIN",
            "intent_signal": "CONTACT_SALES",
        },
        {
            "first_name": "Peter",
            "last_name": "Parker",
            "email": "peter.p@dailybugle.net",
            "company": "Daily Bugle Media",
            "phone": "+1 555-111-2233",
            "industry": "Retail",
            "company_size": "1-10",
            "budget": "2000",
            "source": "ORGANIC_SEARCH",
            "intent_signal": "NEWSLETTER_SIGNUP",
        },
    ]

    for lead_data in sample_leads:
        res = pipeline.process_lead(lead_data, assignment_rule=AssignmentRule.TIER_BASED)
        display_pipeline_result(res)

    # -------------------------------------------------------------
    # Scenario 2: Deduplication Detection
    # -------------------------------------------------------------
    print_step(2, "Deduplication Detection & Safeguard")
    print(f"Re-submitting duplicate lead for '{sample_leads[0]['email']}' within 30-day window...")
    dup_res = pipeline.process_lead(sample_leads[0], strict_dedup=True)
    display_pipeline_result(dup_res)

    # -------------------------------------------------------------
    # Scenario 3: Invalid Leads & Dead-Letter Queue (DLQ)
    # -------------------------------------------------------------
    print_step(3, "Invalid Lead Handling & Dead-Letter Queue (DLQ) Serialization")
    invalid_leads = [
        {
            "first_name": "Bad",
            "last_name": "Email",
            "email": "invalid-format-no-domain",
            "company": "Broken Ventures",
            "phone": "+15550001111",
        },
        {
            "first_name": "Missing",
            "last_name": "Company",
            "email": "valid.email@orphan.com",
            "company": "",  # Empty mandatory company field
            "phone": "+15550002222",
        },
        {
            "first_name": "Short",
            "last_name": "Phone",
            "email": "short.phone@testing.org",
            "company": "Telecom Testing",
            "phone": "123",  # Malformed phone (< 7 digits)
        },
    ]

    for bad_lead in invalid_leads:
        bad_res = pipeline.process_lead(bad_lead)
        display_pipeline_result(bad_res)

    print(f"\n{Term.CYAN}Inspecting Dead-Letter Queue file: {settings.dlq_storage_file}{Term.RESET}")
    if settings.dlq_storage_file.exists():
        with open(settings.dlq_storage_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
            print(f"  -> Total records currently in DLQ JSONL: {Term.BOLD}{len(lines)}{Term.RESET}")
            if lines:
                latest = json.loads(lines[-1])
                print(f"  -> Latest DLQ Error Type: {Term.RED}{latest.get('error_type')}{Term.RESET}")
                print(f"  -> Latest DLQ Error Msg : {latest.get('error_message')}")

    # -------------------------------------------------------------
    # Scenario 4: SDR Routing Parity (Round-Robin & Weighted)
    # -------------------------------------------------------------
    print_step(4, "SDR Routing Parity & Weighted Distribution Inspection")
    print("Simulating 6 rapid inbound warm leads across the SDR pool...")
    for i in range(1, 7):
        rapid_lead = {
            "first_name": f"Prospect_{i}",
            "last_name": "Enterprise",
            "email": f"prospect_{i}_{datetime.now().microsecond}@corp{i}.com",
            "company": f"Corporation_{i}",
            "phone": f"+1555000{1000 + i}",
            "industry": "SaaS",
            "company_size": "201-500",
            "budget": 45000,
            "intent_signal": "PRICING_PAGE",
        }
        res = pipeline.process_lead(rapid_lead, assignment_rule=AssignmentRule.TIER_BASED)
        display_pipeline_result(res)

    print("\nCurrent SDR Assignment Workload Distribution:")
    for sdr_id, count in pipeline.router.assignment_counts.items():
        sdr_meta = next((s for s in settings.sdrs if s.id == sdr_id), None)
        name = sdr_meta.name if sdr_meta else sdr_id
        tier = sdr_meta.tier if sdr_meta else "N/A"
        weight = sdr_meta.weight if sdr_meta else 1
        print(f"  • {Term.BOLD}{name:<16}{Term.RESET} (Tier: {tier:<4} | Weight: {weight}) -> {Term.GREEN}{count} leads{Term.RESET}")

    # -------------------------------------------------------------
    # Scenario 5: External CRM Sync with Exponential Backoff
    # -------------------------------------------------------------
    print_step(5, "Resilient CRM Sync with Exponential Backoff on Transient 503 Errors")
    print("Configuring mock CRM client to simulate 2 network drops before 3rd attempt success...")
    resilient_crm = MockCRMClient(settings=settings, force_failure_count=2)
    resilient_pipeline = LeadAutomationPipeline(
        settings=settings,
        crm_client=resilient_crm,
        sdr_router=pipeline.router,
    )

    lead_with_retry = {
        "first_name": "Tony",
        "last_name": "Stark",
        "email": f"tony.stark.{datetime.now().microsecond}@starkindustries.com",
        "company": "Stark Industries",
        "phone": "+1 555-888-9999",
        "industry": "Enterprise Software",
        "company_size": "1000+",
        "budget": 500000,
        "intent_signal": "REQUEST_DEMO",
    }
    retry_res = resilient_pipeline.process_lead(lead_with_retry, sync_crm=True)
    display_pipeline_result(retry_res)

    # -------------------------------------------------------------
    # Scenario 6: Database State & Audit Trail Inspection
    # -------------------------------------------------------------
    print_step(6, "Relational Database & Audit Log Verification")
    session = pipeline.session_factory()
    try:
        total_leads = session.query(LeadModel).count()
        total_assignments = session.query(AssignmentModel).count()
        total_audits = session.query(AuditLogModel).count()
        total_dlq = session.query(DeadLetterQueueModel).count()

        print(f"  • Database Records in 'leads' table            : {Term.BOLD}{total_leads}{Term.RESET}")
        print(f"  • Database Records in 'assignments' table      : {Term.BOLD}{total_assignments}{Term.RESET}")
        print(f"  • Database Records in 'audit_logs' table        : {Term.BOLD}{total_audits}{Term.RESET}")
        print(f"  • Database Records in 'dead_letter_queue' table: {Term.BOLD}{total_dlq}{Term.RESET}")

        print("\nSample Audit Trail for Lead #1:")
        first_lead_audits = (
            session.query(AuditLogModel)
            .filter(AuditLogModel.lead_id == 1)
            .order_by(AuditLogModel.id)
            .all()
        )
        for audit in first_lead_audits:
            print(
                f"    [{audit.timestamp.strftime('%H:%M:%S')}] {Term.CYAN}{audit.action:<26}{Term.RESET} "
                f"Stage: {audit.stage_from or 'None'} -> {audit.stage_to or 'None'} "
                f"(Actor: {audit.actor})"
            )
    finally:
        session.close()

    print_header("DEMONSTRATION COMPLETED SUCCESSFULLY")


def process_csv_file(csv_path: str):
    """Batch process leads from a CSV file."""
    print_header(f"PROCESSING CSV BATCH: {csv_path}")
    settings = get_settings()
    pipeline = LeadAutomationPipeline(settings=settings)

    raw_leads = load_leads_from_csv(csv_path)
    print(f"Loaded {len(raw_leads)} lead records from CSV.\n")

    summary = pipeline.process_batch(raw_leads)
    for res in summary.results:
        display_pipeline_result(res)

    display_batch_summary(summary)


def process_json_file(json_path: str):
    """Batch process leads from a JSON file."""
    print_header(f"PROCESSING JSON INBOUND: {json_path}")
    settings = get_settings()
    pipeline = LeadAutomationPipeline(settings=settings)

    raw_leads = load_leads_from_json(json_path)
    print(f"Loaded {len(raw_leads)} lead records from JSON.\n")

    summary = pipeline.process_batch(raw_leads)
    for res in summary.results:
        display_pipeline_result(res)

    display_batch_summary(summary)


def display_system_stats():
    """Query and print live pipeline and CRM metrics."""
    print_header("LEAD PIPELINE OPERATIONAL METRICS")
    settings = get_settings()
    pipeline = LeadAutomationPipeline(settings=settings)
    session = pipeline.session_factory()

    try:
        total = session.query(LeadModel).count()
        hot = session.query(LeadModel).filter(LeadModel.score_tier == "HOT").count()
        warm = session.query(LeadModel).filter(LeadModel.score_tier == "WARM").count()
        cold = session.query(LeadModel).filter(LeadModel.score_tier == "COLD").count()
        dlq_count = session.query(DeadLetterQueueModel).count()

        print(f"  • Total Active Pipeline Leads : {Term.BOLD}{total}{Term.RESET}")
        print(f"  • Hot Tier Leads (15-min SLA) : {Term.RED}{hot}{Term.RESET} ({(hot/total*100) if total else 0:.1f}%)")
        print(f"  • Warm Tier Leads (4-hr SLA)  : {Term.YELLOW}{warm}{Term.RESET} ({(warm/total*100) if total else 0:.1f}%)")
        print(f"  • Cold Tier Leads (Drip Nurture): {Term.BLUE}{cold}{Term.RESET} ({(cold/total*100) if total else 0:.1f}%)")
        print(f"  • Dead-Letter Queue (DLQ) Open: {Term.MAGENTA}{dlq_count}{Term.RESET}")
    finally:
        session.close()


def main():
    parser = argparse.ArgumentParser(
        description="Lead Management Automation - Production Pipeline CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run comprehensive end-to-end demonstration scenario",
    )
    parser.add_argument(
        "--csv",
        type=str,
        help="Path to CSV file containing batch leads to ingest",
    )
    parser.add_argument(
        "--json",
        type=str,
        help="Path to JSON file containing inbound leads to ingest",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Display operational CRM pipeline health statistics",
    )

    args = parser.parse_args()

    if args.csv:
        process_csv_file(args.csv)
    elif args.json:
        process_json_file(args.json)
    elif args.stats:
        display_system_stats()
    else:
        # Default action: run full demo
        run_comprehensive_demo()


if __name__ == "__main__":
    main()

import json
import time
from pathlib import Path

from context.context_builder import ContextBuilder
from llm.prompt_builder import PromptBuilder
from llm.llm_engine import LLMEngine
from analyzer.report_writer import ReportWriter

from utils.archive_manager import archive_current_run
from utils.dashboard_export import export as export_dashboard_feed
from utils.execution_summary import ExecutionSummary
from utils.logger import get_logger

from config.settings import (
    MAX_RETRIES,
    INITIAL_RETRY_DELAY,
    REQUEST_DELAY
)

logger = get_logger("Analyzer")

# Per-resource-type eligibility, used only by run_all() (Full
# Investigation). run() (single-resource) is unaffected: a user explicitly
# picking one resource gets it analyzed regardless of state.

# EC2: a stopped/pending/stopping instance can still be meaningfully RCA'd;
# shutting-down/terminated ones can't - skipping those saves collector-
# adjacent work and Gemini tokens on resources that won't produce a useful
# report.
ELIGIBLE_STATES = {"running", "stopped", "pending", "stopping"}

# Load Balancer: collector/alb.py records alb["State"]["Code"] straight from
# describe_load_balancers(), whose only documented values are active,
# provisioning, active_impaired, failed - there is no "deleted" code,
# because a deleted ALB simply stops being returned by that call at all, so
# there's nothing further to filter for that case. "active" and
# "active_impaired" both mean the ALB currently exists and is
# serving/attempting to serve traffic; "provisioning" (not yet up) and
# "failed" (never came up) have no real metrics or target health worth a
# Gemini call.
ELIGIBLE_ALB_STATES = {"active", "active_impaired"}

# Auto Scaling Group: describe_auto_scaling_groups() (collector/
# autoscaling.py) has no lifecycle "state" field for the group itself -
# unlike EC2/ALB, an ASG doesn't have a provisioning/active/deleting
# concept in that API. A deleted ASG simply stops appearing in the
# response, so every ASG context file that exists already represents a
# currently-active group. There is no state to check, so every discovered
# ASG is eligible - see _resource_eligibility() below.

# Skip-reason labels, used only for the log line / skipped_resources entry.
_RESOURCE_LABELS = {
    "EC2": "Instance",
    "Load Balancer": "Load Balancer",
    "Auto Scaling Group": "Auto Scaling Group",
}


def _resource_eligibility(context):
    """Resource-type-aware eligibility check for Full Investigation.

    Returns (eligible: bool, state_label: str). Each resource type is
    judged only against the state/data its own collector already
    produces - see the constants above for why each type's rule is what
    it is. Never invents a state that isn't already in the collector
    output.
    """

    resource_type = context.get("resource_type")
    data = context.get("context") or {}

    if resource_type == "Load Balancer":
        state = str(data.get("state") or "").strip().lower()
        return state in ELIGIBLE_ALB_STATES, state or "unknown"

    if resource_type == "Auto Scaling Group":
        return True, "active"

    # Default: EC2.
    cloudwatch_ctx = data.get("cloudwatch") or {}
    state = str(cloudwatch_ctx.get("State") or "").strip().lower()
    return state in ELIGIBLE_STATES, state or "unknown"


class Analyzer:

    def __init__(self):

        self.builder = ContextBuilder()
        self.prompt = PromptBuilder()
        self.llm = LLMEngine()
        self.report = ReportWriter()

    def run(self, context_file, on_progress=None, resource_id=None, run_id=None):
        """resource_id scopes context building to that one resource (see
        ContextBuilder.run_for_resource) instead of rebuilding every
        resource's context just to analyze one - used by
        api/investigation_manager.py's single-resource investigation.
        run_all() never sets this; its own context build stays untouched.

        run_id, when passed, scopes ContextBuilder's collector-output
        freshness check to this investigation (see
        ContextBuilder._parse_run_started_at)."""

        if on_progress:
            on_progress("BUILDING_CONTEXT", 15)

        if resource_id:
            self.builder.run_for_resource(resource_id, run_id=run_id)
        else:
            self.builder.run(run_id=run_id)

        context = self.prompt.load_context(context_file)

        instance_id = context["instance_id"]

        if on_progress:
            on_progress("RUNNING_AI_ANALYSIS", 45, instance_id)

        prompt = self.prompt.build_prompt(context)

        response = self.llm.analyze(prompt)

        if on_progress:
            on_progress("GENERATING_RCA", 80, instance_id)

        try:
            report = json.loads(response)
        except Exception:
            report = {"raw_response": response}

        self.report.save(instance_id, report)

        return report

    def run_all(self, run_id=None, on_progress=None):

        logger.info("====================================================")
        logger.info("AI Analysis Started")
        logger.info("====================================================")

        summary = ExecutionSummary(run_id=run_id)

        if on_progress:
            on_progress("BUILDING_CONTEXT", 12)

        self.builder.run(run_id=run_id)

        context_dir = Path("output/context")

        # output/context/ holds one file per discovered resource - EC2
        # instances, and now also first-class Load Balancer / Auto Scaling
        # Group resources (context/context_builder.py). Full Investigation
        # analyzes every supported type; eligibility is decided per-resource
        # below via _resource_eligibility(), not by resource_type filtering.
        context_files = sorted(context_dir.glob("*.json"))

        summary.instances_discovered = len(context_files)

        logger.info(f"Found {len(context_files)} context file(s)")

        total_files = len(context_files) or 1

        for index, context_file in enumerate(context_files):

            context = self.prompt.load_context(context_file.name)

            instance_id = context["instance_id"]
            resource_type = context.get("resource_type")

            eligible, state = _resource_eligibility(context)

            if not eligible:

                label = _RESOURCE_LABELS.get(resource_type, "Resource")
                reason = f"{label} state '{state}' is not eligible for analysis"

                summary.skipped += 1
                summary.skipped_resources.append({
                    "instance_id": instance_id,
                    "resource_type": resource_type,
                    "state": state,
                    "reason": reason,
                })

                logger.info(f"Skipping {instance_id}: {reason}")

                if on_progress:
                    on_progress("RUNNING_AI_ANALYSIS", 20 + int(60 * (index / total_files)), instance_id)

                continue

            prompt = self.prompt.build_prompt(context)

            summary.instances_analyzed += 1

            if on_progress:
                on_progress("RUNNING_AI_ANALYSIS", 20 + int(60 * (index / total_files)), instance_id)

            delay = INITIAL_RETRY_DELAY

            success = False

            for attempt in range(1, MAX_RETRIES + 1):

                try:

                    logger.info(
                        f"Analyzing {instance_id} (Attempt {attempt}/{MAX_RETRIES})"
                    )

                    response = self.llm.analyze(prompt)

                    try:
                        report = json.loads(response)
                    except Exception:
                        report = {"raw_response": response}

                    self.report.save(instance_id, report)

                    summary.successful += 1
                    summary.reports_generated += 1

                    logger.info(
                        f"Analysis completed successfully for {instance_id}"
                    )

                    success = True

                    break

                except Exception as e:

                    logger.error(
                        f"Attempt {attempt}/{MAX_RETRIES} failed for {instance_id}"
                    )

                    logger.exception(e)

                    summary.retries += 1

                    if attempt < MAX_RETRIES:

                        logger.info(
                            f"Retrying in {delay} seconds..."
                        )

                        time.sleep(delay)

                        delay *= 2

            if not success:

                summary.failed += 1

                logger.error(
                    f"Skipping {instance_id} after {MAX_RETRIES} failed attempts."
                )

            time.sleep(REQUEST_DELAY)

        logger.info("====================================================")
        logger.info("AI Analysis Completed")
        logger.info("====================================================")

        if on_progress:
            on_progress("GENERATING_RCA", 85)

        summary.archive_created = True

        summary.save()

        archive_current_run()

        if on_progress:
            on_progress("PUBLISHING_DASHBOARD", 95)
        logger.info("====================================================")
        logger.info("Generating Dashboard Feed")
        logger.info("====================================================")

        try:

            export_dashboard_feed()

            logger.info(
                "Dashboard feed generated successfully."
            )

        except Exception as e:

            logger.error(
                "Dashboard feed generation failed."
            )

            logger.exception(e)

        if on_progress:
            on_progress("PUBLISHING_DASHBOARD", 100)
        logger.info("====================================================")
        logger.info("AI Investigation Pipeline Finished")
        logger.info("====================================================")

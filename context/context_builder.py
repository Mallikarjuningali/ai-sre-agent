"""
=========================================================
AI SRE AGENT
Module : Context Builder
Author : Mallikarjun
Purpose:
    Read collector outputs, normalize infrastructure data,
    build AI-ready context, and generate one context
    document per resource.
=========================================================
"""

# =========================================================
# Import Required Libraries
# =========================================================

import json
import logging
from pathlib import Path
from typing import Dict, Any

# =========================================================
# Configure Logger
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)

# =========================================================
# Context Builder Class
# =========================================================


class ContextBuilder:
    """
    Context Builder

    Responsibilities:
    -----------------
    1. Load collector JSON outputs.
    2. Validate collector data.
    3. Prepare infrastructure data for AI processing.
    4. Generate one AI-ready context per resource.

    Note:
    -----
    This module DOES NOT perform:

    - Incident Detection
    - Root Cause Analysis
    - Recommendations

    It only prepares structured context for the AI engine.
    """

    # =====================================================
    # Constructor
    # =====================================================

    def __init__(self):

        # -------------------------------------------------
        # Project Root Directory
        # -------------------------------------------------

        self.project_root = Path(__file__).resolve().parent.parent

        # -------------------------------------------------
        # Collector Output Directory
        # -------------------------------------------------

        self.raw_directory = self.project_root / "output" / "raw"

        # -------------------------------------------------
        # AI Context Output Directory
        # -------------------------------------------------

        self.context_directory = self.project_root / "output" / "context"

        # Create context directory if it doesn't exist
        self.context_directory.mkdir(
            parents=True,
            exist_ok=True
        )

        # -------------------------------------------------
        # Collector Output Files
        # -------------------------------------------------

        self.collector_files = {

            "cloudwatch": "cloudwatch.json",

            "alb": "alb.json",

            "autoscaling": "autoscaling.json",

            "cloudtrail": "cloudtrail.json"

        }


        # -------------------------------------------------
        # Dictionary to store collector data
    # -------------------------------------------------

        self.collectors: Dict[str, Any] = {}

# -------------------------------------------------
# Resource Index
# -------------------------------------------------
# Stores all discovered AWS resources.
# This will become the single source of truth
# for the AI engine.
# -------------------------------------------------

        self.resource_index: Dict[str, Any] = {}

        # -------------------------------------------------
        # Log Project Information
        # -------------------------------------------------

        logger.info("=" * 60)
        logger.info("Context Builder Initialized")
        logger.info("=" * 60)

        logger.info(f"Project Root      : {self.project_root}")
        logger.info(f"Raw Directory     : {self.raw_directory}")
        logger.info(f"Context Directory : {self.context_directory}")

    # =====================================================
    # Load JSON File
    # =====================================================

    def load_json(self, filename: str) -> Dict[str, Any]:
        """
        Safely load a JSON file.

        Args:
            filename:
                Name of the JSON file.

        Returns:
            Parsed JSON dictionary.

            Returns empty dictionary if:

            - File doesn't exist
            - Invalid JSON
            - File cannot be opened
        """

        file_path = self.raw_directory / filename

        # -------------------------------------------------
        # Verify File Exists
        # -------------------------------------------------

        if not file_path.exists():

            logger.error(
                f"Collector output not found: {file_path}"
            )

            return {}

        # -------------------------------------------------
        # Read JSON File
        # -------------------------------------------------

        try:

            logger.info(f"Reading {file_path}")

            with open(
                file_path,
                "r",
                encoding="utf-8"
            ) as file:

                return json.load(file)

        except (json.JSONDecodeError, OSError) as error:

            logger.exception(
                f"Failed to load '{filename}' : {error}"
            )

            return {}

    # =====================================================
    # Load Collector Outputs
    # =====================================================

    def load_collectors(self) -> None:
        """
        Load all collector outputs.
        """

        logger.info("=" * 60)
        logger.info("Loading Collector Outputs")
        logger.info("=" * 60)

        for collector, filename in self.collector_files.items():

            self.collectors[collector] = self.load_json(
                filename
            )

            if self.collectors[collector]:

                logger.info(
                    f"{collector} loaded successfully."
                )

            else:

                logger.warning(
                    f"{collector} is empty or could not be loaded."
                )
# =====================================================
# Add Resource
# =====================================================

    def add_resource(self, resource_id: str, resource_type: str = "EC2") -> None:
        """
        Add a resource to the resource index.

        If the resource already exists, nothing will be changed.

        resource_type distinguishes EC2 instances (enriched piecemeal by the
        cloudwatch/alb/autoscaling/cloudtrail merges below) from first-class
        Load Balancer / Auto Scaling Group resources, which carry their own
        dedicated shape - see _default_entry().
        """

        if resource_id not in self.resource_index:

            self.resource_index[resource_id] = self._default_entry(resource_type)

    def _default_entry(self, resource_type: str) -> Dict[str, Any]:
        """The initial shape for a new resource_index entry, per resource
        type. Kept as a single source of truth so add_resource() never
        silently produces a mismatched shape."""

        if resource_type == "Load Balancer":
            return {
                "resource_type": resource_type,
                "alb_name": None,
                "dns_name": None,
                "scheme": None,
                "state": None,
                "vpc_id": None,
                "metrics": {},
                "target_groups": [],
            }

        if resource_type == "Auto Scaling Group":
            return {
                "resource_type": resource_type,
                "asg_name": None,
                "min_size": None,
                "max_size": None,
                "desired_capacity": None,
                "health_check_type": None,
                "availability_zones": [],
                "metrics": {},
                "instances": [],
                "scaling_policies": [],
                "scaling_activities": [],
            }

        # Default: EC2 instance.
        return {
            "resource_type": "EC2",
            "cloudwatch": None,
            "alb": None,
            "autoscaling": None,
            "cloudtrail": []
        }

    # =====================================================
    # Build Resource Index
    # =====================================================

    def build_resource_index(self) -> None:
        """
        Build a unified resource index
        using all available collector outputs.
        """

        logger.info("=" * 60)
        logger.info("Building Resource Index")
        logger.info("=" * 60)

        # -------------------------------------------------
        # CloudWatch Resources
        # -------------------------------------------------

        cloudwatch = self.collectors.get("cloudwatch", {})

        for resource in cloudwatch.get("resources", []):

            instance_id = resource.get("InstanceId")

            if not instance_id:
                continue

            # Create resource if it doesn't exist
            self.add_resource(instance_id)

            # Attach CloudWatch data
            self.resource_index[instance_id]["cloudwatch"] = resource

        logger.info(
            f"CloudWatch Resources Found : {len(self.resource_index)}"
        )

        # -------------------------------------------------
        # Merge ALB Data
        # -------------------------------------------------

        alb = self.collectors.get("alb", {})

        for load_balancer in alb.get("resources", []):

            for target_group in load_balancer.get("target_groups", []):

                for target in target_group.get("targets", []):

                    instance_id = target.get("instance_id")

                    if not instance_id:
                        continue

                    self.add_resource(instance_id)

                    self.resource_index[instance_id]["alb"] = {

                        "alb_name": load_balancer.get("alb_name"),

                        "target_group": target_group.get("target_group_name"),

                        "health": target.get("health"),

                        "reason": target.get("reason"),

                        "description": target.get("description"),

                        "port": target.get("port")

                    }

        logger.info("ALB data merged successfully.")

        # -------------------------------------------------
        # Promote each ALB to its own first-class resource
        # (the loop above only ever used ALB data to enrich the
        # EC2 instances behind it - this is what makes the ALB
        # itself show up as "Load Balancer" in resources.json).
        # -------------------------------------------------

        for load_balancer in alb.get("resources", []):

            alb_name = load_balancer.get("alb_name")

            if not alb_name:
                continue

            self.add_resource(alb_name, resource_type="Load Balancer")

            entry = self.resource_index[alb_name]

            entry["alb_name"] = alb_name
            entry["dns_name"] = load_balancer.get("dns_name")
            entry["scheme"] = load_balancer.get("scheme")
            entry["state"] = load_balancer.get("state")
            entry["vpc_id"] = load_balancer.get("vpc_id")
            entry["metrics"] = load_balancer.get("metrics", {})
            entry["target_groups"] = [
                {
                    "target_group_name": tg.get("target_group_name"),
                    "protocol": tg.get("protocol"),
                    "port": tg.get("port"),
                    "health_check_path": tg.get("health_check_path"),
                    "healthy_hosts": tg.get("healthy_hosts"),
                    "unhealthy_hosts": tg.get("unhealthy_hosts"),
                    "targets": tg.get("targets", []),
                }
                for tg in load_balancer.get("target_groups", [])
            ]

        logger.info("Load Balancers promoted to first-class resources.")

                # -------------------------------------------------
        # Merge Auto Scaling Data
        # -------------------------------------------------

        autoscaling = self.collectors.get("autoscaling", {})

        for asg in autoscaling.get("resources", []):

            for instance in asg.get("instances", []):

                instance_id = instance.get("instance_id")

                if not instance_id:
                    continue

                self.add_resource(instance_id)

                self.resource_index[instance_id]["autoscaling"] = {

                    "asg_name": asg.get("asg_name"),

                    "health_status": instance.get("health_status"),

                    "lifecycle_state": instance.get("lifecycle_state"),

                    "availability_zone": instance.get("availability_zone"),

                    "instance_type": instance.get("instance_type")

                }

        logger.info("Auto Scaling data merged successfully.")

        # -------------------------------------------------
        # Promote each Auto Scaling Group to its own first-class
        # resource (the loop above only ever used ASG data to enrich
        # its member EC2 instances).
        # -------------------------------------------------

        for asg in autoscaling.get("resources", []):

            asg_name = asg.get("asg_name")

            if not asg_name:
                continue

            self.add_resource(asg_name, resource_type="Auto Scaling Group")

            entry = self.resource_index[asg_name]

            entry["asg_name"] = asg_name
            entry["min_size"] = asg.get("min_size")
            entry["max_size"] = asg.get("max_size")
            entry["desired_capacity"] = asg.get("desired_capacity")
            entry["health_check_type"] = asg.get("health_check_type")
            entry["availability_zones"] = asg.get("availability_zones", [])
            entry["metrics"] = asg.get("metrics", {})
            entry["instances"] = asg.get("instances", [])
            entry["scaling_policies"] = asg.get("scaling_policies", [])
            entry["scaling_activities"] = asg.get("scaling_activities", [])

        logger.info("Auto Scaling Groups promoted to first-class resources.")

                # -------------------------------------------------
        # Merge CloudTrail Data
        # -------------------------------------------------

        cloudtrail = self.collectors.get("cloudtrail", {})

        for event in cloudtrail.get("resources", []):

            for resource in event.get("resources", []):

                if resource.get("type") != "EC2 Instance":
                    continue

                instance_id = resource.get("name")

                if not instance_id:
                    continue

                self.add_resource(instance_id)

                self.resource_index[instance_id]["cloudtrail"].append({

                    "event_time": event.get("event_time"),

                    "event_name": event.get("event_name"),

                    "service": event.get("service"),

                    "username": event.get("username"),

                    "source_ip": event.get("source_ip"),

                    "read_only": event.get("read_only"),

                    "error_code": event.get("error_code")

                })

        logger.info("CloudTrail data merged successfully.")

        # =====================================================
        # =====================================================
    # Remove Stale Context Files
    # =====================================================

    def _cleanup_stale_context_files(self) -> None:
        """
        Deletes output/context/<id>.json for any resource that isn't in
        the resource index this method is called with (built fresh from
        this run's own collector data) - so a resource that no longer
        exists in AWS (terminated instance, deleted ALB/ASG) stops
        appearing in resources.json instead of lingering forever.

        Only ever touches self.context_directory (output/context/).
        output/raw/ and output/archive/ are never referenced here -
        historical investigations are untouched.
        """

        if not self.context_directory.exists():
            return

        current_ids = set(self.resource_index.keys())

        removed = 0

        for existing_file in self.context_directory.glob("*.json"):

            if existing_file.stem not in current_ids:

                existing_file.unlink()

                removed += 1

                logger.info(f"Removed stale context file: {existing_file.name}")

        if removed:
            logger.info(f"Cleaned up {removed} stale context file(s).")

    # =====================================================
    # Save AI Context Files
    # =====================================================

    def save_context(self) -> None:
        """
        Save one AI-ready JSON file per resource.
        """

        logger.info("=" * 60)
        logger.info("Saving AI Context Files")
        logger.info("=" * 60)

        for instance_id, context in self.resource_index.items():

            file_path = (
                self.context_directory /
                f"{instance_id}.json"
            )

            with open(
                file_path,
                "w",
                encoding="utf-8"
            ) as file:

                ai_context = {

                    "instance_id": instance_id,

                    "resource_type": context.get("resource_type"),

                    "generated_by": "AI-SRE-Agent",

                    "collector_count": 4,

                    "context": context

                }

                json.dump(
                    ai_context,
                    file,
                    indent=4
                )

            logger.info(
                f"Saved : {file_path.name}"
            )

        logger.info(
            f"Generated {len(self.resource_index)} context files."
        )
        # =====================================================
    # Execute Context Builder
    # =====================================================

    def run(self) -> None:
        """
        Execute Context Builder workflow.
        """

        logger.info("=" * 60)
        logger.info("Starting Context Builder")
        logger.info("=" * 60)

        # Step 1
        self.load_collectors()

        logger.info("Collector outputs loaded successfully.")

        # Step 2
        self.build_resource_index()

        # Step 3
        self._cleanup_stale_context_files()

        # Step 4
        self.save_context()

        logger.info("=" * 60)
        logger.info("Context Builder completed successfully.")
        logger.info("=" * 60)

    # =====================================================
    # Execute Context Builder - single resource
    # =====================================================

    def run_for_resource(self, instance_id: str) -> None:
        """
        Same pipeline as run(), but narrows the resource index down to one
        instance right before saving - so a resource-scoped investigation
        (api/investigation_manager.py) writes exactly one context file
        instead of one per discovered resource. load_collectors() and
        build_resource_index() are reused unchanged; only what gets passed
        to save_context() differs. Stale context files are still pruned
        fleet-wide (see _cleanup_stale_context_files) since the collectors
        just ran fresh for the whole account regardless of which single
        resource is about to be analyzed.

        Raises ValueError if the resource isn't present in the just-loaded
        collector data.
        """

        logger.info("=" * 60)
        logger.info(f"Starting Context Builder (single resource: {instance_id})")
        logger.info("=" * 60)

        # Step 1
        self.load_collectors()

        # Step 2
        self.build_resource_index()

        if instance_id not in self.resource_index:
            raise ValueError(f"Resource '{instance_id}' not found in collected data")

        # Prune stale files using the full fleet-wide index (collectors
        # just ran fresh for the whole account), before narrowing down to
        # the one resource actually being analyzed below.
        self._cleanup_stale_context_files()

        self.resource_index = {instance_id: self.resource_index[instance_id]}

        # Step 3
        self.save_context()

        logger.info("=" * 60)
        logger.info("Context Builder completed successfully (single resource).")
        logger.info("=" * 60)
# =========================================================
# Main Function
# =========================================================


if __name__ == "__main__":

    builder = ContextBuilder()

    builder.run()

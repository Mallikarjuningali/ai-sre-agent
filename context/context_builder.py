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

    def add_resource(self, instance_id: str) -> None:
        """
        Add a resource to the resource index.

        If the resource already exists,
        nothing will be changed.
        """

        if instance_id not in self.resource_index:

            self.resource_index[instance_id] = {

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
        self.save_context()

        logger.info("=" * 60)
        logger.info("Context Builder completed successfully.")
        logger.info("=" * 60)
# =========================================================
# Main Function
# =========================================================


if __name__ == "__main__":

    builder = ContextBuilder()

    builder.run()

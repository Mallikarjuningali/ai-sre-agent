"""
=========================================================
AI SRE AGENT
Module : CloudTrail Collector
Author : Mallikarjun

Purpose:
    Collect CloudTrail events for AI Root Cause Analysis.
=========================================================
"""

# =========================================================
# Import Required Libraries
# =========================================================
import json
import re

from datetime import datetime, timedelta, UTC

from utils.writer import write_json
from utils.aws_clients import get_cloudtrail_client
from utils.logger import get_logger
from config.settings import METRIC_LOOKBACK_MINUTES

# =========================================================
# Logger
# =========================================================

logger = get_logger(__name__)

# =========================================================
# AWS Client
# =========================================================

cloudtrail = get_cloudtrail_client()
# =========================================================
# Extract Resources
# =========================================================


RESOURCE_PATTERNS = {

    "EC2 Instance": r"i-[0-9a-f]{8,17}",

    "Security Group": r"sg-[0-9a-f]{8,17}",

    "VPC": r"vpc-[0-9a-f]{8,17}",

    "Subnet": r"subnet-[0-9a-f]{8,17}",

    "Volume": r"vol-[0-9a-f]{8,17}",

    "Network Interface": r"eni-[0-9a-f]{8,17}",

    "Internet Gateway": r"igw-[0-9a-f]{8,17}",

    "NAT Gateway": r"nat-[0-9a-f]{8,17}",

    "Auto Scaling": r"arn:aws:autoscaling:[^\s\"]+",

    "Load Balancer": r"arn:aws:elasticloadbalancing:[^\s\"]+",

    "IAM": r"arn:aws:iam:[^\s\"]+"

}


def extract_resources(cloudtrail_event):

    resources = []

    visited = set()

    def search(value):

        if isinstance(value, dict):

            for v in value.values():
                search(v)

        elif isinstance(value, list):

            for item in value:
                search(item)

        elif isinstance(value, str):

            for resource_type, pattern in RESOURCE_PATTERNS.items():

                matches = re.findall(pattern, value)

                for match in matches:

                    key = (resource_type, match)

                    if key not in visited:

                        visited.add(key)

                        resources.append({

                            "type": resource_type,

                            "name": match

                        })

    search(cloudtrail_event)

    return resources
# =========================================================
# Parse CloudTrail Event
# =========================================================

def parse_cloudtrail_event(event):

    cloudtrail_event = json.loads(
        event["CloudTrailEvent"]
    )

    parsed_event = {

        "event_time": str(event["EventTime"]),

        "event_name": event["EventName"],

        "username": event.get(
            "Username",
            "N/A"
        ),

        "service": cloudtrail_event.get(
            "eventSource",
            "N/A"
        ),

        "region": cloudtrail_event.get(
            "awsRegion",
            "N/A"
        ),

        "source_ip": cloudtrail_event.get(
            "sourceIPAddress",
            "N/A"
        ),

        "user_agent": cloudtrail_event.get(
            "userAgent",
            "N/A"
        ),
       "resources": extract_resources(
           cloudtrail_event
        ),
        "read_only": cloudtrail_event.get(
            "readOnly",
            False
        ),

        "error_code": cloudtrail_event.get(
            "errorCode",
            ""
        ),

        "error_message": cloudtrail_event.get(
            "errorMessage",
            ""
        )
    }

    return parsed_event
# =========================================================
# Discover CloudTrail Events
# =========================================================

def discover_events():

    logger.info("Collecting CloudTrail Events...")

    end_time = datetime.now(UTC)

    start_time = end_time - timedelta(
        minutes=METRIC_LOOKBACK_MINUTES
    )

    response = cloudtrail.lookup_events(
        StartTime=start_time,
        EndTime=end_time,
        MaxResults=50
    )

    return response["Events"]
# =========================================================
# Main Function
# =========================================================

def main():

    events = discover_events()

    print(f"\nTotal Events : {len(events)}\n")

    for event in events:

        print(event["EventName"])

# =========================================================
# Main Function
# =========================================================

def main():

    events = discover_events()

    print(f"\nTotal Events : {len(events)}\n")

    parsed_events = []

    for event in events:

        parsed = parse_cloudtrail_event(event)

        parsed_events.append(parsed)

        print("=" * 70)

        print(f"Time        : {parsed['event_time']}")
        print(f"Event       : {parsed['event_name']}")
        print(f"User        : {parsed['username']}")
        print(f"Service     : {parsed['service']}")
        print(f"Region      : {parsed['region']}")
        print(f"Source IP   : {parsed['source_ip']}")
        print(f"User Agent  : {parsed['user_agent']}")

        print("\nResources")

        if parsed["resources"]:

            for resource in parsed["resources"]:

                print(
                    f"{resource['type']} : "
                    f"{resource['name']}"
                )

        else:

            print("No Resources")

        print(f"\nRead Only   : {parsed['read_only']}")
        print(f"Error Code  : {parsed['error_code']}")
        print(f"Error Msg   : {parsed['error_message']}")

        print("=" * 70)
        print()

    # =====================================================
    # Save JSON
    # =====================================================

    write_json(
        "cloudtrail.json",
        {
            "collector": "cloudtrail",
            "resources": parsed_events
        }
    )

    print("\nCloudTrail JSON report generated successfully.")


# =========================================================
# Entry Point
# =========================================================

if __name__ == "__main__":
    main()

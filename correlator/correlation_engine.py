"""
=========================================================
AI SRE AGENT
Module : Correlation Engine
Author : Mallikarjun
Purpose:
    Correlate AWS resources using Instance IDs.
=========================================================
"""

# =========================================================
# Imports
# =========================================================

import json
from pathlib import Path

# =========================================================
# JSON Folder
# =========================================================

RAW_PATH = Path("output/raw")

# =========================================================
# Load JSON
# =========================================================

def load_json(filename):

    with open(RAW_PATH / filename) as file:
        return json.load(file)

# =========================================================
# Main
# =========================================================

def main():

    cloudwatch = load_json("cloudwatch.json")
    alb = load_json("alb.json")
    autoscaling = load_json("autoscaling.json")
    cloudtrail = load_json("cloudtrail.json")

    print("\n========== Instance Correlation ==========\n")

    for instance in cloudwatch["resources"]:

        instance_id = instance["InstanceId"]

        print("=" * 70)
        print(f"Instance : {instance_id}")
        print("=" * 70)

        #
        # CloudWatch
        #

        print("\nCloudWatch")

        print(f"CPU        : {instance['CPU']}")
        print(f"Memory     : {instance['Memory']}")
        print(f"Disk       : {instance['Disk']}")
        print(f"Status     : {instance['StatusCheck']}")

        #
        # ALB
        #

        print("\nALB")

        found = False

        for alb_item in alb["resources"]:

            for tg in alb_item["target_groups"]:

                for target in tg["targets"]:

                    if target["instance_id"] == instance_id:

                        found = True

                        print(f"Health : {target['health']}")
                        print(f"Reason : {target['reason']}")

        if not found:

            print("Not registered")

        #
        # Auto Scaling
        #

        print("\nAuto Scaling")

        found = False

        for asg in autoscaling["resources"]:

            for ec2 in asg["instances"]:

                if ec2["instance_id"] == instance_id:

                    found = True

                    print(f"Lifecycle : {ec2['lifecycle_state']}")
                    print(f"Health    : {ec2['health_status']}")

        if not found:

            print("Not in Auto Scaling")

        #
        # CloudTrail
        #

        print("\nCloudTrail")

        count = 0

        for event in cloudtrail["resources"]:

            for resource in event["resources"]:

                if resource["name"] == instance_id:

                    count += 1

                    print(event["event_name"])

        print(f"\nEvents Found : {count}")

        print()

if __name__ == "__main__":
    main()

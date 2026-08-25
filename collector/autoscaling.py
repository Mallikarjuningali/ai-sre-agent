"""
=========================================================
AI SRE AGENT
Module : Auto Scaling Collector
Author : Mallikarjun
Purpose:
    Discover Auto Scaling Groups, Scaling Activities,
    Launch Configurations and collect Auto Scaling
    information for AI Root Cause Analysis.
=========================================================
"""

# =========================================================
# Import Required Libraries
# =========================================================

from datetime import datetime, timedelta, UTC

from botocore.exceptions import BotoCoreError, ClientError

from utils.writer import write_json
from utils.aws_clients import (
    get_autoscaling_client,
    get_cloudwatch_client
)
from utils.logger import get_logger
from utils.metric_stats import build_metric_payload
from utils.alarm_lookup import AlarmLookup
from config.settings import METRIC_LOOKBACK_MINUTES, METRIC_TREND_LOOKBACK_MINUTES, METRIC_TREND_PERIOD_SECONDS
# =========================================================
# Logger
# =========================================================

logger = get_logger(__name__)
# =========================================================
# AWS Configuration
# =========================================================


# =========================================================
# Create AWS Clients
# =========================================================
# =========================================================
# AWS Clients
# =========================================================

autoscaling = get_autoscaling_client()

cloudwatch = get_cloudwatch_client()
# =========================================================
# Discover Auto Scaling Groups
# =========================================================

def discover_auto_scaling_groups():
    """
    Discover all Auto Scaling Groups
    in the AWS account.
    """

    logger.info("Discovering Auto Scaling Groups...")

    asgs = []

    try:

        paginator = autoscaling.get_paginator(
            "describe_auto_scaling_groups"
        )

        for page in paginator.paginate():

            asgs.extend(
                page["AutoScalingGroups"]
            )

        logger.info(
            f"Found {len(asgs)} Auto Scaling Groups"
        )

        return asgs

    except (BotoCoreError, ClientError) as e:

        # A genuine AWS/API failure (throttling, permissions, network,
        # etc.) must not be interpreted as "0 ASGs found" - that would
        # make main() take the empty-discovery path and, previously,
        # skip writing autoscaling.json entirely. Re-raise so the
        # caller (main() -> InvestigationManager._execute_full()) sees
        # this as a real failure instead of a silent, incorrect "no ASGs
        # this run". Unexpected non-AWS exceptions (KeyError, TypeError,
        # etc.) are intentionally not caught here at all - they were
        # never AWS/API failures in the first place and should surface
        # exactly as they would anywhere else in the codebase.
        logger.error(
            f"Failed to discover Auto Scaling Groups: {str(e)}"
        )

        raise


# =========================================================
# Discover Scaling Activities
# =========================================================

def discover_scaling_activities(asg_name):
    """
    Return recent scaling activities
    for an Auto Scaling Group.
    """

    logger.info(
        f"Getting Scaling Activities for {asg_name}"
    )

    try:

        response = autoscaling.describe_scaling_activities(

            AutoScalingGroupName=asg_name,

            MaxRecords=10

        )

        return response["Activities"]

    except Exception as e:

        logger.error(
            f"Scaling Activities: {str(e)}"
        )

        return []


# =========================================================
# Discover Scaling Policies
# =========================================================

def discover_scaling_policies(asg_name):# =========================================================

    """
    Return scaling policies
    for an Auto Scaling Group.
    """

    logger.info(
        f"Getting Scaling Policies for {asg_name}"
    )

    try:

        response = autoscaling.describe_policies(

            AutoScalingGroupName=asg_name

        )

        return response["ScalingPolicies"]

    except Exception as e:

        logger.error(
            f"Scaling Policies: {str(e)}"
        )

        return []


# =========================================================
# Generic CloudWatch Metric Function
# =========================================================

def get_metric(
    metric_name,
    asg_name,
    statistic="Average"
):
    """
    Retrieve Auto Scaling CloudWatch metric.

    Returns:
        float
    """

    try:
        end_time = datetime.now(UTC)
        start_time = end_time - timedelta(minutes=METRIC_LOOKBACK_MINUTES)


        response = cloudwatch.get_metric_statistics(

            Namespace="AWS/AutoScaling",

            MetricName=metric_name,

            Dimensions=[
                {
                    "Name": "AutoScalingGroupName",
                    "Value": asg_name
                }
            ],

            StartTime=start_time,

            EndTime=end_time,

            Period=300,

            Statistics=[statistic]

        )

        datapoints = response["Datapoints"]

        if not datapoints:
            logger.warning(f"No CloudWatch data found for {metric_name}")
            return 0

        latest = max(

            datapoints,

            key=lambda x: x["Timestamp"]

        )

        return round(
            latest[statistic],
            2
        )

    except Exception as e:

        logger.error(
            f"{metric_name}: {str(e)}"
        )

        return 0


# =========================================================
# Generic CloudWatch Metric Statistics (trend-aware)
# =========================================================
# Only Desired Capacity, InService and Pending Instances get trend stats,
# per the metrics this collector is asked to track trends for -
# Terminating/Total Instances (below) keep using get_metric() above,
# unchanged. Scaling activities already carry their own recent-activity
# list (discover_scaling_activities), reused as-is - no separate stats
# needed there.

ASG_NAMESPACE = "AWS/AutoScaling"


def asg_dimensions(asg_name):
    """The one place ASG CloudWatch dimensions are built - used both to
    fetch datapoints and to look up a matching alarm, so the two can never
    diverge."""

    return [
        {
            "Name": "AutoScalingGroupName",
            "Value": asg_name
        }
    ]


def get_metric_datapoints(
    metric_name,
    asg_name,
    statistic="Average"
):
    """Fetches the previous METRIC_TREND_LOOKBACK_MINUTES of a metric at
    METRIC_TREND_PERIOD_SECONDS granularity. Exists only for this call -
    only the statistical summary (get_metric_stats, below) is ever
    persisted, same as every other collector value in output/raw/."""

    try:
        end_time = datetime.now(UTC)
        start_time = end_time - timedelta(minutes=METRIC_TREND_LOOKBACK_MINUTES)

        response = cloudwatch.get_metric_statistics(
            Namespace=ASG_NAMESPACE,
            MetricName=metric_name,
            Dimensions=asg_dimensions(asg_name),
            StartTime=start_time,
            EndTime=end_time,
            Period=METRIC_TREND_PERIOD_SECONDS,
            Statistics=[statistic]
        )

        return [
            {"timestamp": point["Timestamp"], "value": point[statistic]}
            for point in response["Datapoints"]
        ]

    except Exception as e:

        logger.error(f"{metric_name}: {str(e)}")

        return []


def get_metric_stats(
    metric_name,
    asg_name,
    alarm_lookup,
    statistic="Average",
    unit="Count"
):
    """One CloudWatch call -> (latest_value, telemetry_payload).

    latest_value is a plain rounded number (or None) - kept only for the
    existing snapshot fields the raw record already depends on.
    build_metric_payload() returns {"U", "TH", "H"} only - no interpreted
    or derived values, not even "latest".

    TH is never hardcoded: alarm_lookup.find_threshold() is queried with
    the same ASG_NAMESPACE + asg_dimensions(asg_name) used to fetch the
    datapoints, and only returns a value when a real CloudWatch Alarm
    matches it.

    Both are None/None when there was no data in the window - callers
    should degrade gracefully."""

    datapoints = get_metric_datapoints(metric_name, asg_name, statistic)

    if not datapoints:
        return None, None

    latest_point = max(datapoints, key=lambda point: point["timestamp"])

    threshold = alarm_lookup.find_threshold(ASG_NAMESPACE, metric_name, asg_dimensions(asg_name))

    payload = build_metric_payload(
        datapoints,
        unit=unit,
        threshold_value=threshold["V"] if threshold else None,
        threshold_operator=threshold["OP"] if threshold else None,
    )

    return round(latest_point["value"], 2), payload


# =========================================================
# Auto Scaling Metrics
# =========================================================

def get_desired_capacity_stats(asg_name, alarm_lookup):

    return get_metric_stats(
        "GroupDesiredCapacity",
        asg_name,
        alarm_lookup=alarm_lookup,
        unit="Count"
    )


def get_inservice_instances_stats(asg_name, alarm_lookup):

    return get_metric_stats(
        "GroupInServiceInstances",
        asg_name,
        alarm_lookup=alarm_lookup,
        unit="Count"
    )


def get_pending_instances_stats(asg_name, alarm_lookup):

    return get_metric_stats(
        "GroupPendingInstances",
        asg_name,
        alarm_lookup=alarm_lookup,
        unit="Count"
    )


def get_terminating_instances(asg_name):

    return get_metric(
        "GroupTerminatingInstances",
        asg_name
    )


def get_total_instances(asg_name):

    return get_metric(
        "GroupTotalInstances",
        asg_name
    
    )
# =========================================================
# Main Function
# =========================================================

def main():

    logger.info("Starting Auto Scaling Collector...")

    asgs = discover_auto_scaling_groups()

    if not asgs:

        print("No Auto Scaling Groups Found.")

        # Must still overwrite autoscaling.json for this run - otherwise a
        # previous run's autoscaling.json (possibly containing real ASGs)
        # would silently remain on disk and get reloaded by
        # context_builder.py as if it were current.
        write_json(
            "autoscaling.json",
            {
                "collector": "autoscaling",
                "timestamp": datetime.now(UTC).isoformat(),
                "resources": []
            }
        )

        print("\nAuto Scaling JSON report generated successfully.")

        return

    # One describe_alarms() call for this entire run - every metric below
    # looks its threshold up against this same in-memory index instead of
    # querying CloudWatch Alarms per metric.
    alarm_lookup = AlarmLookup(cloudwatch)

    asg_output = []

    for asg in asgs:

        print("\n" + "=" * 70)

        print(f"ASG Name             : {asg['AutoScalingGroupName']}")
        print(f"Min Size             : {asg['MinSize']}")
        print(f"Max Size             : {asg['MaxSize']}")
        print(f"Desired Capacity     : {asg['DesiredCapacity']}")
        print(f"Default Cooldown     : {asg['DefaultCooldown']}")
        print(f"Health Check Type    : {asg['HealthCheckType']}")
        print(f"Health Grace Period  : {asg['HealthCheckGracePeriod']}")

        print("\nAvailability Zones")

        for zone in asg["AvailabilityZones"]:

            print(f" - {zone}")

        print("\nCloudWatch Metrics")
        print("-" * 70)

        desired, desired_payload = get_desired_capacity_stats(
            asg["AutoScalingGroupName"],
            alarm_lookup
        )

        inservice, inservice_payload = get_inservice_instances_stats(
            asg["AutoScalingGroupName"],
            alarm_lookup
        )

        pending, pending_payload = get_pending_instances_stats(
            asg["AutoScalingGroupName"],
            alarm_lookup
        )

        terminating = get_terminating_instances(
            asg["AutoScalingGroupName"]
        )

        total = get_total_instances(
            asg["AutoScalingGroupName"]
        )

        desired = desired or 0
        inservice = inservice or 0
        pending = pending or 0

        asg_metric_trends = {}
        for key, payload in (
            ("DesiredCapacity", desired_payload),
            ("InServiceInstances", inservice_payload),
            ("PendingInstances", pending_payload),
        ):
            if payload:
                asg_metric_trends[key] = payload

        print(f"Desired Capacity : {desired}")
        print(f"In Service       : {inservice}")
        print(f"Pending          : {pending}")
        print(f"Terminating      : {terminating}")
        print(f"Total Instances  : {total}")

        print("\nInstances")
        print("-" * 70)

        instances = []

        for instance in asg["Instances"]:

            print(
                f"{instance['InstanceId']} | "
                f"{instance['LifecycleState']} | "
                f"{instance['HealthStatus']}"
            )

            instances.append({

                "instance_id": instance["InstanceId"],

                "lifecycle_state": instance["LifecycleState"],

                "health_status": instance["HealthStatus"],

                "availability_zone": instance["AvailabilityZone"],

                "instance_type": instance.get(
                    "InstanceType",
                    "Unknown"
                )

            })

        print("\nScaling Policies")
        print("-" * 70)

        policies = discover_scaling_policies(
            asg["AutoScalingGroupName"]
        )

        policy_list = []

        if not policies:

            print("No Scaling Policies Found.")

        else:

            for policy in policies:

                print(
                    f"{policy['PolicyName']} "
                    f"({policy['PolicyType']})"
                )

                policy_list.append({

                    "policy_name": policy["PolicyName"],

                    "policy_type": policy["PolicyType"]

                })

        print("\nScaling Activities")
        print("-" * 70)

        activities = discover_scaling_activities(
            asg["AutoScalingGroupName"]
        )

        activity_list = []

        if not activities:

            print("No Scaling Activities Found.")

        else:

            for activity in activities:

                print(
                    f"{activity['StatusCode']} | "
                    f"{activity['Description']}"
                )

                activity_list.append({

                    "status": activity["StatusCode"],

                    "description": activity["Description"],

                    "start_time": str(
                        activity["StartTime"]
                    )

                })

        asg_output.append({

            "asg_name": asg["AutoScalingGroupName"],

            "launch_template":
                asg.get(
                    "LaunchTemplate",
                    {}
                ),

            "min_size": asg["MinSize"],

            "max_size": asg["MaxSize"],

            "desired_capacity":
                asg["DesiredCapacity"],

            "default_cooldown":
                asg["DefaultCooldown"],

            "health_check_type":
                asg["HealthCheckType"],

            "health_check_grace_period":
                asg["HealthCheckGracePeriod"],

            "availability_zones":
                asg["AvailabilityZones"],

            "target_groups":
                asg["TargetGroupARNs"],

            "metrics": {

                "desired_capacity": desired,

                "in_service": inservice,

                "pending": pending,

                "terminating": terminating,

                "total_instances": total

            },

            "instances": instances,

            "scaling_policies": policy_list,

            "scaling_activities": activity_list,

            "MetricTrends": asg_metric_trends,

        })

    write_json(

        "autoscaling.json",
        {
            "collector": "autoscaling",
            "timestamp": datetime.now(UTC).isoformat(),
            "resources": asg_output
        }
    )


    print("\n")

    print("=" * 70)

    print("Auto Scaling JSON report generated successfully.")

    print("=" * 70)


# =========================================================
# Entry Point
# =========================================================

if __name__ == "__main__":

    main()

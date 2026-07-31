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

from utils.writer import write_json
from utils.aws_clients import (
    get_autoscaling_client,
    get_cloudwatch_client
)
from utils.logger import get_logger
from config.settings import METRIC_LOOKBACK_MINUTES
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

    except Exception as e:

        logger.error(
            f"Failed to discover Auto Scaling Groups: {str(e)}"
        )

        return []


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
# Auto Scaling Metrics
# =========================================================

def get_desired_capacity(asg_name):

    return get_metric(
        "GroupDesiredCapacity",
        asg_name
    )


def get_inservice_instances(asg_name):

    return get_metric(
        "GroupInServiceInstances",
        asg_name
    )


def get_pending_instances(asg_name):

    return get_metric(
        "GroupPendingInstances",
        asg_name
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

        return

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

        desired = get_desired_capacity(
            asg["AutoScalingGroupName"]
        )

        inservice = get_inservice_instances(
            asg["AutoScalingGroupName"]
        )

        pending = get_pending_instances(
            asg["AutoScalingGroupName"]
        )

        terminating = get_terminating_instances(
            asg["AutoScalingGroupName"]
        )

        total = get_total_instances(
            asg["AutoScalingGroupName"]
        )

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

            "scaling_activities": activity_list

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

"""
=========================================================
AI SRE AGENT
Module : ALB Collector
Author : Mallikarjun
Purpose:
    Discover Application Load Balancers, Target Groups,
    Target Health and collect CloudWatch metrics
    for AI Root Cause Analysis.
=========================================================
"""

# =========================================================
# Import Required Libraries
# =========================================================
from datetime import datetime, timedelta, UTC

from utils.writer import write_json
from utils.aws_clients import (
    get_elbv2_client,
    get_cloudwatch_client
)
from utils.logger import get_logger
from config.settings import METRIC_LOOKBACK_MINUTES
# =========================================================
# Configure Logger
# =========================================================

logger = get_logger(__name__)



# =========================================================
# Create AWS Clients
# =========================================================
elbv2 = get_elbv2_client()

cloudwatch = get_cloudwatch_client()
# =========================================================
# Discover Application Load Balancers
# =========================================================

def discover_load_balancers():
    """
    Discover all Application Load Balancers
    in the AWS account.
    """

    logger.info("Discovering ALBs...")

    albs = []

    paginator = elbv2.get_paginator(
        "describe_load_balancers"
    )

    for page in paginator.paginate():

        for lb in page["LoadBalancers"]:

            if lb["Type"] != "application":
                continue

            albs.append(lb)

    logger.info(f"Found {len(albs)} ALBs")

    return albs


# =========================================================
# Discover Target Groups
# =========================================================

def discover_target_groups(lb_arn):
    """
    Return all Target Groups attached
    to an ALB.
    """

    response = elbv2.describe_target_groups(
        LoadBalancerArn=lb_arn
    )

    return response["TargetGroups"]


# =========================================================
# Discover Target Health
# =========================================================

def discover_target_health(target_group_arn):
    """
    Return health information
    for all registered targets.
    """

    response = elbv2.describe_target_health(
        TargetGroupArn=target_group_arn
    )

    targets = []

    for target in response["TargetHealthDescriptions"]:

        targets.append({

            "instance_id": target["Target"]["Id"],

            "port": target["Target"]["Port"],

            "health": target["TargetHealth"]["State"],

            "reason": target["TargetHealth"].get(
                "Reason",
                ""
            ),

            "description": target["TargetHealth"].get(
                "Description",
                ""
            )

        })

    return targets

# =========================================================
# Convert ALB ARN to CloudWatch Dimension
# =========================================================

def get_alb_dimension(lb_arn):
    """
    Convert ALB ARN to CloudWatch metric dimension.

    Example ARN:

    arn:aws:elasticloadbalancing:us-east-1:123456789012:
    loadbalancer/app/prod-alb/50dc6c495c0c9188

    Returns:

    app/prod-alb/50dc6c495c0c9188
    """

    try:

        return lb_arn.split("loadbalancer/")[1]

    except IndexError:

        logger.error(
            f"Invalid ALB ARN: {lb_arn}"
        )

        return None
# =========================================================
# Convert Target Group ARN to CloudWatch Dimension
# =========================================================

def get_target_group_dimension(target_group_arn):
    """
    Convert Target Group ARN to CloudWatch metric dimension.

    Example ARN:

    arn:aws:elasticloadbalancing:us-east-1:123456789012:
    targetgroup/web-tg/6d0ecf831eec9f09

    Returns:

    targetgroup/web-tg/6d0ecf831eec9f09
    """

    try:

        return "targetgroup/" + target_group_arn.split(
            "targetgroup/"
        )[1]

    except IndexError:

        logger.error(
            f"Invalid Target Group ARN: {target_group_arn}"
        )

        return None
# =========================================================
# Generic CloudWatch Metric Function
# =========================================================

def get_metric(
    namespace,
    metric_name,
    dimensions,
    statistic="Sum"
):
    """
    Retrieve the latest CloudWatch metric.

    Returns:
        float or int
    """

    try:
        end_time = datetime.now(UTC)

        start_time = end_time - timedelta(minutes=METRIC_LOOKBACK_MINUTES)
        response = cloudwatch.get_metric_statistics(
            Namespace=namespace,
            MetricName=metric_name,
            Dimensions=dimensions,
            StartTime=start_time,
            EndTime=end_time,
            Period=300,
            Statistics=[statistic]
        )

        datapoints = response["Datapoints"]

        if not datapoints:
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
# Build ALB CloudWatch Dimensions
# =========================================================

def build_alb_dimensions(lb_dimension):

    return [
        {
            "Name": "LoadBalancer",
            "Value": lb_dimension
        }
    ]
# =========================================================
# Request Count
# =========================================================

def get_request_count(lb_dimension):

    return get_metric(
        namespace="AWS/ApplicationELB",
        metric_name="RequestCount",
        dimensions=build_alb_dimensions(
            lb_dimension
        )
    )
# =========================================================
# Processed Bytes
# =========================================================

def get_processed_bytes(lb_dimension):

    return get_metric(
        namespace="AWS/ApplicationELB",
        metric_name="ProcessedBytes",
        dimensions=build_alb_dimensions(
            lb_dimension
        )
    )
# =========================================================
# Target Response Time
# =========================================================

def get_target_response_time(lb_dimension):

    return get_metric(
        namespace="AWS/ApplicationELB",
        metric_name="TargetResponseTime",
        dimensions=build_alb_dimensions(
            lb_dimension
        ),
        statistic="Average"
    )
# =========================================================
# ELB 4XX Errors
# =========================================================

def get_elb_4xx(lb_dimension):

    return get_metric(
        namespace="AWS/ApplicationELB",
        metric_name="HTTPCode_ELB_4XX_Count",
        dimensions=build_alb_dimensions(
            lb_dimension
        )
    )
# =========================================================
# ELB 5XX Errors
# =========================================================

def get_elb_5xx(lb_dimension):

    return get_metric(
        namespace="AWS/ApplicationELB",
        metric_name="HTTPCode_ELB_5XX_Count",
        dimensions=build_alb_dimensions(
            lb_dimension
        )
    )
# =========================================================
# Target 4XX Errors
# =========================================================

def get_target_4xx(lb_dimension):

    return get_metric(
        namespace="AWS/ApplicationELB",
        metric_name="HTTPCode_Target_4XX_Count",
        dimensions=build_alb_dimensions(
            lb_dimension
        )
    )
# =========================================================
# Target 5XX Errors
# =========================================================

def get_target_5xx(lb_dimension):

    return get_metric(
        namespace="AWS/ApplicationELB",
        metric_name="HTTPCode_Target_5XX_Count",
        dimensions=build_alb_dimensions(
            lb_dimension
        )
    )
# =========================================================
# Healthy Host Count
# =========================================================

def get_healthy_host_count(
    lb_dimension,
    tg_dimension
):

    return get_metric(
        namespace="AWS/ApplicationELB",
        metric_name="HealthyHostCount",
        dimensions=[
            {
                "Name": "LoadBalancer",
                "Value": lb_dimension
            },
            {
                "Name": "TargetGroup",
                "Value": tg_dimension
            }
        ],
        statistic="Average"
    )
# =========================================================
# Unhealthy Host Count
# =========================================================

def get_unhealthy_host_count(
    lb_dimension,
    tg_dimension
):

    return get_metric(
        namespace="AWS/ApplicationELB",
        metric_name="UnHealthyHostCount",
        dimensions=[
            {
                "Name": "LoadBalancer",
                "Value": lb_dimension
            },
            {
                "Name": "TargetGroup",
                "Value": tg_dimension
            }
        ],
        statistic="Average"
    )# =========================================================
# Main Function
# =========================================================

def main():

    logger.info("Starting ALB Collector...")

    albs = discover_load_balancers()

    if not albs:
        print("No Application Load Balancers Found.")
        return

    alb_output = []

    for alb in albs:

        print("\n" + "=" * 70)

        print(f"ALB Name      : {alb['LoadBalancerName']}")
        print(f"DNS Name      : {alb['DNSName']}")
        print(f"Scheme        : {alb['Scheme']}")
        print(f"State         : {alb['State']['Code']}")
        print(f"VPC ID        : {alb['VpcId']}")

        lb_dimension = get_alb_dimension(
            alb["LoadBalancerArn"]
        )

        request_count = get_request_count(lb_dimension)
        processed_bytes = get_processed_bytes(lb_dimension)
        response_time = get_target_response_time(lb_dimension)
        elb_4xx = get_elb_4xx(lb_dimension)
        elb_5xx = get_elb_5xx(lb_dimension)
        target_4xx = get_target_4xx(lb_dimension)
        target_5xx = get_target_5xx(lb_dimension)

        print("\nCloudWatch Metrics")
        print("-" * 70)

        print(f"Request Count : {request_count}")
        print(f"ProcessedBytes: {processed_bytes}")
        print(f"Response Time : {response_time} sec")
        print(f"ELB 4XX       : {elb_4xx}")
        print(f"ELB 5XX       : {elb_5xx}")
        print(f"Target 4XX    : {target_4xx}")
        print(f"Target 5XX    : {target_5xx}")

        target_groups = discover_target_groups(
            alb["LoadBalancerArn"]
        )

        tg_output = []

        for tg in target_groups:

            print("\n" + "-" * 70)

            print(f"Target Group  : {tg['TargetGroupName']}")
            print(f"Protocol      : {tg['Protocol']}")
            print(f"Port          : {tg['Port']}")
            print(f"Health Path   : {tg['HealthCheckPath']}")

            tg_dimension = get_target_group_dimension(
                tg["TargetGroupArn"]
            )

            healthy = get_healthy_host_count(
                lb_dimension,
                tg_dimension
            )

            unhealthy = get_unhealthy_host_count(
                lb_dimension,
                tg_dimension
            )

            print(f"Healthy Hosts : {healthy}")
            print(f"Unhealthy     : {unhealthy}")

            targets = discover_target_health(
                tg["TargetGroupArn"]
            )

            print("\nRegistered Targets")

            for target in targets:

                print(
                    f"Instance : {target['instance_id']} | "
                    f"Port : {target['port']} | "
                    f"Health : {target['health']}"
                )

            tg_output.append({

                "target_group_name": tg["TargetGroupName"],

                "protocol": tg["Protocol"],

                "port": tg["Port"],

                "health_check_path": tg["HealthCheckPath"],

                "healthy_hosts": healthy,

                "unhealthy_hosts": unhealthy,

                "targets": targets

            })

        alb_output.append({

            "alb_name": alb["LoadBalancerName"],

            "dns_name": alb["DNSName"],

            "scheme": alb["Scheme"],

            "state": alb["State"]["Code"],

            "vpc_id": alb["VpcId"],

            "metrics": {

                "request_count": request_count,

                "processed_bytes": processed_bytes,

                "response_time": response_time,

                "elb_4xx": elb_4xx,

                "elb_5xx": elb_5xx,

                "target_4xx": target_4xx,

                "target_5xx": target_5xx

            },

            "target_groups": tg_output

        })

    write_json(

        "alb.json",

        {

            "collector": "alb",
            "timestamp": datetime.now(UTC).isoformat(),

            "resources": alb_output

        }

    )

    print("\nALB JSON report generated successfully.")


# =========================================================
# Entry Point
# =========================================================

if __name__ == "__main__":
    main()

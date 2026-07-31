"""
=========================================================
AI SRE AGENT
Module  : CloudWatch Collector
File    : cloudwatch.py
Author  : Mallikarjun

Purpose
-------
Collect infrastructure metrics from AWS CloudWatch.

Metrics Collected
-----------------
1. CPU Utilization
2. Memory Usage
3. Disk Usage
4. Network In
5. Network Out
6. EC2 Status Checks
7. CloudWatch Alarms

Future Usage
------------
The collected metrics will be consumed by the
Correlation Engine and Gemini AI to generate
Root Cause Analysis (RCA).

Requirements
------------
- boto3
- IAM Role attached to EC2
- CloudWatch Agent installed on monitored EC2 instances
=========================================================
"""

# =========================================================
# Import Required Libraries
# =========================================================
import json

from datetime import datetime, timedelta, UTC

from utils.writer import write_json
from utils.aws_clients import (
    get_cloudwatch_client,
    get_ec2_client
)
from utils.logger import get_logger
from config.settings import METRIC_LOOKBACK_MINUTES
# =========================================================
# Logger
# =========================================================

logger = get_logger(__name__)

# =========================================================
# AWS Clients
# =========================================================

ec2 = get_ec2_client()

cloudwatch = get_cloudwatch_client()

# =========================================================
# Generic CloudWatch Metric Function
# =========================================================

def get_metric(
    namespace,
    metric_name,
    dimensions,
    statistic="Average"
):
    """
    Generic CloudWatch metric reader.

    Returns the latest metric value.

    Example:

    CPU
    Memory
    Disk
    Network
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

        if len(datapoints) == 0:
            return None

        latest = sorted(
            datapoints,
            key=lambda x: x["Timestamp"]
        )[-1]

        return round(
            latest[statistic],
            2
        )

    except Exception:

        return None


# =========================================================
# Fetch EC2 Instances
# =========================================================

def get_instances():
    """
    Returns all EC2 instances.
    """

    response = ec2.describe_instances()

    instances = []

    for reservation in response["Reservations"]:

        for instance in reservation["Instances"]:

            instance_id = instance["InstanceId"]

            state = instance["State"]["Name"]

            private_ip = instance.get(
                "PrivateIpAddress",
                "N/A"
            )

            public_ip = instance.get(
                "PublicIpAddress",
                "N/A"
            )

            instance_name = "No Name"

            if "Tags" in instance:

                for tag in instance["Tags"]:

                    if tag["Key"] == "Name":

                        instance_name = tag["Value"]

            instances.append({

                "Name": instance_name,

                "InstanceId": instance_id,

                "State": state,

                "PrivateIP": private_ip,

                "PublicIP": public_ip

            })

    return instances


# =========================================================
# CPU Utilization
# =========================================================

def get_cpu(instance_id):

    return get_metric(

        namespace="AWS/EC2",

        metric_name="CPUUtilization",

        dimensions=[

            {
                "Name": "InstanceId",
                "Value": instance_id
            }

        ]

    )


# =========================================================
# Memory Usage
# =========================================================

def get_memory(instance_id):

    return get_metric(

        namespace="CWAgent",

        metric_name="mem_used_percent",

        dimensions=[

            {
                "Name": "InstanceId",
                "Value": instance_id
            }

        ]

    )
# =========================================================
# Discover Root Disk Dimensions
# =========================================================

def discover_root_disk_dimensions(instance_id):

    response = cloudwatch.list_metrics(
        Namespace="CWAgent",
        MetricName="disk_used_percent"
    )

    for metric in response["Metrics"]:

        dimensions = metric["Dimensions"]

        instance_match = False
        root_match = False

        for dim in dimensions:

            if dim["Name"] == "InstanceId" and dim["Value"] == instance_id:
                instance_match = True

            if dim["Name"] == "path" and dim["Value"] == "/":
                root_match = True

        if instance_match and root_match:
            return dimensions

    return None

# =========================================================
# Disk Usage
# =========================================================

def get_disk(instance_id):

    dimensions = discover_root_disk_dimensions(instance_id)

    if dimensions is None:
        return "CWAgent Not Installed"

    return get_metric(
        namespace="CWAgent",
        metric_name="disk_used_percent",
        dimensions=dimensions
    )

# =========================================================
# Network In
# =========================================================

def get_network_in(instance_id):

    return get_metric(

        namespace="AWS/EC2",

        metric_name="NetworkIn",

        dimensions=[

            {
                "Name": "InstanceId",
                "Value": instance_id
            }

        ],

        statistic="Sum"

    )


# =========================================================
# Network Out
# =========================================================

def get_network_out(instance_id):

    return get_metric(

        namespace="AWS/EC2",

        metric_name="NetworkOut",

        dimensions=[

            {
                "Name": "InstanceId",
                "Value": instance_id
            }

        ],

        statistic="Sum"

    )


# =========================================================
# EC2 Status Check
# =========================================================

def get_status_check(instance_id):

    value = get_metric(

        namespace="AWS/EC2",

        metric_name="StatusCheckFailed",

        dimensions=[

            {
                "Name": "InstanceId",
                "Value": instance_id
            }

        ],

        statistic="Maximum"

    )

    if value is None:
        return "UNKNOWN"

    if value == 0:
        return "PASS"

    return "FAILED"


# =========================================================
# CloudWatch Alarms
# =========================================================

def get_cloudwatch_alarms():

    alarms = []

    try:

        response = cloudwatch.describe_alarms()

        for alarm in response["MetricAlarms"]:

            alarms.append({

                "AlarmName": alarm["AlarmName"],

                "Metric": alarm["MetricName"],

                "Namespace": alarm["Namespace"],

                "State": alarm["StateValue"],

                "Reason": alarm["StateReason"]

            })

    except Exception as e:

        print(f"Unable to fetch alarms : {e}")

    return alarms


# =========================================================
# Collect Metrics For One Instance
# =========================================================

def collect_instance_metrics(instance):

    instance_id = instance["InstanceId"]

    return {

        "Name": instance["Name"],

        "InstanceId": instance_id,

        "State": instance["State"],

        "PrivateIP": instance["PrivateIP"],

        "PublicIP": instance["PublicIP"],

        "CPU": get_cpu(instance_id),

        "Memory": get_memory(instance_id),

        "Disk": get_disk(instance_id),

        "NetworkIn": get_network_in(instance_id),

        "NetworkOut": get_network_out(instance_id),

        "StatusCheck": get_status_check(instance_id)

    }


# =========================================================
# Collect Entire CloudWatch Inventory
# =========================================================

def collect_cloudwatch_data():

    data = {

        "Timestamp": datetime.now(UTC).isoformat(),

        "Instances": [],

        "Alarms": get_cloudwatch_alarms()

    }

    instances = get_instances()

    for instance in instances:

        metrics = collect_instance_metrics(instance)

        data["Instances"].append(metrics)

    return data
# =========================================================
# Pretty Console Output
# =========================================================

def print_report(data):

    print("\n")
    print("=" * 70)
    print("               AI SRE AGENT - CLOUDWATCH REPORT")
    print("=" * 70)

    print(f"\nCollection Time : {data['Timestamp']}")

    print("\n")
    print("=" * 70)
    print("EC2 INSTANCES")
    print("=" * 70)

    for instance in data["Instances"]:

        print()

        print("-" * 60)
        print(f"Instance Name    : {instance['Name']}")
        print(f"Instance ID      : {instance['InstanceId']}")
        print(f"State            : {instance['State']}")
        print(f"Private IP       : {instance['PrivateIP']}")
        print(f"Public IP        : {instance['PublicIP']}")
        print(f"CPU (%)          : {instance['CPU']}")
        print(f"Memory (%)       : {instance['Memory']}")
        print(f"Disk (%)         : {instance['Disk']}")
        print(f"Network In       : {instance['NetworkIn']}")
        print(f"Network Out      : {instance['NetworkOut']}")
        print(f"Status Check     : {instance['StatusCheck']}")
        print("-" * 60)

    print("\n")
    print("=" * 70)
    print("CLOUDWATCH ALARMS")
    print("=" * 70)

    if len(data["Alarms"]) == 0:

        print("\nNo CloudWatch Alarms Found.\n")

    else:

        for alarm in data["Alarms"]:

            print()

            print("-" * 60)
            print(f"Alarm Name : {alarm['AlarmName']}")
            print(f"Metric     : {alarm['Metric']}")
            print(f"Namespace  : {alarm['Namespace']}")
            print(f"State      : {alarm['State']}")
            print(f"Reason     : {alarm['Reason']}")
            print("-" * 60)


# =========================================================
# Save JSON Report
# =========================================================
def save_json(data):

    write_json(
        "cloudwatch.json",
        {
            "collector": "cloudwatch",
            "timestamp": data["Timestamp"],
            "resources": data["Instances"]
        }
    )

    print("\nCloudWatch JSON report generated successfully.")
# =========================================================
# Main Function
# =========================================================

def main():

    print("\nCollecting CloudWatch metrics...\n")

    data = collect_cloudwatch_data()

    print_report(data)

    save_json(data)


# =========================================================
# Program Entry Point
# =========================================================

if __name__ == "__main__":
    main()

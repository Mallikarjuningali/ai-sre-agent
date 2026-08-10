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
from utils.metric_stats import build_metric_payload
from utils.alarm_lookup import AlarmLookup
from config.settings import METRIC_TREND_LOOKBACK_MINUTES, METRIC_TREND_PERIOD_SECONDS
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
# Generic CloudWatch Metric Functions
# =========================================================

def get_metric_datapoints(
    namespace,
    metric_name,
    dimensions,
    statistic="Average"
):
    """
    Fetches the previous METRIC_TREND_LOOKBACK_MINUTES of a metric at
    METRIC_TREND_PERIOD_SECONDS granularity.

    The returned list exists only for the duration of this call - only
    the compact telemetry payload built from it (get_metric_stats, below)
    is ever persisted, exactly like every other collector value in
    output/raw/.
    """

    try:
        end_time = datetime.now(UTC)
        start_time = end_time - timedelta(minutes=METRIC_TREND_LOOKBACK_MINUTES)

        response = cloudwatch.get_metric_statistics(

            Namespace=namespace,

            MetricName=metric_name,

            Dimensions=dimensions,

            StartTime=start_time,
            EndTime=end_time,
            Period=METRIC_TREND_PERIOD_SECONDS,
            Statistics=[statistic]

        )

        return [
            {"timestamp": point["Timestamp"], "value": point[statistic]}
            for point in response["Datapoints"]
        ]

    except Exception:

        return []


def get_metric_stats(
    namespace,
    metric_name,
    dimensions,
    alarm_lookup,
    statistic="Average",
    unit="Count"
):
    """
    One CloudWatch call -> (latest_value, telemetry_payload).

    latest_value is a plain rounded number (or None) - kept only for the
    existing snapshot fields (CPU/Memory/... on the raw record) that
    dashboard_export.py's telemetry reading already depends on. It is
    derived here directly from the same datapoints, not computed by
    build_metric_payload(), which returns {"U", "TH", "H"} only - no
    interpreted or derived values, not even "latest".

    TH is never hardcoded: alarm_lookup.find_threshold() is queried with
    this exact same namespace/metric_name/dimensions - the identical
    triple used to fetch the datapoints above - and only returns a value
    when a real CloudWatch Alarm matches it. No match means no TH.

    Both latest/payload are None/None when there was no data in the
    window (e.g. CloudWatch Agent not installed) - callers should degrade
    gracefully, not fabricate a value.
    """

    datapoints = get_metric_datapoints(namespace, metric_name, dimensions, statistic)

    if not datapoints:
        return None, None

    latest_point = max(datapoints, key=lambda point: point["timestamp"])

    threshold = alarm_lookup.find_threshold(namespace, metric_name, dimensions)

    payload = build_metric_payload(
        datapoints,
        unit=unit,
        threshold_value=threshold["V"] if threshold else None,
        threshold_operator=threshold["OP"] if threshold else None,
    )

    return round(latest_point["value"], 2), payload


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

def get_cpu_stats(instance_id, alarm_lookup):

    return get_metric_stats(

        namespace="AWS/EC2",

        metric_name="CPUUtilization",

        dimensions=[

            {
                "Name": "InstanceId",
                "Value": instance_id
            }

        ],

        alarm_lookup=alarm_lookup,
        unit="%"

    )


# =========================================================
# Memory Usage
# =========================================================

def get_memory_stats(instance_id, alarm_lookup):

    return get_metric_stats(

        namespace="CWAgent",

        metric_name="mem_used_percent",

        dimensions=[

            {
                "Name": "InstanceId",
                "Value": instance_id
            }

        ],

        alarm_lookup=alarm_lookup,
        unit="%"

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

def get_disk_stats(instance_id, alarm_lookup):
    """Returns ("CWAgent Not Installed", None) when the agent isn't
    reporting disk metrics at all (preserved exactly, same string as
    before this was made telemetry-aware), or (latest, payload)/(None,
    None) otherwise."""

    dimensions = discover_root_disk_dimensions(instance_id)

    if dimensions is None:
        return "CWAgent Not Installed", None

    return get_metric_stats(
        namespace="CWAgent",
        metric_name="disk_used_percent",
        dimensions=dimensions,
        alarm_lookup=alarm_lookup,
        unit="%"
    )

# =========================================================
# Network In
# =========================================================

def get_network_in_stats(instance_id, alarm_lookup):

    return get_metric_stats(

        namespace="AWS/EC2",

        metric_name="NetworkIn",

        dimensions=[

            {
                "Name": "InstanceId",
                "Value": instance_id
            }

        ],

        alarm_lookup=alarm_lookup,
        statistic="Sum",
        unit="Bytes"

    )


# =========================================================
# Network Out
# =========================================================

def get_network_out_stats(instance_id, alarm_lookup):

    return get_metric_stats(

        namespace="AWS/EC2",

        metric_name="NetworkOut",

        dimensions=[

            {
                "Name": "InstanceId",
                "Value": instance_id
            }

        ],

        alarm_lookup=alarm_lookup,
        statistic="Sum",
        unit="Bytes"

    )


# =========================================================
# EC2 Status Check
# =========================================================

def get_status_check_stats(instance_id, alarm_lookup):

    return get_metric_stats(

        namespace="AWS/EC2",

        metric_name="StatusCheckFailed",

        dimensions=[

            {
                "Name": "InstanceId",
                "Value": instance_id
            }

        ],

        alarm_lookup=alarm_lookup,
        statistic="Maximum",
        unit="Count"

    )


def status_check_label(latest_value):
    """PASS/FAILED/UNKNOWN - same derivation as before, reading the plain
    latest StatusCheckFailed value directly (0 or 1) rather than a stats
    dict, since build_metric_payload() no longer computes "latest"."""

    if latest_value is None:
        return "UNKNOWN"

    if latest_value == 0:
        return "PASS"

    return "FAILED"


# =========================================================
# CloudWatch Alarms
# =========================================================

def get_cloudwatch_alarms(alarm_lookup):
    """Thin wrapper over the shared AlarmLookup's already-fetched alarms -
    no independent describe_alarms() call here anymore. One source of
    truth for alarm discovery (see utils/alarm_lookup.py)."""

    return alarm_lookup.list_alarms()


# =========================================================
# Collect Metrics For One Instance
# =========================================================

def collect_instance_metrics(instance, alarm_lookup):
    """
    Fetches each metric's previous-hour window exactly once (get_*_stats
    functions above, each returning (latest, payload)) and derives both
    the existing "latest snapshot" fields (CPU/Memory/Disk/NetworkIn/
    NetworkOut/StatusCheck - unchanged shape, for backward compatibility
    with everything already reading them) and the new MetricTrends
    telemetry payloads from that same call, instead of fetching each
    metric twice.

    alarm_lookup is the single AlarmLookup built once in main() for this
    whole collector run - reused here for every metric so there is never
    more than one describe_alarms() call per run.

    Metrics unavailable in this window (e.g. CWAgent not installed) are
    simply left out of MetricTrends - "continue gracefully, include only
    what's available".
    """

    instance_id = instance["InstanceId"]

    cpu_latest, cpu_payload = get_cpu_stats(instance_id, alarm_lookup)
    memory_latest, memory_payload = get_memory_stats(instance_id, alarm_lookup)
    disk_latest, disk_payload = get_disk_stats(instance_id, alarm_lookup)
    network_in_latest, network_in_payload = get_network_in_stats(instance_id, alarm_lookup)
    network_out_latest, network_out_payload = get_network_out_stats(instance_id, alarm_lookup)
    status_check_latest, status_check_payload = get_status_check_stats(instance_id, alarm_lookup)

    metric_trends = {}
    for key, payload in (
        ("CPU", cpu_payload),
        ("Memory", memory_payload),
        ("Disk", disk_payload),
        ("NetworkIn", network_in_payload),
        ("NetworkOut", network_out_payload),
        ("StatusCheck", status_check_payload),
    ):
        if payload:
            metric_trends[key] = payload

    return {

        "Name": instance["Name"],

        "InstanceId": instance_id,

        "State": instance["State"],

        "PrivateIP": instance["PrivateIP"],

        "PublicIP": instance["PublicIP"],

        "CPU": cpu_latest,

        "Memory": memory_latest,

        "Disk": disk_latest,

        "NetworkIn": network_in_latest,

        "NetworkOut": network_out_latest,

        "StatusCheck": status_check_label(status_check_latest),

        "MetricTrends": metric_trends,

    }


# =========================================================
# Collect Entire CloudWatch Inventory
# =========================================================

def collect_cloudwatch_data(alarm_lookup):

    data = {

        "Timestamp": datetime.now(UTC).isoformat(),

        "Instances": [],

        "Alarms": get_cloudwatch_alarms(alarm_lookup)

    }

    instances = get_instances()

    for instance in instances:

        metrics = collect_instance_metrics(instance, alarm_lookup)

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

    # One describe_alarms() call for this entire run - every metric below
    # looks its threshold up against this same in-memory index instead of
    # querying CloudWatch Alarms per metric.
    alarm_lookup = AlarmLookup(cloudwatch)

    data = collect_cloudwatch_data(alarm_lookup)

    print_report(data)

    save_json(data)


# =========================================================
# Program Entry Point
# =========================================================

if __name__ == "__main__":
    main()

"""
=========================================================
AI SRE AGENT
Module : Log Collector
Purpose:
    AWS-facing discovery/fetch for the OPTIONAL, explicitly user-triggered
    Log Investigation feature (see api/log_investigation_manager.py).

    Unlike collector/cloudwatch.py, collector/alb.py,
    collector/autoscaling.py and collector/cloudtrail.py - which always
    collect fleet-wide, metrics-only data as part of every Full/Single
    Resource Investigation - this module:

      * is scoped to exactly ONE resource and ONE bounded time window per
        call, never the whole fleet;
      * is never imported by api/investigation_manager.py, analyzer/
        analyzer.py, or context/context_builder.py - it has no `main()`
        and writes nothing to output/raw/;
      * is only ever invoked by api/log_investigation_manager.py, and only
        after a user explicitly clicks "Investigate Logs".

    Every function here does not invent a log source - if the AWS
    mechanism a resource type would realistically use isn't configured or
    discoverable within the bounds below, the caller gets an honest
    "unavailable" signal (None / empty list with a reason), never a
    fabricated "no errors found".
=========================================================
"""

import gzip
from datetime import datetime, timedelta, UTC

from botocore.exceptions import BotoCoreError, ClientError

from utils.aws_clients import get_logs_client, get_s3_client, get_autoscaling_client, get_elbv2_client
from utils.logger import get_logger
from config.settings import (
    LOG_MAX_GROUPS_SCANNED,
    LOG_MAX_S3_OBJECTS_SCANNED,
    LOG_MAX_RAW_EVENTS,
)

logger = get_logger(__name__)

logs_client = get_logs_client()
s3_client = get_s3_client()
autoscaling_client = get_autoscaling_client()
elbv2_client = get_elbv2_client()


# =========================================================
# EC2 - CloudWatch Logs
# =========================================================
# CloudWatch Agent's documented default log_stream_name template is the
# instance ID itself (https://docs.aws.amazon.com/AmazonCloudWatch/latest/
# monitoring/CloudWatch-Agent-Configuration-File-Details.html -
# log_stream_name supports the {instance_id} placeholder, and using the
# bare instance ID as the stream name is CloudWatch Agent's own default
# when no log_stream_name is configured at all). Discovery below looks
# for a log stream literally named after the instance ID, bounded to
# LOG_MAX_GROUPS_SCANNED log groups so an account with many unrelated log
# groups can't turn one investigation into an unbounded account-wide scan.

def discover_ec2_log_source(instance_id):
    """Returns {"log_group": ..., "log_stream": ...} for the first log
    group (within the scanned bound) that has a log stream named after
    this instance ID, or None if none was found within that bound - a
    real "not configured/not discoverable" result, never guessed."""

    try:
        groups_scanned = 0
        paginator = logs_client.get_paginator("describe_log_groups")

        for page in paginator.paginate():

            for group in page.get("logGroups", []):

                if groups_scanned >= LOG_MAX_GROUPS_SCANNED:
                    logger.info(
                        f"EC2 log discovery for {instance_id} stopped after "
                        f"{LOG_MAX_GROUPS_SCANNED} log groups scanned (bounded)."
                    )
                    return None

                groups_scanned += 1
                log_group_name = group.get("logGroupName")

                try:
                    streams_response = logs_client.describe_log_streams(
                        logGroupName=log_group_name,
                        logStreamNamePrefix=instance_id,
                        limit=1,
                    )
                except (BotoCoreError, ClientError) as exc:
                    # A single log group being unreadable (permissions,
                    # rare edge cases) must not abort discovery across the
                    # rest of the account - log and keep scanning.
                    logger.warning(f"describe_log_streams failed for {log_group_name}: {exc}")
                    continue

                streams = streams_response.get("logStreams") or []
                if streams:
                    return {"log_group": log_group_name, "log_stream": streams[0]["logStreamName"]}

        return None

    except (BotoCoreError, ClientError) as exc:
        logger.error(f"EC2 log source discovery failed for {instance_id}: {exc}")
        raise


def fetch_ec2_log_events(log_group, log_stream, start_dt, end_dt):
    """Real CloudWatch Logs events for [start_dt, end_dt], bounded to
    LOG_MAX_RAW_EVENTS. Returns a list of {"timestamp": datetime, "message": str}
    - the raw event text itself is NOT sanitized here; sanitization only
    ever happens in llm/log_sanitizer.py, on the reduced Log Evidence
    Package, per the isolation requirement."""

    events = []
    next_token = None
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    while len(events) < LOG_MAX_RAW_EVENTS:

        request_kwargs = {
            "logGroupName": log_group,
            "logStreamNames": [log_stream],
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": min(10000, LOG_MAX_RAW_EVENTS - len(events)),
        }
        if next_token:
            request_kwargs["nextToken"] = next_token

        response = logs_client.filter_log_events(**request_kwargs)

        for event in response.get("events", []):
            events.append({
                "timestamp": datetime.fromtimestamp(event["timestamp"] / 1000, tz=UTC),
                "message": event.get("message", ""),
            })

        next_token = response.get("nextToken")
        if not next_token:
            break

    return events[:LOG_MAX_RAW_EVENTS]


# =========================================================
# ALB - S3-delivered access logs
# =========================================================
# elbv2.describe_load_balancer_attributes() has a real, documented boolean
# attribute (access_logs.s3.enabled) plus the bucket/prefix when enabled -
# this is a genuine AWS feature, not an invented convention. If disabled,
# callers get an honest "unavailable" with zero extra AWS calls.

def resolve_alb_arn(alb_name):
    """context/context_builder.py's first-class ALB resource_id is the
    plain LoadBalancerName (e.g. "prod-alb"), not an ARN -
    describe_load_balancer_attributes() needs the ARN, so this resolves
    it with one bounded, by-name AWS call (never a full-account scan).
    Returns None if no load balancer with this exact name exists
    (e.g. it was deleted/renamed since the investigation ran)."""

    try:
        response = elbv2_client.describe_load_balancers(Names=[alb_name])
    except ClientError as exc:
        if (exc.response.get("Error") or {}).get("Code") == "LoadBalancerNotFoundException":
            # A legitimate "no longer exists by this name" - not a system
            # failure, so it's reported as "not found" (None) rather than
            # bubbling up as an AWS error.
            return None
        logger.error(f"describe_load_balancers failed for {alb_name}: {exc}")
        raise
    except BotoCoreError as exc:
        logger.error(f"describe_load_balancers failed for {alb_name}: {exc}")
        raise

    load_balancers = response.get("LoadBalancers") or []
    if not load_balancers:
        return None

    return load_balancers[0].get("LoadBalancerArn")


def discover_alb_access_log_config(lb_arn):
    """Returns {"bucket": ..., "prefix": ...} if ALB access logging is
    enabled for this load balancer, else None."""

    try:
        response = elbv2_client.describe_load_balancer_attributes(LoadBalancerArn=lb_arn)
    except (BotoCoreError, ClientError) as exc:
        logger.error(f"describe_load_balancer_attributes failed for {lb_arn}: {exc}")
        raise

    attributes = {attr["Key"]: attr["Value"] for attr in response.get("Attributes", [])}

    if attributes.get("access_logs.s3.enabled") != "true":
        return None

    bucket = attributes.get("access_logs.s3.bucket")
    if not bucket:
        return None

    return {"bucket": bucket, "prefix": attributes.get("access_logs.s3.prefix") or ""}


def _discover_account_log_prefix(bucket, prefix):
    """ALB's documented S3 key layout is
    bucket[/prefix]/AWSLogs/<account-id>/elasticloadbalancing/<region>/...
    - rather than guessing the account ID, one bounded ListObjectsV2 call
    with Delimiter='/' discovers the real (usually singular) account-id
    "folder" AWS itself created. Returns the full prefix up to and
    including the account-id segment, or None if nothing was ever
    delivered there."""

    base = f"{prefix}/AWSLogs/" if prefix else "AWSLogs/"

    try:
        response = s3_client.list_objects_v2(Bucket=bucket, Prefix=base, Delimiter="/")
    except (BotoCoreError, ClientError) as exc:
        logger.error(f"list_objects_v2 failed for bucket {bucket} prefix {base}: {exc}")
        raise

    common_prefixes = response.get("CommonPrefixes") or []
    if not common_prefixes:
        return None

    return common_prefixes[0]["Prefix"]


def fetch_alb_access_logs(bucket, prefix, region, start_dt, end_dt):
    """Downloads/parses gzipped ALB access log objects delivered to S3
    within [start_dt, end_dt], bounded to LOG_MAX_S3_OBJECTS_SCANNED
    objects. Only lists the date-hour prefixes inside the requested
    window (never the whole bucket) - ALB delivers one object roughly
    every 5 minutes per load balancer, so a bounded incident window keeps
    this to a small, predictable number of ListObjectsV2/GetObject calls.
    Returns a list of {"timestamp": datetime, "message": str} (one entry
    per access-log line) - raw, unsanitized, exactly like
    fetch_ec2_log_events()."""

    account_prefix = _discover_account_log_prefix(bucket, prefix)
    if account_prefix is None:
        return []

    events = []
    objects_scanned = 0

    current_date = start_dt.date()
    end_date = end_dt.date()

    while current_date <= end_date and objects_scanned < LOG_MAX_S3_OBJECTS_SCANNED:

        day_prefix = (
            f"{account_prefix}elasticloadbalancing/{region}/"
            f"{current_date.year:04d}/{current_date.month:02d}/{current_date.day:02d}/"
        )

        try:
            paginator = s3_client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket, Prefix=day_prefix):
                for obj in page.get("Contents", []):

                    if objects_scanned >= LOG_MAX_S3_OBJECTS_SCANNED:
                        break

                    key = obj["Key"]
                    last_modified = obj.get("LastModified")
                    if last_modified and not (start_dt <= last_modified <= end_dt):
                        continue

                    objects_scanned += 1
                    events.extend(_read_alb_access_log_object(bucket, key))

        except (BotoCoreError, ClientError) as exc:
            logger.error(f"Listing ALB access logs failed for {bucket}/{day_prefix}: {exc}")
            raise

        current_date += timedelta(days=1)

    return [e for e in events if start_dt <= e["timestamp"] <= end_dt]


def _read_alb_access_log_object(bucket, key):
    """Downloads and gunzips one ALB access-log S3 object, returning one
    {"timestamp", "message"} entry per line. ALB access log lines start
    with a space-delimited ISO8601 timestamp as their first field -
    https://docs.aws.amazon.com/elasticloadbalancing/latest/application/
    load-balancer-access-logs.html. A line that doesn't parse is skipped,
    not fabricated a timestamp."""

    try:
        obj = s3_client.get_object(Bucket=bucket, Key=key)
        raw_bytes = gzip.decompress(obj["Body"].read())
    except (BotoCoreError, ClientError, OSError) as exc:
        logger.warning(f"Could not read ALB access log object {key}: {exc}")
        return []

    events = []
    for line in raw_bytes.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        timestamp_field = line.split(" ", 1)[0]
        try:
            timestamp = datetime.strptime(timestamp_field, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
        except ValueError:
            continue
        events.append({"timestamp": timestamp, "message": line})

    return events


# =========================================================
# ASG - scaling activities, lifecycle/health events
# =========================================================
# The request's own listed ASG "log sources" (scaling activity, lifecycle
# events, health-related events) map directly to a real, already-partially
# -used AWS API: describe_scaling_activities. This reuses the same client/
# API collector/autoscaling.py already calls, just with fuller detail and
# a caller-supplied window instead of a fixed MaxRecords=10 snapshot.

def discover_asg_scaling_activities(asg_name, start_dt, end_dt, max_records=100):
    """Real scaling activities for this ASG whose StartTime falls in
    [start_dt, end_dt]. Returns a list of {"timestamp": datetime,
    "message": str} - message is a compact, real-fields-only rendering
    (status/description/cause/status_message), not a fabricated log
    line."""

    try:
        response = autoscaling_client.describe_scaling_activities(
            AutoScalingGroupName=asg_name,
            MaxRecords=max_records,
        )
    except (BotoCoreError, ClientError) as exc:
        logger.error(f"describe_scaling_activities failed for {asg_name}: {exc}")
        raise

    events = []
    for activity in response.get("Activities", []):

        start_time = activity.get("StartTime")
        if start_time is None:
            continue
        if not (start_dt <= start_time <= end_dt):
            continue

        parts = [activity.get("StatusCode") or "", activity.get("Description") or ""]
        if activity.get("Cause"):
            parts.append(f"cause: {activity['Cause']}")
        if activity.get("StatusMessage"):
            parts.append(f"status: {activity['StatusMessage']}")

        events.append({"timestamp": start_time, "message": " | ".join(p for p in parts if p)})

    return events

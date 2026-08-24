"""
=========================================================
AI SRE AGENT
Module : AWS Clients
Author : Mallikarjun
Purpose:
    Create reusable AWS clients.
=========================================================
"""

import boto3

from config.settings import REGION


def get_cloudwatch_client():
    return boto3.client(
        "cloudwatch",
        region_name=REGION
    )


def get_elbv2_client():
    return boto3.client(
        "elbv2",
        region_name=REGION
    )


def get_autoscaling_client():
    return boto3.client(
        "autoscaling",
        region_name=REGION
    )


def get_ec2_client():
    return boto3.client(
        "ec2",
        region_name=REGION
    )


def get_cloudtrail_client():
    return boto3.client(
        "cloudtrail",
        region_name=REGION
    )


def get_ce_client():
    """AWS Cost Explorer is only served from us-east-1, regardless of
    which region the rest of this account's resources (or REGION above)
    live in - so this deliberately does NOT import REGION, unlike every
    other client here. Hardcoding it keeps Cost Explorer correct even if
    REGION is ever changed for EC2/CloudWatch/etc."""
    return boto3.client(
        "ce",
        region_name="us-east-1"
    )

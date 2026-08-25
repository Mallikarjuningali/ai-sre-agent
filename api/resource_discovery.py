"""
=========================================================
AI SRE AGENT
Module : Resource Discovery
Purpose:
    Lightweight, live AWS resource discovery for the Single Resource
    Investigation picker (GET /investigation/resources - see api/app.py).

    Identity/metadata only - no CloudWatch metrics, no MetricTrends, no
    output/raw writes, no context building, no Gemini calls. This exists
    so the picker can be populated before any investigation (Full or
    Single) has ever run, instead of depending on output/context/*.json
    - a byproduct of a previous investigation having already executed.

    Reuses the same discovery calls the collectors' own main() functions
    make - collector.cloudwatch.get_instances(), collector.alb
    .discover_load_balancers(), collector.autoscaling
    .discover_auto_scaling_groups() - none of which fetch metrics or
    write output/raw/*.json themselves (that only happens later in each
    collector's main(), which this module never calls). AWS is the only
    source of truth here.
=========================================================
"""

from collector.cloudwatch import get_instances
from collector.alb import discover_load_balancers
from collector.autoscaling import discover_auto_scaling_groups
from utils.logger import get_logger

logger = get_logger("ResourceDiscovery")

# Same resource-type labels utils/dashboard_export.py's build_resources_json
# groups under (see RESOURCE_TYPE_LABELS / context_builder.py's ALB/ASG
# promotion) - keeps this live-discovered inventory shape-identical to the
# published resources.json, so the UI can treat either source the same way.
EC2_LABEL = "EC2 Instance"
ALB_LABEL = "Load Balancer"
ASG_LABEL = "Auto Scaling Group"


def discover_resources() -> dict:
    """
    Query AWS directly for currently available EC2 instances, ALBs and
    ASGs. Returns:

        {
          "EC2 Instance":        [ { "id": "i-...", "label": "..." } ],
          "Load Balancer":       [ { "id": "alb-name", "label": "..." } ],
          "Auto Scaling Group":  [ { "id": "asg-name", "label": "..." } ],
        }

    "id" for Load Balancer / Auto Scaling Group is the resource's own name
    (LoadBalancerName / AutoScalingGroupName), matching exactly what
    context/context_builder.py's build_resource_index() keys ALB/ASG
    resources by (alb_name / asg_name) - so an id returned here can be
    passed straight through as resource_id to POST /investigation/resource
    and Analyzer.run()/ContextBuilder.run_for_resource() will find it.

    Raises whatever the underlying boto3/botocore call raises - a failure
    here is a real AWS/API error and must surface to the caller, not be
    swallowed into an empty list (the same lesson the stale-collector-data
    fix applied to collector/autoscaling.py's discovery function).
    """

    resources = {
        EC2_LABEL: [],
        ALB_LABEL: [],
        ASG_LABEL: [],
    }

    for instance in get_instances():
        instance_id = instance.get("InstanceId")
        if not instance_id:
            continue
        resources[EC2_LABEL].append({
            "id": instance_id,
            "label": instance.get("Name") or instance_id,
        })

    for alb in discover_load_balancers():
        name = alb.get("LoadBalancerName")
        if not name:
            continue
        resources[ALB_LABEL].append({"id": name, "label": name})

    for asg in discover_auto_scaling_groups():
        name = asg.get("AutoScalingGroupName")
        if not name:
            continue
        resources[ASG_LABEL].append({"id": name, "label": name})

    for entries in resources.values():
        entries.sort(key=lambda e: e["id"])

    logger.info(
        f"Discovered {len(resources[EC2_LABEL])} EC2, "
        f"{len(resources[ALB_LABEL])} ALB, "
        f"{len(resources[ASG_LABEL])} ASG resource(s)."
    )

    return resources

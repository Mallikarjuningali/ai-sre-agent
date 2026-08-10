"""
=========================================================
AI SRE AGENT

Module:
Sanitizer

Purpose:
Clean and minimize infrastructure context before sending
it to the LLM.
=========================================================
"""

from copy import deepcopy


class Sanitizer:

    def sanitize(self, context):

        context = deepcopy(context)

        resource_type = context.get("resource_type")

        # Load Balancer / Auto Scaling Group contexts don't have the
        # cloudwatch/alb/autoscaling/cloudtrail sub-keys the four EC2
        # sanitizers key off of - each of those returns its input
        # unchanged when its key is absent, so without this dispatch an
        # ALB/ASG context would silently skip sanitization entirely and
        # reach Gemini unredacted (dns_name, vpc_id, etc.).
        if resource_type == "Load Balancer":
            return self.sanitize_load_balancer(context)

        if resource_type == "Auto Scaling Group":
            return self.sanitize_auto_scaling_group(context)

        context = self.sanitize_cloudwatch(context)

        context = self.sanitize_alb(context)

        context = self.sanitize_autoscaling(context)

        context = self.sanitize_cloudtrail(context)

        return context
        # =====================================================
    # CloudWatch
    # =====================================================

    def sanitize_cloudwatch(self, context):
        """
        cpu/memory/disk/network_in/network_out/status_check are now the
        previous-hour statistical summary (latest/minimum/maximum/average/
        trend[/threshold_breach_minutes/anomaly_detected]) - see
        utils/metric_stats.py - never the raw datapoints. A metric with no
        summary available (e.g. CloudWatch Agent not installed) is simply
        left out rather than sent as a bare number, so every value Gemini
        sees has the same trend-aware shape.
        """

        cloudwatch = context["context"].get("cloudwatch")

        if not cloudwatch:
            return context

        metric_trends = cloudwatch.get("MetricTrends") or {}

        sanitized = {"state": cloudwatch.get("State")}

        for prompt_key, trend_key in (
            ("cpu", "CPU"),
            ("memory", "Memory"),
            ("disk", "Disk"),
            ("network_in", "NetworkIn"),
            ("network_out", "NetworkOut"),
            ("status_check", "StatusCheck"),
        ):
            stats = metric_trends.get(trend_key)
            if stats:
                sanitized[prompt_key] = stats

        context["context"]["cloudwatch"] = sanitized

        return context
        # =====================================================
    # ALB
    # =====================================================

    def sanitize_alb(self, context):

        alb = context["context"].get("alb")

        if not alb:
            return context

        context["context"]["alb"] = {

            "target_health": alb.get("health"),

            "failure_reason": alb.get("reason"),

            "failure_description": alb.get("description")

        }

        return context
        # =====================================================
    # Auto Scaling
    # =====================================================

    def sanitize_autoscaling(self, context):

        autoscaling = context["context"].get("autoscaling")

        if not autoscaling:
            return context

        context["context"]["autoscaling"] = {

            "health_status": autoscaling.get("health_status"),

            "lifecycle_state": autoscaling.get("lifecycle_state"),

            "instance_type": autoscaling.get("instance_type")

        }

        return context
        # =====================================================
    # CloudTrail
    # =====================================================

    def sanitize_cloudtrail(self, context):

        events = context["context"].get("cloudtrail", [])

        if not events:
            return context

        unique_events = []

        seen = set()

        for event in events:

            event_name = event.get("event_name")

            if event_name in seen:
                continue

            seen.add(event_name)

            unique_events.append({

                "event_name": event_name,

                "service": event.get("service"),

                "error_code": event.get("error_code")

            })

        context["context"]["cloudtrail"] = unique_events

        return context
        # =====================================================
    # Load Balancer (first-class resource)
    # =====================================================

    def sanitize_load_balancer(self, context):
        """
        request_count/target_response_time/http_4xx/http_5xx are now the
        previous-hour statistical summary per metric (see
        utils/metric_stats.py), not a single snapshot value - only
        included when a summary was actually available. healthy_hosts/
        unhealthy_hosts per target group use their own trend summary when
        available, falling back to the plain current count otherwise
        (that count is still meaningful RCA signal on its own).
        """

        data = context.get("context") or {}
        trends = data.get("MetricTrends") or {}

        metrics = {}
        for prompt_key, trend_key in (
            ("request_count", "RequestCount"),
            ("target_response_time", "TargetResponseTime"),
            ("http_4xx", "HTTPCode_Target_4XX_Count"),
            ("http_5xx", "HTTPCode_Target_5XX_Count"),
        ):
            stats = trends.get(trend_key)
            if stats:
                metrics[prompt_key] = stats

        target_groups = []
        for tg in (data.get("target_groups") or []):
            tg_trends = tg.get("MetricTrends") or {}
            target_groups.append({

                "target_group_name": tg.get("target_group_name"),

                "healthy_hosts": tg_trends.get("HealthyHostCount") or tg.get("healthy_hosts"),

                "unhealthy_hosts": tg_trends.get("UnHealthyHostCount") or tg.get("unhealthy_hosts"),

                "targets": [

                    {

                        "instance_id": target.get("instance_id"),

                        "health": target.get("health"),

                        "reason": target.get("reason"),

                        "description": target.get("description"),

                    }

                    for target in (tg.get("targets") or [])

                ],

            })

        context["context"] = {

            "alb_name": data.get("alb_name"),

            "state": data.get("state"),

            "metrics": metrics,

            "target_groups": target_groups,

        }

        # dns_name / vpc_id / scheme / port are intentionally dropped -
        # same "network topology, don't send it" categorization already
        # applied to the EC2 sanitizers above.

        return context
        # =====================================================
    # Auto Scaling Group (first-class resource)
    # =====================================================

    def sanitize_auto_scaling_group(self, context):
        """
        desired_capacity/in_service_instances/pending_instances are now
        the previous-hour statistical summary per metric (see
        utils/metric_stats.py) - desired_capacity falls back to the plain
        current value if no trend summary was available (e.g. an ASG that
        was only just created). scaling_activities is reused as-is (it's
        already a recent-activity list, not a single value needing a
        trend summary of its own).
        """

        data = context.get("context") or {}
        trends = data.get("MetricTrends") or {}

        context["context"] = {

            "asg_name": data.get("asg_name"),

            "min_size": data.get("min_size"),

            "max_size": data.get("max_size"),

            "desired_capacity": trends.get("DesiredCapacity") or data.get("desired_capacity"),

            "health_check_type": data.get("health_check_type"),

            "instances": [

                {

                    "instance_id": instance.get("instance_id"),

                    "lifecycle_state": instance.get("lifecycle_state"),

                    "health_status": instance.get("health_status"),

                }

                for instance in (data.get("instances") or [])

            ],

            "scaling_activities": [

                {

                    "status": activity.get("status"),

                    "description": activity.get("description"),

                }

                for activity in (data.get("scaling_activities") or [])

            ],

        }

        for prompt_key, trend_key in (
            ("in_service_instances", "InServiceInstances"),
            ("pending_instances", "PendingInstances"),
        ):
            stats = trends.get(trend_key)
            if stats:
                context["context"][prompt_key] = stats

        # availability_zones / scaling_policies are dropped for the same
        # reason as EC2's own autoscaling sub-block (low RCA value); no
        # ARNs ever reach this stage - target_group_ARNs were already
        # excluded back in context_builder.py.

        return context

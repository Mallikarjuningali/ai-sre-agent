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

        context = self.sanitize_cloudwatch(context)

        context = self.sanitize_alb(context)

        context = self.sanitize_autoscaling(context)

        context = self.sanitize_cloudtrail(context)

        return context
        # =====================================================
    # CloudWatch
    # =====================================================

    def sanitize_cloudwatch(self, context):

        cloudwatch = context["context"].get("cloudwatch")

        if not cloudwatch:
            return context

        context["context"]["cloudwatch"] = {

            "state": cloudwatch.get("State"),

            "cpu": cloudwatch.get("CPU"),

            "memory": cloudwatch.get("Memory"),

            "disk": cloudwatch.get("Disk"),

            "status_check": cloudwatch.get("StatusCheck")

        }

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

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
        # =====================================================
    # Load Balancer (first-class resource)
    # =====================================================

    def sanitize_load_balancer(self, context):

        data = context.get("context") or {}

        context["context"] = {

            "alb_name": data.get("alb_name"),

            "state": data.get("state"),

            "metrics": data.get("metrics") or {},

            "target_groups": [

                {

                    "target_group_name": tg.get("target_group_name"),

                    "healthy_hosts": tg.get("healthy_hosts"),

                    "unhealthy_hosts": tg.get("unhealthy_hosts"),

                    "targets": [

                        {

                            "instance_id": target.get("instance_id"),

                            "health": target.get("health"),

                            "reason": target.get("reason"),

                            "description": target.get("description"),

                        }

                        for target in (tg.get("targets") or [])

                    ],

                }

                for tg in (data.get("target_groups") or [])

            ],

        }

        # dns_name / vpc_id / scheme / port are intentionally dropped -
        # same "network topology, don't send it" categorization already
        # applied to the EC2 sanitizers above.

        return context
        # =====================================================
    # Auto Scaling Group (first-class resource)
    # =====================================================

    def sanitize_auto_scaling_group(self, context):

        data = context.get("context") or {}

        context["context"] = {

            "asg_name": data.get("asg_name"),

            "min_size": data.get("min_size"),

            "max_size": data.get("max_size"),

            "desired_capacity": data.get("desired_capacity"),

            "health_check_type": data.get("health_check_type"),

            "metrics": data.get("metrics") or {},

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

        # availability_zones / scaling_policies are dropped for the same
        # reason as EC2's own autoscaling sub-block (low RCA value); no
        # ARNs ever reach this stage - target_group_ARNs were already
        # excluded back in context_builder.py.

        return context

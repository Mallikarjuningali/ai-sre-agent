import boto3

client = boto3.client("cloudtrail", region_name="us-east-1")

IMPORTANT_EVENTS = {
    "StopInstances",
    "StartInstances",
    "RebootInstances",
    "TerminateInstances",
    "AuthorizeSecurityGroupIngress",
    "RevokeSecurityGroupIngress",
    "ModifySecurityGroupRules",
    "CreateSecurityGroup",
    "DeleteSecurityGroup"
}

response = client.lookup_events(MaxResults=100)

print("\n===== Important AWS Events =====\n")

found = False

for event in response["Events"]:
    if event["EventName"] in IMPORTANT_EVENTS:
        found = True
        print(f"Event : {event['EventName']}")
        print(f"Time  : {event['EventTime']}")
        print(f"User  : {event.get('Username','N/A')}")
        print("-"*50)

if not found:
    print("No important infrastructure events found.")

#!/usr/bin/env python3
"""Print endpoint state and the newest container log lines (debug helper, no writes)."""
import sys

import boto3

R, EP = "us-east-1", "evp-music"
n = int(sys.argv[1]) if len(sys.argv) > 1 else 15
e = boto3.client("sagemaker", region_name=R).describe_endpoint(EndpointName=EP)
v = (e.get("ProductionVariants") or [{}])[0]
print(e["EndpointStatus"], e.get("FailureReason", ""), "instances", v.get("CurrentInstanceCount"),
      "type", v.get("CurrentInstanceType") or [p.get("InstanceType") for p in v.get("InstancePools", [])
                                                if p.get("CurrentInstanceCount")])
logs = boto3.client("logs", region_name=R)
g = f"/aws/sagemaker/Endpoints/{EP}"
try:
    streams = logs.describe_log_streams(logGroupName=g, orderBy="LastEventTime", descending=True,
                                        limit=2)["logStreams"]
except logs.exceptions.ResourceNotFoundException:
    streams = []
for s in streams:
    print("--", s["logStreamName"])
    for ev in logs.get_log_events(logGroupName=g, logStreamName=s["logStreamName"], limit=n,
                                  startFromHead=False)["events"]:
        print("  ", ev["message"][:240])

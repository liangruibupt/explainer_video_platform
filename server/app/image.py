"""Client for the Qwen-Image async SageMaker endpoint (infra/image).

generate() writes the request JSON to S3, calls InvokeEndpointAsync and polls the S3
output, so it works the same whether the endpoint is warm or still starting from 0.
Credentials come from the machine's AWS profile or role.
"""
import base64
import json
import os
import time
import uuid

import boto3

REGION = os.environ.get("EVP_IMAGE_REGION", "us-east-1")
ENDPOINT = os.environ.get("EVP_IMAGE_ENDPOINT", "evp-image")
_acct = None


def _bucket() -> str:
    global _acct
    if _acct is None:
        _acct = boto3.client("sts").get_caller_identity()["Account"]
    return f"evp-models-{_acct}-{REGION}"


def status() -> dict:
    """{'deployed', 'state', 'instances', 'desired'}; 'ready' when an instance is up."""
    sm = boto3.client("sagemaker", region_name=REGION)
    try:
        e = sm.describe_endpoint(EndpointName=ENDPOINT)
    except sm.exceptions.ClientError:
        return {"deployed": False, "state": "not_deployed", "instances": 0, "desired": 0}
    v = e["ProductionVariants"][0]
    cur, des = v.get("CurrentInstanceCount", 0), v.get("DesiredInstanceCount", 0)
    state = ("failed" if e["EndpointStatus"] == "Failed" else
             "ready" if cur >= 1 else "starting" if des >= 1 else "idle")
    return {"deployed": True, "state": state, "instances": cur, "desired": des,
            "instance_type": (v.get("CurrentInstanceType") or v.get("InstanceType") or "")}


def submit(req: dict) -> dict:
    """Queue one request; returns the S3 locations to poll."""
    s3 = boto3.client("s3", region_name=REGION)
    key = f"jobs/image/in/{uuid.uuid4().hex}.json"
    s3.put_object(Bucket=_bucket(), Key=key, Body=json.dumps(req).encode())
    r = boto3.client("sagemaker-runtime", region_name=REGION).invoke_endpoint_async(
        EndpointName=ENDPOINT, InputLocation=f"s3://{_bucket()}/{key}",
        ContentType="application/json", InvocationTimeoutSeconds=3600)
    return {"out": r["OutputLocation"], "fail": r.get("FailureLocation", "")}


def poll(loc: dict):
    """None while pending, else the decoded result dict. Raises on a failed request."""
    s3 = boto3.client("s3", region_name=REGION)
    for where, ok in ((loc["out"], True), (loc["fail"], False)):
        if not where:
            continue
        b, k = where.removeprefix("s3://").split("/", 1)
        try:
            body = s3.get_object(Bucket=b, Key=k)["Body"].read()
        except s3.exceptions.NoSuchKey:
            continue
        if not ok:
            raise RuntimeError("image endpoint error: " + body.decode()[:500])
        return json.loads(body)
    return None


def generate(prompt: str, ratio: str = "16:9", mode: str = "t2i", seed: int = 42,
             steps: int = 40, n: int = 1, timeout: int = 2400, on_wait=None) -> list:
    """Blocking: returns a list of PNG bytes. on_wait(seconds) is called while waiting."""
    loc = submit({"mode": mode, "prompt": prompt, "ratio": ratio, "seed": seed,
                  "steps": steps, "n": n})
    t0 = time.time()
    while time.time() - t0 < timeout:
        res = poll(loc)
        if res is not None:
            return [base64.b64decode(x) for x in res["images"]]
        if on_wait:
            on_wait(int(time.time() - t0))
        time.sleep(10)
    raise TimeoutError(f"image endpoint gave no result in {timeout} s")

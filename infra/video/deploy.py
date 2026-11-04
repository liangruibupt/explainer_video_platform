#!/usr/bin/env python3
"""Deploy the MiniMax-H3 video async endpoint (evp-video, ap-south-1) that scales to 0.

  deploy.py plan                 show the GPU pool order (GPU + host-RAM check, price, cheapest first)
  deploy.py build                build the container and push it to ECR (run where there is disk)
  deploy.py up --weights S3URI   create role, model, endpoint config, endpoint, scaling
  deploy.py status               endpoint state, instance count, backlog
  deploy.py smoke [--mode t2va]  send one 768p 16:9 5 s clip, time it, save the MP4 to data/
  deploy.py down                 delete endpoint, config, model, scaling, alarm (keeps S3 and ECR)

Credentials come from the machine's AWS profile or role. Nothing secret is stored here.
"""
import argparse, base64, datetime as dt, json, os, subprocess, sys, time, uuid
from pathlib import Path

import boto3

HERE = Path(__file__).resolve().parent
CFG = json.loads((HERE / "endpoint.json").read_text())
R = CFG["region"]; EP = CFG["endpoint"]
ACCT = boto3.client("sts").get_caller_identity()["Account"]
BUCKET = f"evp-models-{ACCT}-{R}"
REPO = f"{ACCT}.dkr.ecr.{R}.amazonaws.com/evp/minimax-h3"
ROLE = "evp-video-endpoint"
TAGS = [{"Key": k, "Value": v} for k, v in CFG["tags"].items()]
sm = boto3.client("sagemaker", region_name=R)
aas = boto3.client("application-autoscaling", region_name=R)
cw = boto3.client("cloudwatch", region_name=R)
RES = f"endpoint/{EP}/variant/main"


def instance_info(instance):
    t = boto3.client("ec2", region_name=R).describe_instance_types(
        InstanceTypes=[instance.removeprefix("ml.")])["InstanceTypes"][0]
    gpu = sum(g["MemoryInfo"]["SizeInMiB"] * g["Count"] for g in t["GpuInfo"]["Gpus"]) / 1024
    ram = t["MemoryInfo"]["SizeInMiB"] / 1024
    return gpu, ram


def sm_price(instance):
    """SageMaker on-demand hourly price for the region, or inf if the API has no row."""
    p = boto3.client("pricing", region_name="us-east-1").get_products(
        ServiceCode="AmazonSageMaker", MaxResults=20, Filters=[
            {"Type": "TERM_MATCH", "Field": "instanceName", "Value": instance},
            {"Type": "TERM_MATCH", "Field": "regionCode", "Value": R}])["PriceList"]
    for s in map(json.loads, p):
        if "Host:" in s["product"]["attributes"].get("usagetype", ""):
            for t in s["terms"]["OnDemand"].values():
                return float(next(iter(t["priceDimensions"].values()))["pricePerUnit"]["USD"])
    return float("inf")


def ec2_price(instance):
    """EC2 on-demand hourly price for the region -- the fallback when SageMaker has no row."""
    ec2type = instance.removeprefix("ml.")
    p = boto3.client("pricing", region_name="us-east-1").get_products(
        ServiceCode="AmazonEC2", MaxResults=40, Filters=[
            {"Type": "TERM_MATCH", "Field": "instanceType", "Value": ec2type},
            {"Type": "TERM_MATCH", "Field": "regionCode", "Value": R},
            {"Type": "TERM_MATCH", "Field": "tenancy", "Value": "Shared"},
            {"Type": "TERM_MATCH", "Field": "operatingSystem", "Value": "Linux"},
            {"Type": "TERM_MATCH", "Field": "preInstalledSw", "Value": "NA"},
            {"Type": "TERM_MATCH", "Field": "capacitystatus", "Value": "Used"}])["PriceList"]
    for s in map(json.loads, p):
        for t in s.get("terms", {}).get("OnDemand", {}).values():
            return float(next(iter(t["priceDimensions"].values()))["pricePerUnit"]["USD"])
    return float("inf")


def plan(show=True):
    rows = []
    for c in CFG["candidates"]:
        gpu, ram = instance_info(c["instance"])
        usd, src = sm_price(c["instance"]), "SageMaker"
        if usd == float("inf"):
            usd, src = ec2_price(c["instance"]), "EC2 fallback"
        ok = gpu >= CFG["gpu_gb_needed"] and ram >= CFG["host_ram_gb_needed"]
        rows.append(dict(c, gpu_gb=round(gpu), ram_gb=round(ram), usd_h=usd, price_src=src, ok=ok))
    pool = sorted((r for r in rows if r["ok"]), key=lambda r: r["usd_h"])
    if show:
        for r in sorted(rows, key=lambda r: r["usd_h"]):
            pri = next((j + 1 for j, p in enumerate(pool) if p is r), "-")
            why = "ok" if r["ok"] else (
                "GPU < %d" % CFG["gpu_gb_needed"] if r["gpu_gb"] < CFG["gpu_gb_needed"]
                else "RAM < %d" % CFG["host_ram_gb_needed"])
            print(f"  priority {pri}  {r['instance']:16} gpu={r['gpu_gb']:>3}GB "
                  f"ram={r['ram_gb']:>3}GB  ${r['usd_h']:.2f}/h ({r['price_src']})  {why}")
    return pool


def build():
    # SageMaker rejects OCI image manifests, so build with buildx and emit a Docker v2
    # manifest (oci-mediatypes=false). --provenance=false avoids the attestation manifest
    # that turns the result into an OCI image index.
    tag = "adapter-" + dt.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    pw = subprocess.check_output(["aws", "ecr", "get-login-password", "--region", R])
    subprocess.run(["docker", "login", "-u", "AWS", "--password-stdin", REPO.split("/")[0]],
                   input=pw, check=True)
    subprocess.run(["docker", "buildx", "build", "--platform", "linux/amd64",
                    "--provenance=false", "--output", "type=registry,oci-mediatypes=false",
                    "-t", f"{REPO}:{tag}", str(HERE / "container")], check=True)
    (HERE / ".image_tag").write_text(tag)
    print("pushed", f"{REPO}:{tag}")


def ensure_role():
    iam = boto3.client("iam")
    trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": "sts:AssumeRole",
             "Principal": {"Service": "sagemaker.amazonaws.com"}}]}
    try:
        arn = iam.get_role(RoleName=ROLE)["Role"]["Arn"]
    except iam.exceptions.NoSuchEntityException:
        arn = iam.create_role(RoleName=ROLE, AssumeRolePolicyDocument=json.dumps(trust),
                              Tags=TAGS)["Role"]["Arn"]
    # Least privilege: read the weights, write async job outputs, pull this repo, own logs/metrics.
    policy = {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": ["s3:GetObject"], "Resource": f"arn:aws:s3:::{BUCKET}/*"},
        {"Effect": "Allow", "Action": ["s3:PutObject"],
         "Resource": f"arn:aws:s3:::{BUCKET}/jobs/*"},
        {"Effect": "Allow", "Action": "s3:ListBucket", "Resource": f"arn:aws:s3:::{BUCKET}"},
        {"Effect": "Allow", "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
         "Resource": f"arn:aws:ecr:{R}:{ACCT}:repository/evp/minimax-h3"},
        {"Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*"},
        {"Effect": "Allow", "Action": ["logs:CreateLogGroup", "logs:CreateLogStream",
                                       "logs:PutLogEvents", "logs:DescribeLogStreams"],
         "Resource": f"arn:aws:logs:{R}:{ACCT}:log-group:/aws/sagemaker/Endpoints/{EP}*"},
        {"Effect": "Allow", "Action": "cloudwatch:PutMetricData", "Resource": "*"}]}
    iam.put_role_policy(RoleName=ROLE, PolicyName="evp-video", PolicyDocument=json.dumps(policy))
    return arn


def up(weights):
    if not weights.endswith("/"):
        sys.exit("--weights must be an S3 prefix ending with /")
    tag = (HERE / ".image_tag").read_text().strip()
    role = ensure_role(); time.sleep(10)  # IAM propagation
    pool = plan()
    if not pool:
        sys.exit("no candidate GPU meets the GPU/RAM thresholds")
    stamp = dt.datetime.utcnow().strftime("%m%d%H%M")
    model = f"{EP}-{stamp}"
    # NVFP4 + Cache-DiT envs go on the Model Environment; the adapter inherits them.
    env = {
        "SGLANG_USE_RUNAI_MODEL_STREAMER": "0",
        "SGLANG_DIFFUSION_FLASHINFER_FP4_GEMM_BACKEND": "auto",
        "H3_FP4_TMA_SCALES": "1",
        "H3_FP4_QKV_FIX": "0",
        "SGLANG_CACHE_DIT_ENABLED": "1",
        "SGLANG_CACHE_DIT_WARMUP": "2",
        "SGLANG_CACHE_DIT_SECONDARY_WARMUP": "2",
        "SGLANG_CACHE_DIT_RDT": "0.24",
        "SGLANG_CACHE_DIT_SECONDARY_RDT": "0.24",
    }
    sm.create_model(ModelName=model, ExecutionRoleArn=role, Tags=TAGS, PrimaryContainer={
        "Image": f"{REPO}:{tag}", "Environment": env,
        "ModelDataSource": {"S3DataSource": {"S3Uri": weights, "S3DataType": "S3Prefix",
                                             "CompressionType": "None"}}})
    variant = {
        "VariantName": "main", "ModelName": model, "InitialInstanceCount": 1,
        "InstancePools": [{"InstanceType": p["instance"], "Priority": i + 1}
                          for i, p in enumerate(pool)],
        "VariantInstanceProvisionTimeoutInSeconds": CFG["provision_timeout_s"],
        "InferenceAmiVersion": CFG["inference_ami"], "VolumeSizeInGB": CFG["volume_gb"],
        "ModelDataDownloadTimeoutInSeconds": CFG["download_timeout_s"],
        "ContainerStartupHealthCheckTimeoutInSeconds": CFG["startup_timeout_s"]}
    cfg = f"{EP}-{stamp}"
    sm.create_endpoint_config(EndpointConfigName=cfg, ProductionVariants=[variant], Tags=TAGS,
        AsyncInferenceConfig={
            "OutputConfig": {"S3OutputPath": f"s3://{BUCKET}/jobs/video/out/",
                             "S3FailurePath": f"s3://{BUCKET}/jobs/video/fail/"},
            "ClientConfig": {"MaxConcurrentInvocationsPerInstance":
                             CFG["max_concurrent_per_instance"]}})
    try:
        sm.describe_endpoint(EndpointName=EP)
        sm.update_endpoint(EndpointName=EP, EndpointConfigName=cfg)
    except sm.exceptions.ClientError:
        sm.create_endpoint(EndpointName=EP, EndpointConfigName=cfg, Tags=TAGS)
    print("creating endpoint", EP, "(first start includes the ~90 GB weight download)")


def scaling():
    aas.register_scalable_target(ServiceNamespace="sagemaker", ResourceId=RES,
        ScalableDimension="sagemaker:variant:DesiredInstanceCount",
        MinCapacity=0, MaxCapacity=CFG["max_instances"])
    aas.put_scaling_policy(PolicyName=f"{EP}-backlog", ServiceNamespace="sagemaker",
        ResourceId=RES, ScalableDimension="sagemaker:variant:DesiredInstanceCount",
        PolicyType="TargetTrackingScaling", TargetTrackingScalingPolicyConfiguration={
            "TargetValue": 1.0, "ScaleInCooldown": CFG["scale_in_idle_s"], "ScaleOutCooldown": 60,
            "CustomizedMetricSpecification": {
                "MetricName": "ApproximateBacklogSizePerInstance", "Namespace": "AWS/SageMaker",
                "Dimensions": [{"Name": "EndpointName", "Value": EP}], "Statistic": "Average"}})
    arn = aas.put_scaling_policy(PolicyName=f"{EP}-from-zero", ServiceNamespace="sagemaker",
        ResourceId=RES, ScalableDimension="sagemaker:variant:DesiredInstanceCount",
        PolicyType="StepScaling", StepScalingPolicyConfiguration={
            "AdjustmentType": "ChangeInCapacity", "MetricAggregationType": "Average",
            "Cooldown": 300, "StepAdjustments": [{"MetricIntervalLowerBound": 0,
                                                  "ScalingAdjustment": 1}]})["PolicyARN"]
    cw.put_metric_alarm(AlarmName=f"{EP}-has-backlog-without-capacity", AlarmActions=[arn],
        MetricName="HasBacklogWithoutCapacity", Namespace="AWS/SageMaker",
        Dimensions=[{"Name": "EndpointName", "Value": EP}], Statistic="Average", Period=60,
        EvaluationPeriods=1, DatapointsToAlarm=1, Threshold=1,
        ComparisonOperator="GreaterThanOrEqualToThreshold", TreatMissingData="missing", Tags=TAGS)


def wait_in_service():
    print("waiting for endpoint InService...")
    sm.get_waiter("endpoint_in_service").wait(EndpointName=EP,
                                              WaiterConfig={"Delay": 30, "MaxAttempts": 240})
    scaling()
    print("endpoint", EP, "InService; scales to 0 after", CFG["scale_in_idle_s"], "s idle")


def status():
    try:
        e = sm.describe_endpoint(EndpointName=EP)
    except sm.exceptions.ClientError:
        print("endpoint not deployed"); return
    vs = e.get("ProductionVariants") or []
    if not vs:  # Creating: the variant list is not populated yet
        print(f"{EP}: {e['EndpointStatus']}  {e.get('FailureReason', '')}")
        return
    v = vs[0]
    print(f"{EP}: {e['EndpointStatus']}  instances={v.get('CurrentInstanceCount')} "
          f"desired={v.get('DesiredInstanceCount')}  {e.get('FailureReason', '')}")
    for p in v.get("InstancePools", []) or []:
        print("  pool", p)
    now = dt.datetime.utcnow()
    for m in ("ApproximateBacklogSize", "HasBacklogWithoutCapacity"):
        d = cw.get_metric_statistics(Namespace="AWS/SageMaker", MetricName=m, Period=60,
            StartTime=now - dt.timedelta(minutes=10), EndTime=now, Statistics=["Maximum"],
            Dimensions=[{"Name": "EndpointName", "Value": EP}])["Datapoints"]
        last = max(d, key=lambda x: x["Timestamp"])["Maximum"] if d else "no data"
        print(f"  {m}: {last}")


def invoke(req, out_mp4, timeout=1800):
    s3 = boto3.client("s3", region_name=R)
    key = f"jobs/video/in/{uuid.uuid4().hex}.json"
    s3.put_object(Bucket=BUCKET, Key=key, Body=json.dumps(req).encode())
    t0 = time.time()
    r = boto3.client("sagemaker-runtime", region_name=R).invoke_endpoint_async(
        EndpointName=EP, InputLocation=f"s3://{BUCKET}/{key}", ContentType="application/json",
        InvocationTimeoutSeconds=CFG["invocation_timeout_s"])
    out, fail = r["OutputLocation"], r.get("FailureLocation", "")
    while time.time() - t0 < timeout:
        for loc, ok in ((out, True), (fail, False)):
            if not loc:
                continue
            b, k = loc.removeprefix("s3://").split("/", 1)
            try:
                body = s3.get_object(Bucket=b, Key=k)["Body"].read()
            except s3.exceptions.NoSuchKey:
                continue
            if not ok:
                sys.exit("failed: " + body.decode()[:2000])
            res = json.loads(body)
            Path(out_mp4).write_bytes(base64.b64decode(res.pop("video_b64")))
            res["wall_s"] = round(time.time() - t0)
            return res
        time.sleep(10)
    sys.exit("timed out")


def smoke(mode="t2va"):
    out = HERE.parents[1] / "data" / "video_smoke"; out.mkdir(parents=True, exist_ok=True)
    req = {"mode": mode, "aspect": "16:9", "short_edge": 768, "duration": 5.0, "steps": 8,
           "seed": 7,
           "prompt": "西湖清晨，薄雾笼罩湖面，远处苏堤的柳树和石拱桥，一只小船缓缓划过，电影感，平稳镜头"}
    res = invoke(req, str(out / "smoke_0.mp4"))
    print(json.dumps(res, ensure_ascii=False))


def down():
    for name in (f"{EP}-backlog", f"{EP}-from-zero"):
        try:
            aas.delete_scaling_policy(PolicyName=name, ServiceNamespace="sagemaker", ResourceId=RES,
                                      ScalableDimension="sagemaker:variant:DesiredInstanceCount")
        except aas.exceptions.ObjectNotFoundException:
            pass
    cw.delete_alarms(AlarmNames=[f"{EP}-has-backlog-without-capacity"])
    try:
        aas.deregister_scalable_target(ServiceNamespace="sagemaker", ResourceId=RES,
                                       ScalableDimension="sagemaker:variant:DesiredInstanceCount")
    except aas.exceptions.ObjectNotFoundException:
        pass
    try:
        sm.describe_endpoint(EndpointName=EP)
        sm.delete_endpoint(EndpointName=EP)
        sm.get_waiter("endpoint_deleted").wait(EndpointName=EP)
    except sm.exceptions.ClientError:
        pass
    for c in sm.list_endpoint_configs(NameContains=EP)["EndpointConfigs"]:
        sm.delete_endpoint_config(EndpointConfigName=c["EndpointConfigName"])
    for m in sm.list_models(NameContains=EP)["Models"]:
        sm.delete_model(ModelName=m["ModelName"])
    print("deleted endpoint, configs, model, scaling and alarm; S3 weights and ECR image kept")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["plan", "build", "up", "wait", "status", "smoke", "down"])
    ap.add_argument("--weights", help="S3 prefix of the uncompressed model directory (ends with /)")
    ap.add_argument("--mode", default="t2va", choices=["t2va", "fl2va"])
    a = ap.parse_args()
    {"plan": plan, "build": build, "status": status, "down": down, "wait": wait_in_service,
     "smoke": lambda: smoke(a.mode),
     "up": lambda: up(a.weights or sys.exit("--weights required"))}[a.cmd]()

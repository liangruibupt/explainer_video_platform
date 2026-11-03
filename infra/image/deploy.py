#!/usr/bin/env python3
"""Deploy the Qwen-Image 2.1 async endpoint that scales to 0.

  deploy.py plan                 show the GPU pool order (memory check + price, cheapest first)
  deploy.py build                build the container and push it to ECR
  deploy.py up --weights S3URI   create role, models, endpoint config, endpoint, scaling
  deploy.py status               endpoint state, instance count, backlog
  deploy.py smoke                send one 16:9 image, time it, save it to data/
  deploy.py down                 delete endpoint, config, models, alarms (keeps S3 and ECR)

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
REPO = f"{ACCT}.dkr.ecr.{R}.amazonaws.com/evp/qwen-image"
ROLE = "evp-image-endpoint"
TAGS = [{"Key": k, "Value": v} for k, v in CFG["tags"].items()]
sm = boto3.client("sagemaker", region_name=R)
aas = boto3.client("application-autoscaling", region_name=R)
cw = boto3.client("cloudwatch", region_name=R)
RES = f"endpoint/{EP}/variant/main"


def gpu_gb(instance):
    t = boto3.client("ec2", region_name=R).describe_instance_types(
        InstanceTypes=[instance.removeprefix("ml.")])["InstanceTypes"][0]
    return sum(g["MemoryInfo"]["SizeInMiB"] * g["Count"] for g in t["GpuInfo"]["Gpus"]) / 1024


def price(instance):
    p = boto3.client("pricing", region_name="us-east-1").get_products(
        ServiceCode="AmazonSageMaker", MaxResults=20, Filters=[
            {"Type": "TERM_MATCH", "Field": "instanceName", "Value": instance},
            {"Type": "TERM_MATCH", "Field": "regionCode", "Value": R}])["PriceList"]
    for s in map(json.loads, p):
        if "Host:" in s["product"]["attributes"].get("usagetype", ""):
            for t in s["terms"]["OnDemand"].values():
                return float(next(iter(t["priceDimensions"].values()))["pricePerUnit"]["USD"])
    return float("inf")


def plan(show=True):
    rows = []
    for c in CFG["candidates"]:
        gb, usd = gpu_gb(c["instance"]), price(c["instance"])
        ok = gb >= CFG["gpu_gb_needed"][c["mem_mode"]]
        rows.append(dict(c, gpu_gb=round(gb), usd_h=usd, ok=ok))
    pool = sorted((r for r in rows if r["ok"]), key=lambda r: r["usd_h"])
    if show:
        for i, r in enumerate(sorted(rows, key=lambda r: r["usd_h"])):
            pri = next((j + 1 for j, p in enumerate(pool) if p is r), "-")
            print(f"  priority {pri}  {r['instance']:16} {r['gpu_gb']:>3} GB  ${r['usd_h']:.2f}/h  "
                  f"mode={r['mem_mode']:8} {'ok' if r['ok'] else 'NOT ENOUGH GPU MEMORY'}")
    return pool


def build():
    # SageMaker rejects OCI image manifests, so build with buildx and emit a Docker v2
    # manifest (oci-mediatypes=false) pushed straight to ECR. --provenance=false avoids
    # the attestation manifest that turns the result into an OCI image index.
    tag = dt.datetime.utcnow().strftime("%Y%m%d-%H%M%S") + "-amd64"
    pw = subprocess.check_output(["aws", "ecr", "get-login-password", "--region", R])
    subprocess.run(["docker", "login", "-u", "AWS", "--password-stdin", REPO.split("/")[0]],
                   input=pw, check=True)
    subprocess.run(["docker", "buildx", "build", "--builder", "evpamd64",
                    "--platform", "linux/amd64", "--provenance=false",
                    "--output", "type=registry,oci-mediatypes=false",
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
    # Least privilege: this project's bucket, this repo, and its own logs/metrics.
    policy = {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject"],
         "Resource": f"arn:aws:s3:::{BUCKET}/*"},
        {"Effect": "Allow", "Action": "s3:ListBucket", "Resource": f"arn:aws:s3:::{BUCKET}"},
        {"Effect": "Allow", "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
         "Resource": f"arn:aws:ecr:{R}:{ACCT}:repository/evp/qwen-image"},
        {"Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*"},
        {"Effect": "Allow", "Action": ["logs:CreateLogGroup", "logs:CreateLogStream",
                                       "logs:PutLogEvents", "logs:DescribeLogStreams"],
         "Resource": f"arn:aws:logs:{R}:{ACCT}:log-group:/aws/sagemaker/Endpoints/{EP}*"},
        {"Effect": "Allow", "Action": "cloudwatch:PutMetricData", "Resource": "*"}]}
    iam.put_role_policy(RoleName=ROLE, PolicyName="evp-image", PolicyDocument=json.dumps(policy))
    return arn


def up(weights):
    if not weights.endswith("/"):
        sys.exit("--weights must be an S3 prefix ending with /")
    tag = (HERE / ".image_tag").read_text().strip()
    role = ensure_role(); time.sleep(10)  # IAM propagation
    pool = plan()
    if not pool:
        sys.exit("no candidate GPU has enough memory")
    stamp = dt.datetime.utcnow().strftime("%m%d%H%M")
    models = {}
    for mode in sorted({p["mem_mode"] for p in pool}):
        name = f"{EP}-{mode}-{stamp}"
        sm.create_model(ModelName=name, ExecutionRoleArn=role, Tags=TAGS, PrimaryContainer={
            "Image": f"{REPO}:{tag}", "Environment": {"EVP_MEM_MODE": mode},
            "ModelDataSource": {"S3DataSource": {"S3Uri": weights, "S3DataType": "S3Prefix",
                                                 "CompressionType": "None"}}})
        models[mode] = name
    first = pool[0]
    variant = {
        "VariantName": "main", "ModelName": models[first["mem_mode"]], "InitialInstanceCount": 1,
        "InstancePools": [{"InstanceType": p["instance"], "Priority": i + 1,
                           **({"ModelNameOverride": models[p["mem_mode"]]}
                              if p["mem_mode"] != first["mem_mode"] else {})}
                          for i, p in enumerate(pool)],
        "VariantInstanceProvisionTimeoutInSeconds": CFG["provision_timeout_s"],
        "InferenceAmiVersion": CFG["inference_ami"], "VolumeSizeInGB": CFG["volume_gb"],
        "ModelDataDownloadTimeoutInSeconds": CFG["download_timeout_s"],
        "ContainerStartupHealthCheckTimeoutInSeconds": CFG["startup_timeout_s"]}
    cfg = f"{EP}-{stamp}"
    sm.create_endpoint_config(EndpointConfigName=cfg, ProductionVariants=[variant], Tags=TAGS,
        AsyncInferenceConfig={
            "OutputConfig": {"S3OutputPath": f"s3://{BUCKET}/jobs/image/out/",
                             "S3FailurePath": f"s3://{BUCKET}/jobs/image/fail/"},
            "ClientConfig": {"MaxConcurrentInvocationsPerInstance":
                             CFG["max_concurrent_per_instance"]}})
    try:
        sm.describe_endpoint(EndpointName=EP)
        sm.update_endpoint(EndpointName=EP, EndpointConfigName=cfg)
    except sm.exceptions.ClientError:
        sm.create_endpoint(EndpointName=EP, EndpointConfigName=cfg, Tags=TAGS)
    print("waiting for endpoint InService (first start includes the weight download)...")
    sm.get_waiter("endpoint_in_service").wait(EndpointName=EP,
                                              WaiterConfig={"Delay": 30, "MaxAttempts": 120})
    scaling()
    print("endpoint", EP, "InService; scales to 0 after", CFG["scale_in_idle_s"], "s idle")


def scaling():
    aas.register_scalable_target(ServiceNamespace="sagemaker", ResourceId=RES,
        ScalableDimension="sagemaker:variant:DesiredInstanceCount",
        MinCapacity=0, MaxCapacity=CFG["max_instances"])
    # Scale back to 0 when the queue is empty. Target tracking on backlog per instance;
    # its scale-in alarm needs N empty datapoints, so the idle time is set by the cooldown.
    aas.put_scaling_policy(PolicyName=f"{EP}-backlog", ServiceNamespace="sagemaker",
        ResourceId=RES, ScalableDimension="sagemaker:variant:DesiredInstanceCount",
        PolicyType="TargetTrackingScaling", TargetTrackingScalingPolicyConfiguration={
            "TargetValue": 1.0, "ScaleInCooldown": CFG["scale_in_idle_s"], "ScaleOutCooldown": 60,
            "CustomizedMetricSpecification": {
                "MetricName": "ApproximateBacklogSizePerInstance", "Namespace": "AWS/SageMaker",
                "Dimensions": [{"Name": "EndpointName", "Value": EP}], "Statistic": "Average"}})
    # Start from 0: target tracking cannot divide by 0 instances, so a step policy
    # on HasBacklogWithoutCapacity adds the first instance.
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


def status():
    try:
        e = sm.describe_endpoint(EndpointName=EP)
    except sm.exceptions.ClientError:
        print("endpoint not deployed"); return
    v = e["ProductionVariants"][0]
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


def invoke(req, out_png, timeout=3600):
    s3 = boto3.client("s3", region_name=R)
    key = f"jobs/image/in/{uuid.uuid4().hex}.json"
    s3.put_object(Bucket=BUCKET, Key=key, Body=json.dumps(req).encode())
    t0 = time.time()
    r = boto3.client("sagemaker-runtime", region_name=R).invoke_endpoint_async(
        EndpointName=EP, InputLocation=f"s3://{BUCKET}/{key}", ContentType="application/json",
        InvocationTimeoutSeconds=3600)
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
            for i, im in enumerate(res["images"]):
                Path(out_png.format(i)).write_bytes(base64.b64decode(im))
            res["images"] = len(res["images"]); res["wall_s"] = round(time.time() - t0)
            return res
        time.sleep(15)
    sys.exit("timed out")


def smoke():
    out = HERE.parents[1] / "data" / "image_smoke"; out.mkdir(parents=True, exist_ok=True)
    res = invoke({"mode": "t2i", "ratio": "16:9", "steps": 40, "seed": 7,
                  "prompt": "北宋山水长卷风格，远山层叠，江面开阔，一叶渔舟，淡墨设色，绢本质感，画面中没有任何文字"},
                 str(out / "smoke_{}.png"))
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
        cfg = sm.describe_endpoint(EndpointName=EP)["EndpointConfigName"]
        sm.delete_endpoint(EndpointName=EP)
        sm.get_waiter("endpoint_deleted").wait(EndpointName=EP)
    except sm.exceptions.ClientError:
        cfg = None
    for c in sm.list_endpoint_configs(NameContains=EP)["EndpointConfigs"]:
        sm.delete_endpoint_config(EndpointConfigName=c["EndpointConfigName"])
    for m in sm.list_models(NameContains=EP)["Models"]:
        sm.delete_model(ModelName=m["ModelName"])
    print("deleted endpoint, configs, models, scaling and alarm; S3 weights and ECR image kept")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("cmd", choices=["plan", "build", "up",
                                                                     "status", "smoke", "down"])
    ap.add_argument("--weights", help="S3 prefix of the uncompressed model directory (ends with /)")
    a = ap.parse_args()
    {"plan": plan, "build": build, "status": status, "smoke": smoke, "down": down,
     "up": lambda: up(a.weights or sys.exit("--weights required"))}[a.cmd]()

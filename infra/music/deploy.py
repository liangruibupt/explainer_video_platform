#!/usr/bin/env python3
"""Deploy the YuE2-3B music async endpoint that scales to 0.

  deploy.py plan                 show the GPU pool order (GPU + host memory check, price, cheapest first)
  deploy.py build                build the container and push it to ECR (evp/yue2)
  deploy.py up [--weights S3URI] grant the shared role, create models, endpoint config, endpoint, scaling
  deploy.py status               endpoint state, instance count, backlog
  deploy.py smoke [--case NAME]  instrumental bed and/or a short Chinese song, saved to data/music_smoke/
  deploy.py down                 delete endpoint, config, models, alarms (keeps S3 and ECR)

The execution role evp-image-endpoint is SHARED with the image module (we already hold PassRole
for it); `up` adds a separate inline policy "evp-music" for the evp/yue2 repo and its log group.
Credentials come from the machine's AWS profile or role. Nothing secret is stored here.
"""
import argparse, base64, datetime as dt, json, subprocess, sys, time, uuid
from pathlib import Path

import boto3

HERE = Path(__file__).resolve().parent
CFG = json.loads((HERE / "endpoint.json").read_text())
R = CFG["region"]; EP = CFG["endpoint"]
ACCT = boto3.client("sts").get_caller_identity()["Account"]
BUCKET = f"evp-models-{ACCT}-{R}"
REPO_NAME = "evp/yue2"
REPO = f"{ACCT}.dkr.ecr.{R}.amazonaws.com/{REPO_NAME}"
ROLE = "evp-image-endpoint"
TAGS = [{"Key": k, "Value": v} for k, v in CFG["tags"].items()]
sm = boto3.client("sagemaker", region_name=R)
aas = boto3.client("application-autoscaling", region_name=R)
cw = boto3.client("cloudwatch", region_name=R)
RES = f"endpoint/{EP}/variant/main"
SCALE_DIM = "sagemaker:variant:DesiredInstanceCount"

SMOKE = {
    "instrumental": {"style": "舒缓的中国古典器乐，古琴与箫，慢速，空灵，instrumental, no vocals",
                     "lyrics": "[Intro]\n\n[Instrumental]\n\n[Outro]", "cot": "full", "seed": 7},
    "song_zh": {"style": "华语流行，温暖的女声，木吉他，钢琴，轻柔鼓点，中速",
                "lyrics": "[Verse]\n清晨的光 落在窗台\n风把昨天 轻轻吹开\n\n"
                          "[Verse]\n脚步慢慢 走向远方\n心里有歌 不再迷茫\n\n[Outro]",
                "cot": "full", "seed": 11},
    # Variants tried after "instrumental" came back with sung vocals.
    "instrumental_en": {"style": "Chinese classical instrumental, guqin and xiao flute, slow, "
                                 "ethereal, ambient, no drums, instrumental, no vocals",
                        "lyrics": "[Intro]\n\n[Instrumental]\n\n[Outro]", "cot": "full", "seed": 7},
    "instrumental_off": {"style": "舒缓的中国古典器乐，古琴与箫，慢速，空灵，instrumental, no vocals",
                         "lyrics": "[Intro]\n\n[Instrumental]\n\n[Outro]", "cot": "off", "seed": 7},
}
# Cold-start check: the short song again with another seed, sent while the endpoint is at 0.
SMOKE["song_zh_cold"] = dict(SMOKE["song_zh"], seed=12)


def mem_gib(instance):
    t = boto3.client("ec2", region_name=R).describe_instance_types(
        InstanceTypes=[instance.removeprefix("ml.")])["InstanceTypes"][0]
    gpu = sum(g["MemoryInfo"]["SizeInMiB"] * g["Count"] for g in t["GpuInfo"]["Gpus"]) / 1024
    return gpu, t["MemoryInfo"]["SizeInMiB"] / 1024


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
    for inst in CFG["candidates"]:
        gpu, host = mem_gib(inst)
        ok = gpu >= CFG["gpu_gib_needed"] and host >= CFG["host_gib_needed"]
        rows.append(dict(instance=inst, gpu_gib=round(gpu), host_gib=round(host),
                         usd_h=price(inst), ok=ok))
    pool = sorted((r for r in rows if r["ok"]), key=lambda r: r["usd_h"])[:5]  # SageMaker allows 5 pools
    if show:
        for r in sorted(rows, key=lambda r: r["usd_h"]):
            pri = next((j + 1 for j, p in enumerate(pool) if p is r), "-")
            print(f"  priority {pri}  {r['instance']:16} GPU {r['gpu_gib']:>3} GiB  host {r['host_gib']:>3} GiB"
                  f"  ${r['usd_h']:.3f}/h  {'ok' if r['ok'] else 'NOT ENOUGH MEMORY'}")
    return pool


def build():
    # SageMaker rejects OCI image manifests: buildx with oci-mediatypes=false and no provenance
    # gives a plain Docker v2 manifest pushed straight to ECR.
    ecr = boto3.client("ecr", region_name=R)
    try:
        ecr.create_repository(repositoryName=REPO_NAME, tags=TAGS)
    except ecr.exceptions.RepositoryAlreadyExistsException:
        pass
    whl = HERE / "container" / "yue2_infer-0.1.5-py3-none-any.whl"
    if not whl.exists():
        from huggingface_hub import hf_hub_download
        hf_hub_download("m-a-p/YuE2-3B", whl.name, revision=CFG["weights"].rstrip("/").split("/")[-1],
                        local_dir=whl.parent)
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
    # Shared role: the image module owns policy "evp-image" (bucket read/write, its repo).
    # This adds only what the music endpoint needs on top, in its own inline policy.
    iam = boto3.client("iam")
    arn = iam.get_role(RoleName=ROLE)["Role"]["Arn"]
    policy = {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
         "Resource": f"arn:aws:ecr:{R}:{ACCT}:repository/{REPO_NAME}"},
        {"Effect": "Allow", "Action": ["logs:CreateLogGroup", "logs:CreateLogStream",
                                       "logs:PutLogEvents", "logs:DescribeLogStreams"],
         "Resource": f"arn:aws:logs:{R}:{ACCT}:log-group:/aws/sagemaker/Endpoints/{EP}*"}]}
    iam.put_role_policy(RoleName=ROLE, PolicyName="evp-music", PolicyDocument=json.dumps(policy))
    return arn


def up(weights):
    if not weights.endswith("/"):
        sys.exit("--weights must be an S3 prefix ending with /")
    tag = (HERE / ".image_tag").read_text().strip()
    role = ensure_role(); time.sleep(10)  # IAM propagation
    pool = plan()
    if not pool:
        sys.exit("no candidate instance has enough GPU and host memory")
    stamp = dt.datetime.utcnow().strftime("%m%d%H%M")
    model = f"{EP}-{stamp}"
    sm.create_model(ModelName=model, ExecutionRoleArn=role, Tags=TAGS, PrimaryContainer={
        "Image": f"{REPO}:{tag}",
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
            "OutputConfig": {"S3OutputPath": f"s3://{BUCKET}/jobs/music/out/",
                             "S3FailurePath": f"s3://{BUCKET}/jobs/music/fail/"},
            "ClientConfig": {"MaxConcurrentInvocationsPerInstance":
                             CFG["max_concurrent_per_instance"]}})
    try:
        sm.describe_endpoint(EndpointName=EP)
        sm.update_endpoint(EndpointName=EP, EndpointConfigName=cfg)
    except sm.exceptions.ClientError:
        sm.create_endpoint(EndpointName=EP, EndpointConfigName=cfg, Tags=TAGS)
    print("waiting for endpoint InService (first start includes the weight download)...", flush=True)
    sm.get_waiter("endpoint_in_service").wait(EndpointName=EP,
                                              WaiterConfig={"Delay": 30, "MaxAttempts": 120})
    scaling()
    print("endpoint", EP, "InService; scales to 0 after", CFG["scale_in_idle_s"], "s idle")


def scaling():
    aas.register_scalable_target(ServiceNamespace="sagemaker", ResourceId=RES,
        ScalableDimension=SCALE_DIM, MinCapacity=0, MaxCapacity=CFG["max_instances"])
    # Back to 0 when the queue is empty: target tracking on backlog per instance; the idle
    # time before scale-in is set by the cooldown.
    aas.put_scaling_policy(PolicyName=f"{EP}-backlog", ServiceNamespace="sagemaker",
        ResourceId=RES, ScalableDimension=SCALE_DIM,
        PolicyType="TargetTrackingScaling", TargetTrackingScalingPolicyConfiguration={
            "TargetValue": 1.0, "ScaleInCooldown": CFG["scale_in_idle_s"], "ScaleOutCooldown": 60,
            "CustomizedMetricSpecification": {
                "MetricName": "ApproximateBacklogSizePerInstance", "Namespace": "AWS/SageMaker",
                "Dimensions": [{"Name": "EndpointName", "Value": EP}], "Statistic": "Average"}})
    # From 0: target tracking cannot divide by 0 instances, so a step policy on
    # HasBacklogWithoutCapacity adds the first instance.
    arn = aas.put_scaling_policy(PolicyName=f"{EP}-from-zero", ServiceNamespace="sagemaker",
        ResourceId=RES, ScalableDimension=SCALE_DIM,
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
    v = (e.get("ProductionVariants") or [{}])[0]  # empty while Creating
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


def invoke(req, out_stem, timeout=3600):
    """Send one async request, wait for the result, write the audio, return the metadata."""
    s3 = boto3.client("s3", region_name=R)
    key = f"jobs/music/in/{uuid.uuid4().hex}.json"
    s3.put_object(Bucket=BUCKET, Key=key, Body=json.dumps(req, ensure_ascii=False).encode())
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
            path = Path(f"{out_stem}.{res['format']}")
            path.write_bytes(base64.b64decode(res.pop("audio_b64")))
            if res.get("abc"):
                Path(f"{out_stem}.abc").write_text(res["abc"])
                res["abc"] = f"{len(res['abc'])} chars -> {out_stem}.abc"
            res["file"] = str(path); res["wall_s"] = round(time.time() - t0)
            return res
        time.sleep(10)
    sys.exit("timed out")


def smoke(case):
    out = HERE.parents[1] / "data" / "music_smoke"; out.mkdir(parents=True, exist_ok=True)
    for name in ([case] if case else ["instrumental", "song_zh"]):
        res = invoke(SMOKE[name], str(out / name))
        print(name, json.dumps(res, ensure_ascii=False), flush=True)


def down():
    for name in (f"{EP}-backlog", f"{EP}-from-zero"):
        try:
            aas.delete_scaling_policy(PolicyName=name, ServiceNamespace="sagemaker", ResourceId=RES,
                                      ScalableDimension=SCALE_DIM)
        except aas.exceptions.ObjectNotFoundException:
            pass
    cw.delete_alarms(AlarmNames=[f"{EP}-has-backlog-without-capacity"])
    try:
        aas.deregister_scalable_target(ServiceNamespace="sagemaker", ResourceId=RES,
                                       ScalableDimension=SCALE_DIM)
    except aas.exceptions.ObjectNotFoundException:
        pass
    try:
        sm.describe_endpoint(EndpointName=EP)
        sm.delete_endpoint(EndpointName=EP)
        sm.get_waiter("endpoint_deleted").wait(EndpointName=EP)
    except sm.exceptions.ClientError:
        pass
    # NameContains "evp-music" only matches this module's resources.
    for c in sm.list_endpoint_configs(NameContains=EP)["EndpointConfigs"]:
        sm.delete_endpoint_config(EndpointConfigName=c["EndpointConfigName"])
    for m in sm.list_models(NameContains=EP)["Models"]:
        sm.delete_model(ModelName=m["ModelName"])
    print("deleted endpoint, configs, models, scaling and alarm; S3 weights and ECR image kept")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["plan", "build", "up", "status", "smoke", "down"])
    ap.add_argument("--weights", default=CFG["weights"],
                    help="S3 prefix holding model/ and vae/ (ends with /)")
    ap.add_argument("--case", choices=list(SMOKE), help="smoke: run only this case")
    a = ap.parse_args()
    {"plan": plan, "build": build, "status": status, "down": down,
     "smoke": lambda: smoke(a.case), "up": lambda: up(a.weights)}[a.cmd]()

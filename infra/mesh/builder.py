#!/usr/bin/env python3
"""Temporary x86 builder EC2 for the mesh image (this host is aarch64 with no GPU).

  builder.py launch [--cap-min N] [--type T]   upload infra/mesh/container, start the builder
  builder.py status RUN                        status.json + last log lines, live builders
  builder.py stop                              terminate every live mesh builder

The builder (CPU, c7i.4xlarge, 150 GB gp3, instance profile evp-video-builder) runs
builder.sh: docker build + push evp/<repo>:<tag>, then powers off. Shutdown behaviour is
terminate and the script sets a hard-cap poweroff, so it never outlives the cap. Tags:
project=explainer-video, module=mesh-builder. Same pattern as infra/avatar/builder.py.
"""
import argparse, io, json, sys, tarfile, time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

HERE = Path(__file__).resolve().parent
R = "us-east-1"
ACCT = boto3.client("sts").get_caller_identity()["Account"]
BUCKET = f"evp-models-{ACCT}-{R}"
REPO = f"{ACCT}.dkr.ecr.{R}.amazonaws.com/evp/mesh"
TAGS = {"project": "explainer-video", "module": "mesh-builder", "Name": "evp-mesh-builder"}
ec2 = boto3.client("ec2", region_name=R)
s3 = boto3.client("s3", region_name=R)


def src_tgz():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        t.add(HERE / "container", "container")
    return buf.getvalue()


def ensure_repo():
    ecr = boto3.client("ecr", region_name=R)
    try:
        ecr.describe_repositories(repositoryNames=["evp/mesh"])
    except ClientError as e:
        if e.response["Error"]["Code"] != "RepositoryNotFoundException":
            raise
        ecr.create_repository(repositoryName="evp/mesh", imageScanningConfiguration={"scanOnPush": True},
                              tags=[{"Key": "project", "Value": "explainer-video"},
                                    {"Key": "module", "Value": "mesh"}])
        print("created ECR repo evp/mesh")


def launch(cap_min, itype):
    ensure_repo()
    run = time.strftime("%Y%m%dT%H%M%S")
    tag = time.strftime("%Y%m%d-%H%M%S")
    s3.put_object(Bucket=BUCKET, Key=f"jobs/mesh-build/{run}/src.tgz", Body=src_tgz())
    ud = (HERE / "builder.sh").read_text()
    for k, v in (("__RUN__", run), ("__BUCKET__", BUCKET), ("__TAG__", tag),
                 ("__CAP_MIN__", str(cap_min)), ("__REPO__", REPO)):
        ud = ud.replace(k, v)
    ami = boto3.client("ssm", region_name=R).get_parameter(
        Name="/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64")["Parameter"]["Value"]
    vpc = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"][0]["VpcId"]
    sg = ec2.describe_security_groups(Filters=[{"Name": "group-name", "Values": ["evp-gpu-worker"]},
                                               {"Name": "vpc-id", "Values": [vpc]}])["SecurityGroups"][0]["GroupId"]
    tags = [{"Key": k, "Value": v} for k, v in TAGS.items()]
    if live():
        sys.exit(f"a mesh builder is already running: {live()}")
    for az in ("us-east-1a", "us-east-1b", "us-east-1d"):
        sub = ec2.describe_subnets(Filters=[{"Name": "default-for-az", "Values": ["true"]},
                                            {"Name": "availability-zone", "Values": [az]}])["Subnets"]
        try:
            i = ec2.run_instances(
                ImageId=ami, InstanceType=itype, MinCount=1, MaxCount=1, UserData=ud,
                IamInstanceProfile={"Name": "evp-video-builder"},
                InstanceInitiatedShutdownBehavior="terminate",
                MetadataOptions={"HttpTokens": "required", "HttpEndpoint": "enabled"},
                NetworkInterfaces=[{"DeviceIndex": 0, "SubnetId": sub[0]["SubnetId"], "Groups": [sg],
                                    "AssociatePublicIpAddress": True, "DeleteOnTermination": True}],
                BlockDeviceMappings=[
                    {"DeviceName": "/dev/xvda", "Ebs": {"VolumeSize": 30, "VolumeType": "gp3",
                                                        "DeleteOnTermination": True, "Encrypted": True}},
                    {"DeviceName": "/dev/sdf", "Ebs": {"VolumeSize": 150, "VolumeType": "gp3", "Iops": 6000,
                                                       "Throughput": 500, "DeleteOnTermination": True,
                                                       "Encrypted": True}}],
                TagSpecifications=[{"ResourceType": t, "Tags": tags} for t in ("instance", "volume")])["Instances"][0]
        except ClientError as e:
            print(az, e.response["Error"]["Code"]); continue
        print(json.dumps({"run": run, "tag": tag, "image": f"{REPO}:{tag}", "instance": i["InstanceId"],
                          "az": az, "type": itype}))
        return
    sys.exit("no capacity")


def live(states=("pending", "running", "shutting-down")):
    res = ec2.describe_instances(Filters=[{"Name": "tag:module", "Values": ["mesh-builder"]},
                                          {"Name": "instance-state-name", "Values": list(states)}])
    return [(i["InstanceId"], i["State"]["Name"]) for r in res["Reservations"] for i in r["Instances"]]


def status(run):
    for k in ("status.json", "build.log"):
        try:
            b = s3.get_object(Bucket=BUCKET, Key=f"jobs/mesh-build/{run}/{k}")["Body"].read().decode()
            print(f"== {k}\n" + ("\n".join(b.splitlines()[-40:]) if k.endswith(".log") else b))
        except ClientError:
            print(f"== {k}: none yet")
    print("live builders:", live())


def stop():
    ids = [i for i, st in live(("pending", "running")) ]
    if ids:
        ec2.terminate_instances(InstanceIds=ids)
    print("terminated", ids)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["launch", "status", "stop"])
    ap.add_argument("run", nargs="?")
    ap.add_argument("--cap-min", type=int, default=90)
    ap.add_argument("--type", default="c7i.4xlarge")
    a = ap.parse_args()
    if a.cmd == "launch":
        launch(a.cap_min, a.type)
    elif a.cmd == "status":
        status(a.run)
    else:
        stop()

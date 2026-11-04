#!/usr/bin/env python3
"""Temporary builder EC2 for the avatar module (this host has ~17 GB free disk).

  builder.py launch [--only weights|image]   upload infra/avatar to S3, start the builder
  builder.py status RUN                      status.json + last log lines
  builder.py stop RUN                        terminate it

The builder (CPU, c7i.4xlarge, 400 GB gp3, instance profile evp-video-builder) runs
builder.sh: stages both models' weights HF -> S3 (stage_weights.py) and builds + pushes
evp/avatar:<tag>, then powers off. Shutdown behaviour is terminate and the script sets a
hard-cap poweroff, so it never outlives the cap even if the build hangs.
"""
import argparse, io, json, sys, tarfile, time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

HERE = Path(__file__).resolve().parent
R = "us-east-1"
ACCT = boto3.client("sts").get_caller_identity()["Account"]
BUCKET = f"evp-models-{ACCT}-{R}"
REPO = f"{ACCT}.dkr.ecr.{R}.amazonaws.com/evp/avatar"
TAGS = {"project": "explainer-video", "module": "avatar-builder", "Name": "evp-avatar-builder"}
ec2 = boto3.client("ec2", region_name=R)
s3 = boto3.client("s3", region_name=R)


def src_tgz():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        t.add(HERE / "stage_weights.py", "stage_weights.py")
        t.add(HERE / "container", "container")
    return buf.getvalue()


def launch(cap_min, itype, only="all"):
    run = time.strftime("%Y%m%dT%H%M%S")
    tag = time.strftime("%Y%m%d-%H%M%S")
    s3.put_object(Bucket=BUCKET, Key=f"jobs/avatar-build/{run}/src.tgz", Body=src_tgz())
    ud = (HERE / "builder.sh").read_text()
    for k, v in (("__RUN__", run), ("__BUCKET__", BUCKET), ("__TAG__", tag), ("__CAP_MIN__", str(cap_min)),
                 ("__REPO__", REPO), ("__ONLY__", only)):
        ud = ud.replace(k, v)
    ami = boto3.client("ssm", region_name=R).get_parameter(
        Name="/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64")["Parameter"]["Value"]
    vpc = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"][0]["VpcId"]
    sg = ec2.describe_security_groups(Filters=[{"Name": "group-name", "Values": ["evp-gpu-worker"]},
                                               {"Name": "vpc-id", "Values": [vpc]}])["SecurityGroups"][0]["GroupId"]
    tags = [{"Key": k, "Value": v} for k, v in TAGS.items()]
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
                    {"DeviceName": "/dev/sdf", "Ebs": {"VolumeSize": 400, "VolumeType": "gp3", "Iops": 8000,
                                                       "Throughput": 1000, "DeleteOnTermination": True,
                                                       "Encrypted": True}}],
                TagSpecifications=[{"ResourceType": t, "Tags": tags} for t in ("instance", "volume")])["Instances"][0]
        except ClientError as e:
            print(az, e.response["Error"]["Code"]); continue
        print(json.dumps({"run": run, "tag": tag, "instance": i["InstanceId"], "az": az, "type": itype}))
        return
    sys.exit("no capacity")


def status(run):
    for k in ("status.json", "build.log"):
        try:
            b = s3.get_object(Bucket=BUCKET, Key=f"jobs/avatar-build/{run}/{k}")["Body"].read().decode()
            print(f"== {k}\n" + ("\n".join(b.splitlines()[-25:]) if k.endswith(".log") else b))
        except ClientError:
            print(f"== {k}: none yet")
    res = ec2.describe_instances(Filters=[{"Name": "tag:module", "Values": ["avatar-builder"]},
                                          {"Name": "instance-state-name", "Values": ["pending", "running", "shutting-down"]}])
    print("live builders:", [(i["InstanceId"], i["State"]["Name"]) for r in res["Reservations"] for i in r["Instances"]])


def stop():
    res = ec2.describe_instances(Filters=[{"Name": "tag:module", "Values": ["avatar-builder"]},
                                          {"Name": "instance-state-name", "Values": ["pending", "running"]}])
    ids = [i["InstanceId"] for r in res["Reservations"] for i in r["Instances"]]
    if ids:
        ec2.terminate_instances(InstanceIds=ids)
    print("terminated", ids)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["launch", "status", "stop"])
    ap.add_argument("run", nargs="?")
    ap.add_argument("--cap-min", type=int, default=150)
    ap.add_argument("--type", default="c7i.4xlarge")
    ap.add_argument("--only", default="all", choices=["all", "image"])
    a = ap.parse_args()
    if a.cmd == "launch":
        launch(a.cap_min, a.type, a.only)
    elif a.cmd == "status":
        status(a.run)
    else:
        stop()

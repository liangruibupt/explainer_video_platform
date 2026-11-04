"""Self-managed EC2 GPU backend: start a worker instance on demand, let it stop itself.

Rule (docs/DESIGN.md section 4): new or scarce GPUs (g6e, g7, g7e) run on EC2 instances
the platform starts and stops itself; SageMaker async endpoints are for plentiful older
GPUs (g5, g4dn). Each module has a JSON config, e.g. infra/gpu_ec2/modules/video.json.

    gpu = GpuEC2("video")
    jid = gpu.submit({...})      # writes jobs/<module>/in/<id>.json, then ensure_running()
    gpu.poll(jid)                # None while pending, else {"ok": bool, ...}
    gpu.status()                 # none|launching|booting|loading|ready|busy
    gpu.stop()                   # terminate the module's instance(s)

The instance runs infra/gpu_ec2/worker/{bootstrap.sh,worker.py} from user-data. It
terminates itself after idle_shutdown_s with no job and at hard_cap_s from boot, so an
idle module costs nothing. At most one instance per module (checked by tags).
"""
import base64, gzip, io, json, tarfile, time, uuid
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[2]
MODULES = ROOT / "infra" / "gpu_ec2" / "modules"
WORKER = ROOT / "infra" / "gpu_ec2" / "worker"
CAPACITY_ERRORS = {"InsufficientInstanceCapacity", "InstanceLimitExceeded", "Unsupported",
                   "MaxSpotInstanceCountExceeded", "SpotMaxPriceTooLow",
                   "InsufficientCapacityOnHost", "VcpuLimitExceeded"}
LIVE = ["pending", "running"]


class NoCapacity(RuntimeError):
    def __init__(self, attempts):
        super().__init__("no capacity: " + "; ".join(f"{a['option']}: {a['error']}" for a in attempts))
        self.attempts = attempts


class GpuEC2:
    def __init__(self, module, config=None):
        self.cfg = config or json.loads((MODULES / f"{module}.json").read_text())
        self.module = self.cfg["module"]
        self.region = self.cfg["region"]
        self.bucket, self.prefix = self.cfg["bucket"], self.cfg["jobs_prefix"]
        self.ec2 = boto3.client("ec2", region_name=self.region)
        self.s3 = boto3.client("s3", region_name=self.region)
        self.last_launch = None  # dict describing the option that launched, attempts, timing

    # ---------- queue ----------
    def submit(self, req, ensure=True):
        jid = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
        body = dict(req, _submitted_ts=time.time())
        self.s3.put_object(Bucket=self.bucket, Key=f"{self.prefix}in/{jid}.json",
                           Body=json.dumps(body, ensure_ascii=False).encode(),
                           ContentType="application/json")
        if ensure:
            self.ensure_running()
        return jid

    def _get(self, key):
        try:
            return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except ClientError as e:
            if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
                return None
            raise

    def poll(self, jid):
        """None while queued/running; {"ok": True, ...result} or {"ok": False, "error": ...}."""
        out = self._get(f"{self.prefix}out/{jid}.json")
        fail = None if out is not None else self._get(f"{self.prefix}fail/{jid}.json")
        if out is not None or fail is not None:
            for k in (f"{self.prefix}in/{jid}.json", f"{self.prefix}claimed/{jid}.json"):
                try:
                    self.s3.delete_object(Bucket=self.bucket, Key=k)
                except ClientError:
                    pass
        if out is not None:
            return dict(json.loads(out), ok=True)
        if fail is not None:
            return dict(json.loads(fail), ok=False)
        return None

    def fetch(self, res, key="video_b64"):
        """List of bytes for one output key of a result (see worker.store_outputs)."""
        keys = (res.get("files") or {}).get(key)
        if not keys and key == "video_b64" and res.get("video_key"):
            keys = [res["video_key"]]  # results written by the first video worker
        return [self._get(k) for k in keys or []]

    def fetch_video(self, res, path):
        Path(path).write_bytes(self.fetch(res, "video_b64")[0])
        return path

    def heartbeat(self):
        b = self._get(self.prefix + "heartbeat.json")
        return json.loads(b) if b else None

    # ---------- instances ----------
    def instances(self, states=LIVE):
        filters = [{"Name": f"tag:{k}", "Values": [v]} for k, v in self.cfg["tags"].items()]
        filters.append({"Name": "instance-state-name", "Values": states})
        res = self.ec2.describe_instances(Filters=filters)["Reservations"]
        return [i for r in res for i in r["Instances"]]

    def status(self):
        """none | launching | booting | loading | ready | busy (+ details)."""
        inst = self.instances()
        if not inst:
            return {"state": "none"}
        i = inst[0]
        d = {"instance_id": i["InstanceId"], "instance_type": i["InstanceType"],
             "az": i["Placement"]["AvailabilityZone"],
             "lifecycle": i.get("InstanceLifecycle", "on-demand"),
             "ec2_state": i["State"]["Name"], "launch_time": i["LaunchTime"].isoformat()}
        if i["State"]["Name"] == "pending":
            return dict(d, state="launching")
        hb = self.heartbeat()
        # the worker writes "busy" once per job and nothing until it finishes, so a busy
        # heartbeat stays valid for the whole invoke timeout (avatar renders run 20-50 min)
        max_age = self.cfg["heartbeat_stale_s"]
        if hb and hb.get("state") == "busy":
            max_age = max(max_age, self.cfg.get("invoke_timeout_s", 0))
        fresh = (hb and hb.get("instance_id") == i["InstanceId"]
                 and time.time() - hb["ts"] < max_age)
        if not fresh:
            return dict(d, state="booting", heartbeat="stale" if hb else "none")
        st = hb["state"] if hb["state"] in ("booting", "loading", "ready", "busy") else "booting"
        return dict(d, state=st, heartbeat=hb)

    def ensure_running(self):
        """Return the live instance id, launching one (capacity order) if there is none."""
        inst = self.instances()
        if inst:
            return inst[0]["InstanceId"]
        return self.launch()

    def stop(self):
        ids = [i["InstanceId"] for i in self.instances(LIVE + ["stopping", "stopped"])]
        if ids:
            self.ec2.terminate_instances(InstanceIds=ids)
        return ids

    # ---------- launch ----------
    def ami(self):
        name = boto3.client("ssm", region_name=self.region).get_parameter(
            Name=self.cfg["ami_ssm"])["Parameter"]["Value"]
        return name

    def security_group(self):
        name = self.cfg["security_group"]
        vpc = self.ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"][0]["VpcId"]
        g = self.ec2.describe_security_groups(Filters=[
            {"Name": "group-name", "Values": [name]}, {"Name": "vpc-id", "Values": [vpc]}])["SecurityGroups"]
        if g:
            return g[0]["GroupId"]
        # No inbound rules at all (the default egress-all rule stays); access is SSM only.
        gid = self.ec2.create_security_group(
            GroupName=name, VpcId=vpc, Description="EVP GPU worker: no inbound, egress only",
            TagSpecifications=[{"ResourceType": "security-group", "Tags": self._tags(name)}])["GroupId"]
        return gid

    def subnet(self, az):
        s = self.ec2.describe_subnets(Filters=[{"Name": "default-for-az", "Values": ["true"]},
                                               {"Name": "availability-zone", "Values": [az]}])["Subnets"]
        return s[0]["SubnetId"] if s else None

    def _tags(self, name=None):
        t = dict(self.cfg["tags"], Name=name or self.cfg["name"])
        return [{"Key": k, "Value": v} for k, v in t.items()]

    def user_data(self):
        """A shell script carrying the worker files as an embedded tar.gz (user-data <16 KB)."""
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for name, data in (("config.json", json.dumps(self.cfg).encode()),
                               ("bootstrap.sh", (WORKER / "bootstrap.sh").read_bytes()),
                               ("worker.py", (WORKER / "worker.py").read_bytes())):
                ti = tarfile.TarInfo(name); ti.size = len(data); ti.mode = 0o755
                tar.addfile(ti, io.BytesIO(data))
        payload = base64.b64encode(buf.getvalue()).decode()
        script = f"""#!/bin/bash
mkdir -p /opt/evp
echo '{payload}' | base64 -d | tar -xz -C /opt/evp
cat > /etc/systemd/system/evp-worker.service <<'UNIT'
[Unit]
Description=EVP GPU worker
After=network-online.target docker.service
Wants=network-online.target docker.service
[Service]
Type=simple
ExecStart=/opt/evp/bootstrap.sh
Restart=no
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now evp-worker.service
"""
        if len(script) > 16000:
            raise RuntimeError(f"user-data too large: {len(script)} bytes")
        return script

    def launch(self):
        ami, sg, ud = self.ami(), self.security_group(), self.user_data()
        attempts, t0 = [], time.time()
        for opt in self.cfg["capacity"]:
            label = f"{opt['market']} {opt['type']} {opt['az']}"
            subnet = self.subnet(opt["az"])
            if not subnet:
                attempts.append({"option": label, "error": "no default subnet"}); continue
            args = dict(
                ImageId=ami, InstanceType=opt["type"], MinCount=1, MaxCount=1, UserData=ud,
                IamInstanceProfile={"Name": self.cfg["instance_profile"]},
                InstanceInitiatedShutdownBehavior="terminate",
                MetadataOptions={"HttpTokens": "required", "HttpEndpoint": "enabled",
                                 "HttpPutResponseHopLimit": 2},
                NetworkInterfaces=[{"DeviceIndex": 0, "SubnetId": subnet, "Groups": [sg],
                                    "AssociatePublicIpAddress": True, "DeleteOnTermination": True}],
                BlockDeviceMappings=[{"DeviceName": "/dev/xvda", "Ebs": {
                    "VolumeSize": self.cfg["root_gb"], "VolumeType": "gp3",
                    "DeleteOnTermination": True, "Encrypted": True}}],
                TagSpecifications=[{"ResourceType": t, "Tags": self._tags()}
                                   for t in ("instance", "volume", "network-interface")])
            if opt["market"] == "spot":
                args["InstanceMarketOptions"] = {"MarketType": "spot", "SpotOptions": {
                    "SpotInstanceType": "one-time", "InstanceInterruptionBehavior": "terminate"}}
                args["TagSpecifications"].append({"ResourceType": "spot-instances-request",
                                                  "Tags": self._tags()})
            # Re-check right before each call: never more than one instance per module.
            live = self.instances()
            if live:
                return live[0]["InstanceId"]
            try:
                i = self.ec2.run_instances(**args)["Instances"][0]
            except ClientError as e:
                code = e.response["Error"]["Code"]
                attempts.append({"option": label, "error": code})
                if code in CAPACITY_ERRORS:
                    continue
                raise
            self.last_launch = {"option": label, "instance_id": i["InstanceId"], "ami": ami,
                                "attempts": attempts, "launch_api_s": round(time.time() - t0, 1),
                                "launched_ts": time.time()}
            return i["InstanceId"]
        raise NoCapacity(attempts)

    # ---------- pricing ----------
    def prices(self):
        """{(type, market, az): usd_per_hour}; on-demand from the Pricing API, spot latest."""
        pr = boto3.client("pricing", region_name="us-east-1")
        out = {}
        for t in sorted({o["type"] for o in self.cfg["capacity"]}):
            p = pr.get_products(ServiceCode="AmazonEC2", MaxResults=10, Filters=[
                {"Type": "TERM_MATCH", "Field": f, "Value": v} for f, v in (
                    ("instanceType", t), ("regionCode", self.region), ("tenancy", "Shared"),
                    ("operatingSystem", "Linux"), ("preInstalledSw", "NA"),
                    ("capacitystatus", "Used"))])["PriceList"]
            od = float("nan")
            for s in map(json.loads, p):
                for term in s.get("terms", {}).get("OnDemand", {}).values():
                    od = float(next(iter(term["priceDimensions"].values()))["pricePerUnit"]["USD"])
            for o in self.cfg["capacity"]:
                if o["type"] == t and o["market"] == "on-demand":
                    out[(t, "on-demand", o["az"])] = od
            sp = self.ec2.describe_spot_price_history(
                InstanceTypes=[t], ProductDescriptions=["Linux/UNIX"],
                StartTime=time.time() - 6 * 3600)["SpotPriceHistory"]
            for o in self.cfg["capacity"]:
                if o["type"] == t and o["market"] == "spot":
                    rows = [float(x["SpotPrice"]) for x in sp if x["AvailabilityZone"] == o["az"]]
                    out[(t, "spot", o["az"])] = rows[0] if rows else float("nan")
        return out

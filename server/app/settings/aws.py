"""Read-mostly AWS lookups for the settings page.

Every call goes through `client()` and `gpu()`, so tests replace those two and make no
AWS calls. The only write actions are GpuEC2.ensure_running() and GpuEC2.stop().
"""
import datetime as dt
import json
import math
import re
import time

import boto3

from ..gpu_ec2 import MODULES, GpuEC2
from . import store

SAGEMAKER_REGIONS = ("us-east-1", "ap-south-1")
CONTROL_REGION = "us-east-1"
PROJECT_TAG = "explainer-video"
INSTANCE_RE = re.compile(r"i-[0-9a-f]{8,17}")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_gpus: dict = {}


def client(service: str, region: str = CONTROL_REGION):
    return boto3.client(service, region_name=region)


def modules() -> list[str]:
    """Module names that have a config file (the only names the API accepts)."""
    return sorted(p.stem for p in MODULES.glob("*.json"))


def config(module: str) -> dict:
    return json.loads((MODULES / f"{module}.json").read_text())


def gpu(module: str) -> GpuEC2:
    if module not in _gpus:
        _gpus[module] = GpuEC2(module)
    return _gpus[module]


def _num(x):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(x, 4)


def parse_s3(uri: str) -> tuple[str, str]:
    m = re.fullmatch(r"s3://([^/]+)/(.*)", uri or "")
    return (m.group(1), m.group(2)) if m else ("", "")


# ---------------------------------------------------------------- deployment cards

def prices(module: str, refresh: bool = False):
    """[{type, market, az, usd_per_hour}] in capacity order; cached 1 h."""
    def load():
        p = gpu(module).prices()
        return [{"type": t, "market": mk, "az": az, "usd_per_hour": _num(v)}
                for (t, mk, az), v in p.items()]
    rows, ts = store.cached("prices", module, 3600, load, refresh)
    by = {(r["type"], r["market"], r["az"]): r["usd_per_hour"] for r in rows}
    order = [dict(o, usd_per_hour=by.get((o["type"], o["market"], o["az"])))
             for o in config(module)["capacity"]]
    return order, ts


def card(module: str) -> dict:
    cfg = config(module)
    bucket, wprefix = parse_s3(cfg.get("weights", ""))
    out = {
        "module": module, "region": cfg["region"], "name": cfg.get("name"),
        "image": cfg.get("image"), "image_tag": (cfg.get("image") or "").rpartition(":")[2],
        "weights": cfg.get("weights"),
        "weights_model": wprefix.strip("/").split("/")[1] if wprefix.count("/") >= 2 else "",
        "weights_sha": wprefix.strip("/").rpartition("/")[2],
        "idle_shutdown_min": round(cfg.get("idle_shutdown_s", 0) / 60),
        "hard_cap_min": round(cfg.get("hard_cap_s", 0) / 60),
        "heartbeat_stale_s": cfg.get("heartbeat_stale_s"),
        "capacity": cfg["capacity"],
    }
    g = gpu(module)
    try:
        st = g.status()
        out["status"] = {k: v for k, v in st.items() if k != "heartbeat"}
        if st.get("launch_time"):
            lt = dt.datetime.fromisoformat(st["launch_time"])
            out["status"]["uptime_s"] = int(time.time() - lt.timestamp())
    except Exception as e:  # noqa: BLE001 - shown on the card
        out["status"] = {"state": "unavailable", "error": str(e)[:300]}
    try:
        hb = g.heartbeat()
        if hb:
            out["heartbeat"] = {"state": hb.get("state"), "instance_id": hb.get("instance_id"),
                                "ts": hb.get("ts"),
                                "age_s": int(time.time() - hb["ts"]) if hb.get("ts") else None,
                                "note": hb.get("note")}
    except Exception as e:  # noqa: BLE001
        out["heartbeat_error"] = str(e)[:300]
    try:
        out["capacity"], out["prices_ts"] = prices(module)
    except Exception as e:  # noqa: BLE001 - e.g. no pricing permission
        out["prices_error"] = str(e)[:300]
    return out


def worker_log(module: str, instance_id: str = "", lines: int = 50) -> dict:
    """Last `lines` of logs/<instance>.log in the module's jobs prefix (S3)."""
    g = gpu(module)
    if not instance_id:
        st = g.status()
        instance_id = st.get("instance_id") or ""
        if not instance_id:
            hb = g.heartbeat() or {}
            instance_id = hb.get("instance_id") or ""
    if not instance_id:
        return {"instance_id": "", "lines": [], "note": "没有正在运行或最近的实例"}
    if not INSTANCE_RE.fullmatch(instance_id):
        raise ValueError("bad instance id")
    cfg = config(module)
    key = f"{cfg['jobs_prefix']}logs/{instance_id}.log"
    s3 = client("s3", cfg["region"])
    try:
        obj = s3.get_object(Bucket=cfg["bucket"], Key=key)
    except Exception as e:  # noqa: BLE001
        code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404"):
            return {"instance_id": instance_id, "lines": [], "note": "日志还没有上传"}
        raise
    txt = ANSI_RE.sub("", obj["Body"].read()[-200_000:].decode("utf-8", "replace"))
    lm = obj.get("LastModified")
    return {"instance_id": instance_id, "key": key, "lines": txt.splitlines()[-lines:],
            "updated": lm.isoformat() if hasattr(lm, "isoformat") else None}


def sagemaker() -> dict:
    """SageMaker fallback: 'retired' unless evp-* endpoints exist."""
    found, errors = [], []
    for region in SAGEMAKER_REGIONS:
        try:
            sm = client("sagemaker", region)
            res = sm.list_endpoints(NameContains="evp-", MaxResults=100)
            for e in res.get("Endpoints", []):
                if e["EndpointName"].startswith("evp-"):
                    found.append({"region": region, "name": e["EndpointName"],
                                  "status": e.get("EndpointStatus")})
        except Exception as e:  # noqa: BLE001
            errors.append(f"{region}: {str(e)[:200]}")
    return {"state": "active" if found else ("unknown" if errors and not found else "retired"),
            "endpoints": found, "errors": errors, "regions": list(SAGEMAKER_REGIONS)}


# ---------------------------------------------------------------- cost

def _cost():
    today = dt.date.today()
    start = today.replace(day=1)
    end = today + dt.timedelta(days=1)
    ce = client("ce", CONTROL_REGION)
    out = {"start": start.isoformat(), "end": end.isoformat(), "currency": "USD"}
    res = ce.get_cost_and_usage(
        TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
        Granularity="MONTHLY", Metrics=["UnblendedCost"],
        Filter={"Tags": {"Key": "project", "Values": [PROJECT_TAG]}},
        GroupBy=[{"Type": "TAG", "Key": "module"}])
    by = {}
    for period in res.get("ResultsByTime", []):
        for g in period.get("Groups", []):
            name = g["Keys"][0].split("$", 1)[-1] or "(未标记)"
            amt = float(g["Metrics"]["UnblendedCost"]["Amount"])
            by[name] = by.get(name, 0.0) + amt
    out["by_module"] = [{"module": k, "usd": round(v, 2)} for k, v in sorted(by.items())]
    out["total_usd"] = round(sum(by.values()), 2)
    return out


def _budgets():
    account = client("sts", CONTROL_REGION).get_caller_identity()["Account"]
    b = client("budgets", CONTROL_REGION)
    rows = []
    for bu in b.describe_budgets(AccountId=account).get("Budgets", []):
        name = bu["BudgetName"]
        # Thresholds only; never read subscribers (they hold the alert email).
        notes = b.describe_notifications_for_budget(AccountId=account, BudgetName=name)
        th = [{"type": n.get("NotificationType"), "operator": n.get("ComparisonOperator"),
               "threshold": n.get("Threshold"), "threshold_type": n.get("ThresholdType"),
               "state": n.get("NotificationState")} for n in notes.get("Notifications", [])]
        th.sort(key=lambda n: n["threshold"] or 0)
        spend = (bu.get("CalculatedSpend") or {}).get("ActualSpend") or {}
        rows.append({"name": name, "limit": (bu.get("BudgetLimit") or {}).get("Amount"),
                     "unit": (bu.get("BudgetLimit") or {}).get("Unit"),
                     "time_unit": bu.get("TimeUnit"), "actual": spend.get("Amount"),
                     "thresholds": th})
    return rows


def cost(refresh: bool = False) -> dict:
    """Month-to-date cost by module tag + budget alert thresholds; each part cached 6 h
    and failing on its own (e.g. the role has no ce:* or budgets:* permission)."""
    out = {}
    for key, fn in (("cost", _cost), ("budgets", _budgets)):
        try:
            out[key], out[f"{key}_ts"] = store.cached("cost", key, 6 * 3600, fn, refresh)
        except Exception as e:  # noqa: BLE001
            out[f"{key}_error"] = str(e)[:400]
    return out


# ---------------------------------------------------------------- S3 model cache

def _weights():
    """Per bucket: every weights/<model>/<version>/ prefix with its size and file count."""
    in_use = {}
    buckets = {}
    for m in modules():
        cfg = config(m)
        b, p = parse_s3(cfg.get("weights", ""))
        if b:
            in_use[(b, p.rstrip("/") + "/")] = m
            buckets.setdefault(b, cfg["region"])
    rows = []
    for b, region in sorted(buckets.items()):
        s3 = client("s3", region)
        groups = {}
        token = None
        while True:
            kw = {"Bucket": b, "Prefix": "weights/"}
            if token:
                kw["ContinuationToken"] = token
            res = s3.list_objects_v2(**kw)
            for o in res.get("Contents", []):
                parts = o["Key"].split("/")
                if len(parts) < 4:
                    continue
                pre = "/".join(parts[:3]) + "/"
                g = groups.setdefault(pre, {"size": 0, "files": 0, "modified": ""})
                g["size"] += o["Size"]
                g["files"] += 1
                lm = o["LastModified"]
                lm = lm.isoformat() if hasattr(lm, "isoformat") else str(lm)
                g["modified"] = max(g["modified"], lm)
            if not res.get("IsTruncated"):
                break
            token = res.get("NextContinuationToken")
        for pre, g in sorted(groups.items()):
            rows.append(dict(g, bucket=b, region=region, prefix=pre,
                             model=pre.split("/")[1], version=pre.split("/")[2],
                             module=in_use.get((b, pre))))
    return rows


def model_cache(refresh: bool = False) -> dict:
    try:
        rows, ts = store.cached("model_cache", "weights", 3600, _weights, refresh)
        return {"prefixes": rows, "ts": ts}
    except Exception as e:  # noqa: BLE001
        return {"prefixes": [], "error": str(e)[:400]}


# ---------------------------------------------------------------- account

def account() -> dict:
    regions = sorted({CONTROL_REGION, *(config(m)["region"] for m in modules())})
    out = {"regions": regions, "control_region": CONTROL_REGION,
           "gpu_regions": {m: config(m)["region"] for m in modules()}}
    try:
        ident = client("sts", CONTROL_REGION).get_caller_identity()
        out.update(account_id=ident["Account"], arn=ident["Arn"], user_id=ident["UserId"])
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:300]
    return out

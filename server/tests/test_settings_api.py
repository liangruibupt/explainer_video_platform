"""Settings API tests: GpuEC2 and boto3 are stubbed, no AWS calls."""
import datetime as dt
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gpu_ec2 import NoCapacity
from app.settings import api, aws, store


class FakeGpu:
    def __init__(self, module, state="none"):
        self.module = module
        self.state = state
        self.calls = []
        self.last_launch = None

    def status(self):
        self.calls.append("status")
        if self.state == "none":
            return {"state": "none"}
        return {"state": self.state, "instance_id": "i-0123456789abcdef0",
                "instance_type": "g6.2xlarge", "az": "us-east-1a", "lifecycle": "spot",
                "ec2_state": "running",
                "launch_time": (dt.datetime.now(dt.timezone.utc)
                                - dt.timedelta(minutes=5)).isoformat()}

    def heartbeat(self):
        return {"state": "ready", "instance_id": "i-0123456789abcdef0", "ts": time.time() - 30}

    def prices(self):
        self.calls.append("prices")
        cfg = aws.config(self.module)
        return {(o["type"], o["market"], o["az"]): (1.5 if o["market"] == "on-demand"
                                                    else float("nan"))
                for o in cfg["capacity"]}

    def ensure_running(self):
        self.calls.append("ensure_running")
        if self.state == "nocap":
            raise NoCapacity([{"option": "spot g6 a", "error": "InsufficientInstanceCapacity"}])
        return "i-0123456789abcdef0"

    def stop(self):
        self.calls.append("stop")
        return ["i-0123456789abcdef0"]


class FakeBody:
    def __init__(self, b):
        self.b = b

    def read(self):
        return self.b


class FakeErr(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeClient:
    def __init__(self, service, region, fail=()):
        self.service, self.region, self.fail = service, region, fail

    def _check(self):
        if self.service in self.fail:
            raise FakeErr("AccessDeniedException")

    # s3
    def get_object(self, Bucket, Key):
        if "missing" in Key:
            raise FakeErr("NoSuchKey")
        txt = "\n".join(f"line {i}" for i in range(119)) + "\n\x1b[32mline 119\x1b[0m"
        return {"Body": FakeBody(txt.encode()), "LastModified": dt.datetime(2026, 11, 4)}

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        lm = dt.datetime(2026, 11, 3)
        if ContinuationToken is None:
            return {"IsTruncated": True, "NextContinuationToken": "t", "Contents": [
                {"Key": "weights/m1/v1/a.bin", "Size": 10, "LastModified": lm},
                {"Key": "weights/m1/v1/sub/b.bin", "Size": 5, "LastModified": lm},
                {"Key": "weights/README", "Size": 1, "LastModified": lm}]}
        return {"IsTruncated": False, "Contents": [
            {"Key": "weights/m2/v9/c.bin", "Size": 7, "LastModified": lm}]}

    # sagemaker
    def list_endpoints(self, **kw):
        self._check()
        if self.region == "ap-south-1":
            return {"Endpoints": [{"EndpointName": "evp-video", "EndpointStatus": "InService"},
                                  {"EndpointName": "other-evp-x", "EndpointStatus": "InService"}]}
        return {"Endpoints": []}

    # ce / budgets / sts
    def get_cost_and_usage(self, **kw):
        self._check()
        assert kw["Filter"] == {"Tags": {"Key": "project", "Values": ["explainer-video"]}}
        return {"ResultsByTime": [{"Groups": [
            {"Keys": ["module$video"], "Metrics": {"UnblendedCost": {"Amount": "12.345"}}},
            {"Keys": ["module$"], "Metrics": {"UnblendedCost": {"Amount": "1"}}}]}]}

    def get_caller_identity(self):
        return {"Account": "111122223333", "Arn": "arn:aws:sts::111122223333:assumed-role/r/i",
                "UserId": "AROA:i"}

    def describe_budgets(self, AccountId):
        self._check()
        return {"Budgets": [{"BudgetName": "b", "BudgetLimit": {"Amount": "1000", "Unit": "USD"},
                             "TimeUnit": "MONTHLY",
                             "CalculatedSpend": {"ActualSpend": {"Amount": "44"}}}]}

    def describe_notifications_for_budget(self, AccountId, BudgetName):
        return {"Notifications": [{"NotificationType": "ACTUAL", "Threshold": 500.0},
                                  {"NotificationType": "ACTUAL", "Threshold": 200.0}]}


@pytest.fixture
def env(monkeypatch, tmp_path):
    from app import pipeline
    monkeypatch.setattr(pipeline, "DATA", tmp_path)
    gpus = {}
    fail = set()
    monkeypatch.setattr(aws, "gpu", lambda m: gpus.setdefault(m, FakeGpu(m)))
    monkeypatch.setattr(aws, "client", lambda s, r="us-east-1": FakeClient(s, r, fail))

    def boom(*a, **k):
        raise AssertionError("real boto3 client created in a test")
    monkeypatch.setattr(aws.boto3, "client", boom)
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app), gpus, fail, tmp_path


def test_deploy_cards(env):
    c, gpus, _, tmp = env
    r = c.get("/api/settings/deploy").json()
    assert [m["module"] for m in r["modules"]] == ["avatar", "image", "music", "video"]
    music = next(m for m in r["modules"] if m["module"] == "music")
    assert music["status"]["state"] == "none"
    assert music["weights_model"] == "yue2-3b" and len(music["weights_sha"]) == 40
    assert music["idle_shutdown_min"] == 15 and music["hard_cap_min"] == 240
    assert music["capacity"][0]["usd_per_hour"] == 1.5
    assert any(o["usd_per_hour"] is None for o in music["capacity"])  # NaN spot -> null
    assert 0 <= music["heartbeat"]["age_s"] < 100
    assert r["sagemaker"]["state"] == "active"
    assert r["sagemaker"]["endpoints"] == [{"region": "ap-south-1", "name": "evp-video",
                                            "status": "InService"}]
    assert (tmp / "settings" / "prices.json").exists()


def test_prices_cached_one_hour(env):
    c, gpus, _, _ = env
    c.get("/api/settings/deploy/image")
    c.get("/api/settings/deploy/image")
    assert gpus["image"].calls.count("prices") == 1
    c.get("/api/settings/deploy/image/prices?refresh=true")
    assert gpus["image"].calls.count("prices") == 2


def test_running_card_uptime(env):
    c, gpus, _, _ = env
    gpus["video"] = FakeGpu("video", "ready")
    s = c.get("/api/settings/deploy/video").json()["status"]
    assert s["lifecycle"] == "spot" and 290 <= s["uptime_s"] <= 400


def test_unknown_module_rejected(env):
    c, gpus, _, _ = env
    for path in ("/api/settings/deploy/foo/start", "/api/settings/deploy/..%2Fx/stop"):
        assert c.post(path).status_code == 404
    assert c.get("/api/settings/deploy/nope").status_code == 404
    assert not any("ensure_running" in g.calls or "stop" in g.calls for g in gpus.values())


def test_start_stop_post_only(env):
    c, gpus, _, _ = env
    assert c.get("/api/settings/deploy/music/start").status_code == 405
    assert c.get("/api/settings/deploy/music/stop").status_code == 405
    r = c.post("/api/settings/deploy/music/start")
    assert r.status_code == 200 and r.json()["instance_id"] == "i-0123456789abcdef0"
    r = c.post("/api/settings/deploy/music/stop")
    assert r.json()["terminated"] == ["i-0123456789abcdef0"]
    assert gpus["music"].calls.count("ensure_running") == 1
    assert gpus["music"].calls.count("stop") == 1


def test_start_no_capacity(env):
    c, gpus, _, _ = env
    gpus["video"] = FakeGpu("video", "nocap")
    r = c.post("/api/settings/deploy/video/start")
    assert r.status_code == 503 and "InsufficientInstanceCapacity" in r.json()["detail"]


def test_worker_log(env):
    c, gpus, _, _ = env
    r = c.get("/api/settings/deploy/music/log").json()  # no live instance -> heartbeat's
    assert r["instance_id"] == "i-0123456789abcdef0"
    assert len(r["lines"]) == 50 and r["lines"][-1] == "line 119"
    assert r["key"] == "jobs/music-ec2/logs/i-0123456789abcdef0.log"
    assert c.get("/api/settings/deploy/music/log?instance_id=../etc").status_code == 422


def test_cost_and_budgets(env):
    c, _, fail, tmp = env
    r = c.get("/api/settings/cost").json()
    assert r["cost"]["by_module"] == [{"module": "(未标记)", "usd": 1.0},
                                      {"module": "video", "usd": 12.35}]
    assert r["cost"]["total_usd"] == 13.35
    assert [t["threshold"] for t in r["budgets"][0]["thresholds"]] == [200.0, 500.0]
    assert "Subscribers" not in str(r)
    fail.update({"ce", "budgets"})
    assert "cost_error" not in c.get("/api/settings/cost").json()  # cached 6 h
    r = c.get("/api/settings/cost?refresh=true").json()
    assert "AccessDenied" in r["cost_error"] and "AccessDenied" in r["budgets_error"]


def test_cost_permission_error_keeps_working(env):
    c, _, fail, _ = env
    fail.add("ce")
    r = c.get("/api/settings/cost")
    assert r.status_code == 200
    j = r.json()
    assert "cost_error" in j and j["budgets"][0]["limit"] == "1000"


def test_model_cache(env):
    c, _, _, _ = env
    r = c.get("/api/settings/model-cache").json()
    rows = {(p["bucket"], p["prefix"]): p for p in r["prefixes"]}
    p = rows[("evp-models-747411437379-us-east-1", "weights/m1/v1/")]
    assert p["size"] == 15 and p["files"] == 2 and p["model"] == "m1"
    assert ("evp-models-747411437379-us-east-1", "weights/m2/v9/") in rows


def test_account(env):
    c, _, _, _ = env
    r = c.get("/api/settings/account").json()
    assert r["account_id"] == "111122223333"
    assert r["regions"] == ["ap-south-1", "us-east-1"]
    assert "Secret" not in str(r) and "Token" not in str(r)


def test_notifications_and_ui(env):
    c, _, _, tmp = env
    assert c.get("/api/settings/notifications").json() == {"in_app": True, "browser": False}
    assert c.put("/api/settings/notifications",
                 json={"in_app": False, "browser": True}).status_code == 200
    assert store.get("notifications") == {"in_app": False, "browser": True}
    assert (tmp / "settings" / "notifications.json").exists()
    assert c.get("/api/settings/ui").json() == {"theme": "auto", "density": "comfortable"}
    assert c.put("/api/settings/ui", json={"theme": "purple", "density": "compact"}
                 ).status_code == 422
    assert c.put("/api/settings/ui", json={"theme": "dark", "density": "compact"}
                 ).json() == {"theme": "dark", "density": "compact"}
    assert c.get("/api/settings/ui").json()["theme"] == "dark"

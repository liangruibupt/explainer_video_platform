"""HTTP API for the settings page: /api/settings/*.

Write actions are only: start/stop a module's GPU worker (POST, module name checked
against infra/gpu_ec2/modules/*.json, only GpuEC2.ensure_running()/stop() are called) and
the two user settings files. No credential ever leaves the server.
"""
from typing import Literal
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..gpu_ec2 import NoCapacity
from . import aws, store

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _module(module: str) -> str:
    if module not in aws.modules():
        raise HTTPException(404, "no such module")
    return module


@router.get("/deploy")
def deploy():
    with ThreadPoolExecutor(max_workers=4) as ex:
        cards = ex.map(aws.card, aws.modules())
        sm = ex.submit(aws.sagemaker)
        return {"modules": list(cards), "sagemaker": sm.result()}


@router.get("/deploy/{module}")
def deploy_one(module: str):
    return aws.card(_module(module))


@router.get("/deploy/{module}/prices")
def deploy_prices(module: str, refresh: bool = False):
    try:
        rows, ts = aws.prices(_module(module), refresh)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"pricing: {str(e)[:300]}")
    return {"capacity": rows, "ts": ts}


@router.get("/deploy/{module}/log")
def deploy_log(module: str, instance_id: str = "", lines: int = 50):
    try:
        return aws.worker_log(_module(module), instance_id, max(1, min(lines, 500)))
    except ValueError as e:
        raise HTTPException(422, str(e))
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"log: {str(e)[:300]}")


@router.post("/deploy/{module}/start")
def deploy_start(module: str):
    g = aws.gpu(_module(module))
    try:
        iid = g.ensure_running()
    except NoCapacity as e:
        raise HTTPException(503, str(e)[:600])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"start: {str(e)[:300]}")
    return {"module": module, "instance_id": iid, "launch": getattr(g, "last_launch", None)}


@router.post("/deploy/{module}/stop")
def deploy_stop(module: str):
    try:
        ids = aws.gpu(_module(module)).stop()
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"stop: {str(e)[:300]}")
    return {"module": module, "terminated": ids}


@router.get("/cost")
def cost(refresh: bool = False):
    return aws.cost(refresh)


@router.get("/model-cache")
def model_cache(refresh: bool = False):
    return aws.model_cache(refresh)


@router.get("/account")
def account():
    return aws.account()


class NotificationsIn(BaseModel):
    in_app: bool
    browser: bool


class UiIn(BaseModel):
    theme: Literal["light", "dark", "auto"]
    density: Literal["comfortable", "compact"]


@router.get("/notifications")
def notifications_get():
    return store.get("notifications")


@router.put("/notifications")
def notifications_put(body: NotificationsIn):
    return store.put("notifications", body.model_dump())


@router.get("/ui")
def ui_get():
    return store.get("ui")


@router.put("/ui")
def ui_put(body: UiIn):
    return store.put("ui", body.model_dump())

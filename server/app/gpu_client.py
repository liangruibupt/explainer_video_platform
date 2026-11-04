"""Shared helpers for studio clients of the self-managed EC2 GPU backend (gpu_ec2.py).

Each module client (image.py, music.py, video.py) keeps one GpuEC2 per module and maps
its status to the shape the studio UI already reads.
"""
import time

from .gpu_ec2 import GpuEC2, NoCapacity

_gpus = {}

# GpuEC2.status() state -> studio state (ready|starting|idle|failed|unavailable)
_STATE = {"none": "idle", "launching": "starting", "booting": "starting",
          "loading": "starting", "ready": "ready", "busy": "ready"}


def gpu(module: str) -> GpuEC2:
    if module not in _gpus:
        _gpus[module] = GpuEC2(module)
    return _gpus[module]


def status(module: str) -> dict:
    """{'deployed', 'state', 'instances', 'desired', 'instance_type', 'backend'}."""
    try:
        g = gpu(module)
        st = g.status()
    except Exception as e:  # no credentials, no config, API error
        return {"deployed": False, "state": "unavailable", "instances": 0, "desired": 0,
                "instance_type": "", "backend": "ec2", "error": str(e)[:300]}
    hb = st.get("heartbeat") if isinstance(st.get("heartbeat"), dict) else {}
    state = "failed" if hb.get("state") == "failed" else _STATE.get(st["state"], "starting")
    live = st["state"] != "none"
    return {"deployed": True, "state": state, "instances": 1 if state == "ready" else 0,
            "desired": 1 if live else 0, "instance_type": st.get("instance_type", ""),
            "backend": "ec2", "detail": st["state"]}


def run(module: str, req: dict, timeout: int, on_wait=None) -> dict:
    """Submit one job, block until it finishes; returns the result dict (raises on failure)."""
    g = gpu(module)
    try:
        jid = g.submit(req)
    except NoCapacity as e:
        raise RuntimeError(f"{module}: no GPU capacity right now ({e})") from e
    t0 = time.time()
    while time.time() - t0 < timeout:
        res = g.poll(jid)
        if res is not None:
            if not res["ok"]:
                raise RuntimeError(f"{module} worker error: " + str(res.get("error"))[:500])
            return res
        if on_wait:
            on_wait(int(time.time() - t0))
        time.sleep(10)
    raise TimeoutError(f"{module} backend gave no result in {timeout} s (job {jid})")

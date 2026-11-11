"""Motion editor HTTP API (docs/REMOTION_CONTRACT.md §7, part C).

  POST /api/jobs/{id}/editor            link + (re)start the workbench for this job
  GET  /api/motion/status               {running, job_id, port}
  POST /api/motion/stop                 stop the dev server we started
  ANY  /motion/{path}  (+ websocket)    reverse proxy to the workbench dev server (vite base /motion/)
  GET  /api/jobs/{id}/versions          version records; n=1 is final.mp4
  POST /api/jobs/{id}/versions          {project} -> 202 + record, renders in the background
  GET  /api/jobs/{id}/versions/{n}/video

The workbench is patched (infra/motion/patches) so every URL the page uses (modules, public
files via staticFile, /api/export) lives under /motion/; the proxy therefore needs no other
absolute paths. The HMR websocket is proxied too.
"""
from __future__ import annotations

import asyncio
import json
import re

import httpx
from fastapi import APIRouter, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from . import pipeline
from .motion import versions, workbench

router = APIRouter()

_HOP = {b"connection", b"keep-alive", b"proxy-authenticate", b"proxy-authorization", b"te",
        b"trailers", b"transfer-encoding", b"upgrade", b"host"}
_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
_client: httpx.AsyncClient | None = None


def _id(job_id: str) -> str:
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}", job_id):
        raise HTTPException(404, "没有这个任务")
    return job_id


def _remotion_job(job_id: str) -> tuple[str, dict]:
    jid = _id(job_id)
    job = pipeline.read_job(jid)
    if not job:
        raise HTTPException(404, "没有这个任务")
    if job.get("renderer") != "remotion" or job.get("status") != "done":
        raise HTTPException(409, "动效编辑器只能打开已完成的 Remotion 任务（renderer=remotion）")
    if not (pipeline.job_dir(jid) / "remotion" / "src" / "workbench.ts").exists():
        raise HTTPException(409, "该任务没有 Remotion 工程（缺少 remotion/src/workbench.ts）")
    return jid, job


@router.on_event("startup")
def _recover_versions() -> None:
    versions.recover(pipeline.DATA)


# ---------------------------------------------------------------- workbench lifecycle

@router.post("/api/jobs/{job_id}/editor")
async def editor(job_id: str, request: Request):
    """Body (optional) {version: n}: open the editor on version n. Restores that version's scene
    code (snapshots.py) and rebuilds the UI when it differs; the page then loads the version's
    project from /versions/{n}/project (v1 = import from the manifest)."""
    jid, _ = _remotion_job(job_id)
    d = pipeline.job_dir(jid)
    version = None
    raw = await request.body()
    if raw.strip():
        try:
            version = json.loads(raw).get("version")
        except (ValueError, AttributeError):
            raise HTTPException(422, "请求体必须是 JSON：{version: n}")
    if version is not None:
        if isinstance(version, bool) or not isinstance(version, int):
            raise HTTPException(422, "version 必须是版本号")
        rec = next((v for v in versions.list_versions(d) if v["n"] == version), None)
        if not rec or rec["status"] != "done":
            raise HTTPException(404, f"没有 v{version}，或它还没渲染完成")

    def run():
        from .motion import snapshots
        restored = version is not None and snapshots.restore(d, version)
        st = workbench.start(jid, d / "remotion")
        if restored and st.get("running"):
            workbench.rebuild(jid)
        return restored

    try:
        restored = await asyncio.to_thread(run)
    except workbench.MotionError as e:
        raise HTTPException(503, str(e))
    out = {"job_id": jid, "url": "/motion/?import=project"}
    if version is not None:
        from .motion import snapshots
        out.update(version=version, scenes_restored=restored,
                   has_scenes=snapshots.has_snapshot(d, version),
                   project=None if version == 1 else f"/api/jobs/{jid}/versions/{version}/project")
    return out


@router.get("/api/motion/status")
def motion_status():
    return workbench.status()


@router.post("/api/motion/stop")
def motion_stop():
    return workbench.stop()


# ---------------------------------------------------------------- versions

@router.get("/api/jobs/{job_id}/versions")
def version_list(job_id: str):
    jid = _id(job_id)
    if not pipeline.read_job(jid):
        raise HTTPException(404, "没有这个任务")
    return versions.list_versions(pipeline.job_dir(jid))


@router.post("/api/jobs/{job_id}/versions", status_code=202)
async def version_create(job_id: str, request: Request):
    jid, _ = _remotion_job(job_id)
    raw = await request.body()
    if len(raw) > versions.MAX_PROJECT_BYTES:
        raise HTTPException(413, "工程 JSON 太大")
    try:
        body = json.loads(raw)
    except ValueError:
        raise HTTPException(422, "请求体必须是 JSON：{project: ...}")
    if not isinstance(body, dict) or "project" not in body:
        raise HTTPException(422, "请求体必须是 JSON：{project: ...}")
    try:
        rec = await asyncio.to_thread(versions.submit, jid, pipeline.job_dir(jid), body["project"],
                                      body.get("base"))
    except versions.VersionError as e:
        raise HTTPException(422, str(e))
    return JSONResponse(rec, status_code=202)


@router.get("/api/jobs/{job_id}/versions/{n}/project")
def version_project(job_id: str, n: int):
    """The workbench project a saved version was rendered from (v2+; v1 has none: the editor
    imports it from the manifest)."""
    jid = _id(job_id)
    p = versions.vdir(pipeline.job_dir(jid)) / f"v{n}.project.json"
    if n < 2 or not p.is_file():
        raise HTTPException(404, "这个版本没有保存的工程（v1 由原片导入）")
    return FileResponse(p, media_type="application/json")


@router.get("/api/jobs/{job_id}/versions/{n}/video")
def version_video(job_id: str, n: int):
    jid = _id(job_id)
    p = versions.video_path(pipeline.job_dir(jid), n)
    if not p:
        raise HTTPException(404, "没有这个版本，或还没渲染完成")
    return FileResponse(p, media_type="video/mp4")


# ---------------------------------------------------------------- reverse proxy

def _upstream() -> str:
    return f"127.0.0.1:{workbench.PORT}"


def _client_get() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=3.0), follow_redirects=False)
    return _client


_DOWN = ("<!doctype html><meta charset=utf-8><title>动效编辑器未启动</title>"
         "<body style='font-family:sans-serif;padding:2em'>动效编辑器未启动。请在任务页点"
         "「在动效编辑器中打开」，或 POST /api/jobs/{id}/editor。</body>")


@router.api_route("/motion", methods=_METHODS, include_in_schema=False)
def motion_root(request: Request):
    q = request.url.query
    return RedirectResponse("/motion/" + (f"?{q}" if q else ""), status_code=307)


_ASSET = re.compile(r"^assets/[A-Za-z0-9._-]+\.(js|css)$")
_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}


def _from_build(path: str, request: Request):
    """Serve the editor UI from the production build (workbench.build) when there is one for
    the linked job: index.html (no-cache) and hashed assets (immutable, pre-gzipped). Every
    other path (public files, /motion/api/export) still goes to the dev server."""
    dist = workbench.built_dist()
    if dist is None or not workbench.status()["running"]:
        return None
    if path in ("", "index.html"):
        return FileResponse(dist / "index.html", media_type="text/html; charset=utf-8",
                            headers={"Cache-Control": "no-cache"})
    if not _ASSET.match(path):
        return None
    f = dist / path
    if not f.is_file():
        return None
    headers = {"Cache-Control": "public, max-age=31536000, immutable", "Vary": "Accept-Encoding"}
    gz = f.with_name(f.name + ".gz")
    if gz.is_file() and "gzip" in request.headers.get("accept-encoding", ""):
        headers["Content-Encoding"] = "gzip"
        return FileResponse(gz, media_type=_TYPES[f.suffix], headers=headers)
    return FileResponse(f, media_type=_TYPES[f.suffix], headers=headers)


@router.api_route("/motion/{path:path}", methods=_METHODS, include_in_schema=False)
async def motion_proxy(path: str, request: Request):
    if request.method in ("GET", "HEAD"):
        built = _from_build(path, request)
        if built is not None:
            return built
    raw_path = request.scope.get("raw_path") or request.url.path.encode()
    q = request.scope.get("query_string") or b""
    url = f"http://{_upstream()}{raw_path.decode('latin-1')}" + (f"?{q.decode('latin-1')}" if q else "")
    headers = [(k, v) for k, v in request.headers.raw if k.lower() not in _HOP]
    headers.append((b"host", _upstream().encode()))
    client = _client_get()
    try:
        req = client.build_request(request.method, url, headers=headers,
                                   content=await request.body())
        resp = await client.send(req, stream=True)
    except httpx.TransportError:
        return HTMLResponse(_DOWN, status_code=503)
    out = StreamingResponse(resp.aiter_raw(), status_code=resp.status_code,
                            background=BackgroundTask(resp.aclose))
    out.raw_headers = [(k, v) for k, v in resp.headers.raw if k.lower() not in _HOP]
    return out


@router.websocket("/motion/{path:path}")
async def motion_ws(ws: WebSocket, path: str):
    """Vite's HMR socket (`/motion/?token=...`, subprotocol vite-hmr)."""
    from websockets.asyncio.client import connect
    from websockets.exceptions import ConnectionClosed

    raw_path = (ws.scope.get("raw_path") or ws.url.path.encode()).decode("latin-1")
    q = (ws.scope.get("query_string") or b"").decode("latin-1")
    url = f"ws://{_upstream()}{raw_path}" + (f"?{q}" if q else "")
    subs = ws.scope.get("subprotocols") or None
    try:
        up = await connect(url, subprotocols=subs, open_timeout=5, max_size=None)
    except Exception:  # noqa: BLE001 - upstream down or refused
        await ws.close(code=1011)
        return
    await ws.accept(subprotocol=up.subprotocol)

    async def down():  # browser -> vite
        while True:
            m = await ws.receive()
            if m["type"] == "websocket.disconnect":
                return
            if m.get("text") is not None:
                await up.send(m["text"])
            elif m.get("bytes") is not None:
                await up.send(m["bytes"])

    async def upward():  # vite -> browser
        try:
            async for m in up:
                if isinstance(m, str):
                    await ws.send_text(m)
                else:
                    await ws.send_bytes(m)
        except ConnectionClosed:
            return

    tasks = [asyncio.create_task(down()), asyncio.create_task(upward())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        await up.close()
        try:
            await ws.close()
        except RuntimeError:
            pass

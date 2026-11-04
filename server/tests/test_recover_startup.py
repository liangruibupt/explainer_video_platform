"""Workflow recovery runs when the server starts, not when app.main is imported.

A helper script or test that imports app.main against a live data dir used to mark the
live run's running nodes failed (服务重启时节点还在运行); found in the avatar integration run.
"""
import json
import os

from fastapi.testclient import TestClient


def _put_running_run(runs_dir, rid="20261104-000000-recov1"):
    d = runs_dir / rid
    d.mkdir(parents=True, exist_ok=True)
    run = {"id": rid, "status": "running",
           "workflow": {"nodes": [{"id": "a", "type": "input.text"}], "edges": []},
           "nodes": {"a": {"status": "running"}}, "events": []}
    (d / "run.json").write_text(json.dumps(run))
    return d / "run.json"


def test_import_does_not_recover_but_startup_does():
    from app import main
    from app.workflows import engine
    p = _put_running_run(engine.RUNS)
    try:
        import importlib
        importlib.reload(main)  # what a second importer does
        assert json.loads(p.read_text())["nodes"]["a"]["status"] == "running"
        with TestClient(main.app):  # runs the startup hook, like uvicorn
            pass
        node = json.loads(p.read_text())["nodes"]["a"]
        assert node["status"] == "failed" and "重启" in node["error"]
    finally:
        p.unlink(missing_ok=True)
        os.rmdir(p.parent)

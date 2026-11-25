#!/usr/bin/env python3
"""Live test of the mesh module from 0 instances (docs/MESH.md section 5).

  bench.py [--image PATH | --prompt TEXT] [--cases 2mini,2,2mini+tex] [--out DIR] [--stop]

Gets the input (a fixed image, or a concept image from the image module with the 4.6
prompt template), then runs each case on the mesh worker in order through the studio
client (server/app/mesh.py), so the first case includes the cold start. Writes
OUT/<case>.glb, OUT/input.png and OUT/results.json (wall seconds from submit, worker meta,
instance bootstrap timings). --stop terminates the mesh worker at the end instead of
leaving it to its 900 s idle shutdown.
"""
import argparse, json, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))
from app import gpu_client, image, mesh, mesh_api  # noqa: E402

CASES = {"2mini": ("hunyuan3d-2mini", False), "2": ("hunyuan3d-2", False),
         "2mini+tex": ("hunyuan3d-2mini", True), "2+tex": ("hunyuan3d-2", True)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image")
    ap.add_argument("--prompt", default="一只坐着的猫")
    ap.add_argument("--cases", default="2mini,2,2mini+tex")
    ap.add_argument("--out", default=str(ROOT / "data" / "mesh_smoke"))
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--stop", action="store_true")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    res = {"started": time.time(), "cases": []}
    if a.image:
        src = Path(a.image).read_bytes()
        res["input"] = {"type": "file", "path": a.image}
    elif (out / "input.png").exists():
        src = (out / "input.png").read_bytes()
        res["input"] = {"type": "reused", "prompt": a.prompt}
    else:
        t0 = time.time()
        p = mesh_api.concept_prompt(a.prompt)
        src = image.generate(p, ratio="1:1", seed=a.seed)[0]
        res["input"] = {"type": "concept", "prompt": p, "wall_s": round(time.time() - t0, 1)}
        print("concept image", res["input"], flush=True)
    (out / "input.png").write_bytes(src)
    for c in a.cases.split(","):
        model, tex = CASES[c]
        t0 = time.time()
        meta = {}
        try:
            glb = mesh.generate(src, model=model, texture=tex, seed=a.seed, meta_out=meta,
                                on_wait=lambda s: None)
            (out / f"{c.replace('+', '_')}.glb").write_bytes(glb)
            ok = True
        except Exception as e:  # noqa: BLE001 - recorded, next case still runs
            meta["error"] = str(e)[:500]
            ok = False
        row = {"case": c, "ok": ok, "wall_s": round(time.time() - t0, 1), "meta": meta}
        res["cases"].append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        (out / "results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
    hb = gpu_client.gpu("mesh").heartbeat()
    res["heartbeat"] = hb
    (out / "results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
    if a.stop:
        print("stopped", gpu_client.gpu("mesh").stop(), flush=True)


if __name__ == "__main__":
    main()

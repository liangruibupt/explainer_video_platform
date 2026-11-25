#!/usr/bin/env python3
"""Compare two bench.py output dirs (e.g. the Python 3.10 and 3.12 images, docs/MESH.md 5).

  compare.py DIR_A DIR_B

Per case: worker render_s, raw / final faces, peak VRAM from results.json, and the GLB
bounding-box size read from the POSITION accessors' min/max (no mesh library needed). Prints
a table and exits 1 when a bbox side differs by more than 1 % or raw faces by more than 5 %.
"""
import json, struct, sys
from pathlib import Path


def bbox(glb: Path):
    b = glb.read_bytes()
    n = struct.unpack_from("<I", b, 12)[0]
    g = json.loads(b[20:20 + n])
    lo, hi = [1e9] * 3, [-1e9] * 3
    for node in g.get("nodes", []):  # bench GLBs: one mesh node, identity transform
        assert not any(k in node for k in ("matrix", "rotation", "scale")), "transformed node"
    for m in g["meshes"]:
        for p in m["primitives"]:
            a = g["accessors"][p["attributes"]["POSITION"]]
            lo = [min(x, y) for x, y in zip(lo, a["min"])]
            hi = [max(x, y) for x, y in zip(hi, a["max"])]
    return [h - l for l, h in zip(lo, hi)]


def main(a: Path, b: Path):
    ra = {c["case"]: c for c in json.loads((a / "results.json").read_text())["cases"]}
    rb = {c["case"]: c for c in json.loads((b / "results.json").read_text())["cases"]}
    bad = False
    print("| 用例 | render_s A → B | 原始面数 A → B | 面数 A → B | 峰值显存 A → B | 包围盒最大偏差 |")
    print("|---|---|---|---|---|---|")
    for case in ra:
        if case not in rb or not (ra[case]["ok"] and rb[case]["ok"]):
            continue
        ma, mb = ra[case]["meta"], rb[case]["meta"]
        f = case.replace("+", "_") + ".glb"
        sa, sb = bbox(a / f), bbox(b / f)
        dbox = max(abs(x - y) / x for x, y in zip(sa, sb))
        dfaces = abs(ma["raw_faces"] - mb["raw_faces"]) / ma["raw_faces"]
        bad |= dbox > 0.01 or dfaces > 0.05
        print(f"| {case} | {ma['render_s']} → {mb['render_s']} | {ma['raw_faces']:,} → {mb['raw_faces']:,} "
              f"({dfaces:.1%}) | {ma['faces']:,} → {mb['faces']:,} | {ma['peak_vram_gb']} → {mb['peak_vram_gb']} GB "
              f"| {dbox:.2%} |")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))

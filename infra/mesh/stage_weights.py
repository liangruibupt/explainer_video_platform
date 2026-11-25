#!/usr/bin/env python3
"""Stage the Hunyuan3D-2 weights HF -> S3 at pinned commits, one file at a time.

  stage_weights.py WORKDIR [--dry-run]

Small enough (~20 GB) to run on the control host: each file is downloaded into WORKDIR,
uploaded with boto3 (multipart), checked by size and deleted before the next one, so the
disk never holds more than the largest file (3.7 GB). Uses boto3 rather than the AWS CLI.

S3 layout (bucket evp-models-<acct>-us-east-1), the sha is tencent/Hunyuan3D-2's commit:
  weights/hunyuan3d-2/<sha>/
    tencent/Hunyuan3D-2/hunyuan3d-dit-v2-0/           shape 2.0 (config.yaml, model.fp16.safetensors)
    tencent/Hunyuan3D-2/hunyuan3d-paint-v2-0-turbo/   Paint multiview diffusion (texture path)
    tencent/Hunyuan3D-2/hunyuan3d-delight-v2-0/       Paint de-lighting (texture path)
    tencent/Hunyuan3D-2mini/hunyuan3d-dit-v2-mini/    shape 2mini (repo pinned below)
    _evp_manifest.json                                repos, revisions, files, bytes
The container sets HY3DGEN_MODELS=/opt/ml/model, so hy3dgen's loaders find these paths
locally (smart_load_model / Hunyuan3DPaintPipeline.from_pretrained) and never call HF.
Each checkpoint includes its own VAE and DINOv2 conditioner, so no vae-* folder is needed.
"""
import json, os, sys, time
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
import boto3
from huggingface_hub import hf_hub_download, HfApi

R = "us-east-1"
H2_SHA = "9cd649ba6913f7a852e3286bad86bfa9a2d83dcf"     # tencent/Hunyuan3D-2
MINI_SHA = "f90a0f7df7d5e6f71109cf333f6a95a0ae3194a6"   # tencent/Hunyuan3D-2mini
PREFIX = f"weights/hunyuan3d-2/{H2_SHA}/"
# (repo, revision, subfolder, file names to skip). Only what the runtime loads: the fp16
# safetensors of the shape DiTs; Paint turbo without the unet .safetensors (its UNet2p5D
# loader reads diffusion_pytorch_model.bin only, hunyuanpaint/unet/modules.py).
REPOS = [
    ("tencent/Hunyuan3D-2mini", MINI_SHA, "hunyuan3d-dit-v2-mini", {"model.fp16.ckpt"}),
    ("tencent/Hunyuan3D-2", H2_SHA, "hunyuan3d-dit-v2-0",
     {"model.ckpt", "model.fp16.ckpt", "model.safetensors", "model_fp16.ckpt"}),
    ("tencent/Hunyuan3D-2", H2_SHA, "hunyuan3d-delight-v2-0", set()),
    ("tencent/Hunyuan3D-2", H2_SHA, "hunyuan3d-paint-v2-0-turbo",
     {"unet/diffusion_pytorch_model.safetensors", ".gitattributes", "README.md"}),
]


def plan():
    api = HfApi()
    out = []
    for repo, rev, sub, skip in REPOS:
        info = api.model_info(repo, revision=rev, files_metadata=True)
        for s in info.siblings:
            if not s.rfilename.startswith(sub + "/"):
                continue
            rel = s.rfilename[len(sub) + 1:]
            if rel in skip:
                continue
            out.append((repo, rev, s.rfilename, s.size or 0))
    return out


def main(work, dry=False):
    acct = boto3.client("sts").get_caller_identity()["Account"]
    bucket = f"evp-models-{acct}-{R}"
    s3 = boto3.client("s3", region_name=R)
    files = plan()
    print(f"{len(files)} files, {sum(f[3] for f in files) / 1e9:.2f} GB -> s3://{bucket}/{PREFIX}", flush=True)
    if dry:
        for f in files:
            print(f"  {f[0]}/{f[2]}  {f[3] / 1e6:.1f} MB")
        return
    have = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=PREFIX):
        for o in page.get("Contents", []):
            have[o["Key"]] = o["Size"]
    manifest = {"repos": [{"repo": r, "revision": v, "dir": f"{r}/{s}"} for r, v, s, _ in REPOS],
                "files": {}}
    for repo, rev, name, size in files:
        key = f"{PREFIX}{repo}/{name}"
        manifest["files"][f"{repo}/{name}"] = size
        if have.get(key) == size:
            continue
        t0 = time.time()
        p = Path(hf_hub_download(repo, name, revision=rev, local_dir=Path(work) / repo))
        assert p.stat().st_size == size, (name, p.stat().st_size, size)
        s3.upload_file(str(p), bucket, key)
        print(f"  {repo}/{name} {size / 1e6:.0f} MB in {time.time() - t0:.0f} s", flush=True)
        p.unlink()
    got = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=PREFIX):
        for o in page.get("Contents", []):
            got[o["Key"].removeprefix(PREFIX)] = o["Size"]
    got.pop("_evp_manifest.json", None)
    if got != manifest["files"]:
        sys.exit(f"S3 mismatch: {sorted(set(got.items()) ^ set(manifest['files'].items()))[:10]}")
    manifest["count"], manifest["bytes"] = len(got), sum(got.values())
    s3.put_object(Bucket=bucket, Key=PREFIX + "_evp_manifest.json",
                  Body=json.dumps(manifest, indent=1).encode())
    print("OK", f"s3://{bucket}/{PREFIX}", manifest["count"], "files", manifest["bytes"], "bytes", flush=True)


if __name__ == "__main__":
    main(sys.argv[1], "--dry-run" in sys.argv)

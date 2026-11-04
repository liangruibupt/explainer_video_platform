#!/usr/bin/env python3
"""Download YuE2-3B + YuE2-Vae at pinned revisions and copy them to S3 for SageMaker.

  stage_weights.py WORKDIR

Layout in S3 (uncompressed ModelDataSource lands it under /opt/ml/model):
  s3://evp-models-<acct>-us-east-1/weights/yue2-3b/<sha>/model/...   -> /opt/ml/model/model
  s3://evp-models-<acct>-us-east-1/weights/yue2-3b/<sha>/vae/...     -> /opt/ml/model/vae
  s3://.../_evp_manifest.json  file list with bytes, revisions
Only the files the yue2 runtime loads are copied (no demo audio or figures).
The local copy is left in WORKDIR; delete it yourself after the check passes.
"""
import json, sys
from pathlib import Path

import boto3
from huggingface_hub import snapshot_download

MODEL = ("m-a-p/YuE2-3B", "8f24312f187763b9854adeec87e437e2b03bbff1")
VAE = ("m-a-p/YuE2-Vae", "152733a19ad43aa67e367f9b5503ef8075bb5126")
# Same file boundary as yue2.storage.resolve_model.
FILES = ["config.json", "generation_config.json", "yue2_generation_config.json",
         "weights_manifest.json", "model.safetensors", "qwen.tiktoken", "modeling_yue2.py",
         "modeling_vae.py", "LICENSE", "THIRD_PARTY_NOTICES.md", "licenses/*"]
R = "us-east-1"


def main(work):
    work = Path(work)
    acct = boto3.client("sts").get_caller_identity()["Account"]
    bucket = f"evp-models-{acct}-{R}"
    prefix = f"weights/yue2-3b/{MODEL[1]}/"
    s3 = boto3.client("s3", region_name=R)
    manifest = {"model": {"repo": MODEL[0], "revision": MODEL[1]},
                "vae": {"repo": VAE[0], "revision": VAE[1]}, "files": {}}
    for sub, (repo, rev) in (("model", MODEL), ("vae", VAE)):
        d = work / sub
        snapshot_download(repo, revision=rev, local_dir=d, allow_patterns=FILES)
        for f in sorted(p for p in d.rglob("*") if p.is_file() and ".cache" not in p.parts):
            key = f"{sub}/{f.relative_to(d).as_posix()}"
            s3.upload_file(str(f), bucket, prefix + key)
            manifest["files"][key] = f.stat().st_size
            print("uploaded", key, f.stat().st_size, flush=True)
    # Verify: every file present in S3 with the same size, nothing extra.
    got = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for o in page.get("Contents", []):
            got[o["Key"].removeprefix(prefix)] = o["Size"]
    got.pop("_evp_manifest.json", None)
    if got != manifest["files"]:
        sys.exit(f"S3 mismatch: local {len(manifest['files'])} files, s3 {len(got)}")
    manifest["count"], manifest["bytes"] = len(got), sum(got.values())
    s3.put_object(Bucket=bucket, Key=prefix + "_evp_manifest.json",
                  Body=json.dumps(manifest, indent=1).encode())
    print("OK", f"s3://{bucket}/{prefix}", manifest["count"], "files", manifest["bytes"], "bytes")


if __name__ == "__main__":
    main(sys.argv[1])

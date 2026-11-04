#!/usr/bin/env python3
"""Stage InfiniteTalk and LongCat-Video-Avatar-1.5 weights HF -> S3 at pinned commits.

Runs on the builder EC2 (infra/avatar/builder.sh), not on the 17 GB control host.

  stage_weights.py WORKDIR [infinitetalk|longcat ...]

S3 layout (bucket evp-models-<acct>-us-east-1), the sha in each prefix is the main repo's:
  weights/infinitetalk/<MeiGen-AI/InfiniteTalk sha>/
      Wan2.1-I2V-14B-480P/  chinese-wav2vec2-base/  InfiniteTalk/{single,quant_models}/  lora/
  weights/longcat-avatar-1.5/<meituan-longcat/LongCat-Video-Avatar-1.5 sha>/
      LongCat-Video/{tokenizer,text_encoder,vae,scheduler}  LongCat-Video-Avatar-1.5/...
  each with _evp_manifest.json (repos, revisions, files, bytes).
Only files the runtimes load are copied: the LongCat-Video base DiT and its LoRAs are not
used by the avatar pipeline (it loads only tokenizer/text_encoder/vae from the base), and
whisper-large-v3 keeps only model.safetensors. Each repo is uploaded with s5cmd and its
local copy deleted before the next, so the builder disk only holds one repo at a time.
"""
import json, os, shutil, subprocess, sys, time
from pathlib import Path

import boto3
from huggingface_hub import snapshot_download

R = "us-east-1"
# (repo, revision sha, local subdir, allow_patterns or None, ignore_patterns or None)
MODELS = {
    "infinitetalk": ("weights/infinitetalk/d59847ebdacf19245bfca3fb23311c0cada8378a/", [
        ("Wan-AI/Wan2.1-I2V-14B-480P", "6b73f84e66371cdfe870c72acd6826e1d61cf279", "Wan2.1-I2V-14B-480P",
         None, ["examples/*", "assets/*", "*.mp4"]),
        # refs/pr/1 adds model.safetensors (transformers refuses torch.load of .bin on torch<2.6).
        ("TencentGameMate/chinese-wav2vec2-base", "5f7a6cfdfc5440ec78748f79a9dd66f077c2b463",
         "chinese-wav2vec2-base", ["config.json", "preprocessor_config.json", "model.safetensors"], None),
        ("MeiGen-AI/InfiniteTalk", "d59847ebdacf19245bfca3fb23311c0cada8378a", "InfiniteTalk",
         ["single/*", "README.md"], None),  # quant_models/ not used: bf16 fits 48 GB and 96 GB GPUs
        ("Kijai/WanVideo_comfy", "8260d429d19fd7a72304cad059160b95d843913f", "lora",
         ["Wan21_T2V_14B_lightx2v_cfg_step_distill_lora_rank32.safetensors"], None),
    ]),
    "longcat": ("weights/longcat-avatar-1.5/92016c71d5d318d0f5d84e4db30015a571484ab6/", [
        ("meituan-longcat/LongCat-Video", "03b55529b1d1d4045f5fbe14d65c8c6e8116b278", "LongCat-Video",
         ["tokenizer/*", "text_encoder/*", "vae/*", "scheduler/*", "*.json", "LICENSE"], None),
        ("meituan-longcat/LongCat-Video-Avatar-1.5", "92016c71d5d318d0f5d84e4db30015a571484ab6",
         "LongCat-Video-Avatar-1.5",
         ["base_model/*", "base_model_int8/*", "lora/*", "scheduler/*", "vocal_separator/*", "*.json",
          "whisper-large-v3/*.json", "whisper-large-v3/merges.txt", "whisper-large-v3/model.safetensors"],
         None),
    ]),
}


def sh(*a):
    subprocess.run(a, check=True)


def stage(work, model):
    acct = boto3.client("sts").get_caller_identity()["Account"]
    bucket = f"evp-models-{acct}-{R}"
    prefix, repos = MODELS[model]
    s3 = boto3.client("s3", region_name=R)
    manifest = {"model": model, "repos": [], "files": {}}
    for repo, rev, sub, allow, ignore in repos:
        d = Path(work) / model / sub
        t0 = time.time()
        snapshot_download(repo, revision=rev, local_dir=d, allow_patterns=allow, ignore_patterns=ignore,
                          max_workers=16)
        shutil.rmtree(d / ".cache", ignore_errors=True)
        files = {f"{sub}/{f.relative_to(d).as_posix()}": f.stat().st_size
                 for f in d.rglob("*") if f.is_file()}
        size = sum(files.values())
        print(f"{repo}@{rev[:8]}: {len(files)} files {size / 1e9:.1f} GB in {time.time() - t0:.0f} s", flush=True)
        t0 = time.time()
        sh("s5cmd", "--numworkers", "64", "cp", "--concurrency", "16", str(d) + "/", f"s3://{bucket}/{prefix}{sub}/")
        print(f"  uploaded in {time.time() - t0:.0f} s", flush=True)
        manifest["repos"].append({"repo": repo, "revision": rev, "dir": sub, "files": len(files), "bytes": size})
        manifest["files"].update(files)
        shutil.rmtree(d)
    got = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for o in page.get("Contents", []):
            got[o["Key"].removeprefix(prefix)] = o["Size"]
    got.pop("_evp_manifest.json", None)
    if got != manifest["files"]:
        miss = set(manifest["files"]) ^ set(got)
        sys.exit(f"S3 mismatch for {model}: {sorted(miss)[:10]}")
    manifest["count"], manifest["bytes"] = len(got), sum(got.values())
    s3.put_object(Bucket=bucket, Key=prefix + "_evp_manifest.json", Body=json.dumps(manifest, indent=1).encode())
    print("OK", model, f"s3://{bucket}/{prefix}", manifest["count"], "files", manifest["bytes"], "bytes", flush=True)


if __name__ == "__main__":
    for m in sys.argv[2:] or list(MODELS):
        stage(sys.argv[1], m)

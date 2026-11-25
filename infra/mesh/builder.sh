#!/bin/bash
# Builder EC2 user-data body (launched by infra/mesh/builder.py). Runs as root on AL2023:
#   1. hard cap: poweroff (= terminate) after CAP_MIN minutes whatever happens
#   2. fetch infra/mesh/container from s3://<bucket>/jobs/mesh-build/<run>/src.tgz
#   3. docker build + push evp/mesh:<tag> (the build runs the CPU import check)
#   4. write status.json + log to the same S3 run prefix, then power off.
# Weights are not staged here: infra/mesh/stage_weights.py runs on the control host.
set -uo pipefail
RUN=__RUN__; BUCKET=__BUCKET__; TAG=__TAG__; CAP_MIN=__CAP_MIN__; REPO=__REPO__
P="s3://$BUCKET/jobs/mesh-build/$RUN"
export AWS_DEFAULT_REGION=us-east-1 HOME=/root
LOG=/var/log/mesh-build.log; exec >> $LOG 2>&1
shutdown -h +$CAP_MIN "mesh builder hard cap"
log() { echo "$(date -u +%FT%TZ) $*"; }
push_log() { aws s3 cp --only-show-errors $LOG "$P/build.log" || true; }
( while sleep 60; do push_log; done ) &
finish() { log "finish: $1"; echo "{\"result\": \"$1\", \"tag\": \"$TAG\", \"ts\": $(date +%s)}" > /tmp/status.json
  aws s3 cp --only-show-errors /tmp/status.json "$P/status.json"; push_log; sleep 5; shutdown -h now; exit 0; }

# Work disk: the gp3 data volume (/dev/sdf), which can attach a few seconds after boot.
DEV=""
for _ in $(seq 1 30); do
  DEV=$(lsblk -dpnbo NAME,SIZE | awk '$2 >= 100*1024^3 {print $1}' | head -1)
  [ -n "$DEV" ] && break; sleep 5
done
log "lsblk: $(lsblk -dpno NAME,SIZE | tr '\n' ';') -> $DEV"
[ -n "$DEV" ] || finish "no data disk"
mkfs.xfs -f "$DEV" && mkdir -p /work && mount "$DEV" /work || finish "mount failed"

dnf install -y -q docker tar gzip || finish "dnf failed"
mkdir -p /work/docker && echo '{"data-root": "/work/docker"}' > /etc/docker/daemon.json
systemctl start docker
mkdir -p /work/src && aws s3 cp --only-show-errors "$P/src.tgz" /work/src.tgz && tar -xzf /work/src.tgz -C /work/src || finish "src fetch failed"

build() {
  cd /work/src/container
  aws ecr get-login-password | docker login -u AWS --password-stdin "${REPO%%/*}" >/dev/null || return 1
  DOCKER_BUILDKIT=0 docker build -t "$REPO:$TAG" . > /work/build.out 2>&1 || { tail -120 /work/build.out; return 1; }
  grep -E '^(ok  |SKIP|FAIL|imports ok|u2net md5)' /work/build.out | tail -30
  docker push -q "$REPO:$TAG" || return 1
  docker image inspect "$REPO:$TAG" --format 'image bytes {{.Size}}'
  docker run --rm --entrypoint python "$REPO:$TAG" -c \
    "import torch, diffusers, transformers, huggingface_hub, sys; print('versions: python', sys.version.split()[0], 'torch', torch.__version__, 'diffusers', diffusers.__version__, 'transformers', transformers.__version__, 'hub', huggingface_hub.__version__)"
}
log "start: image build"
build > /work/build.log 2>&1; RC=$?
cat /work/build.log
finish "build=$RC"

#!/bin/bash
# Builder EC2 user-data body (launched by infra/avatar/builder.py). Runs as root on AL2023:
#   1. hard cap: poweroff (= terminate) after CAP_MIN minutes whatever happens
#   2. fetch infra/avatar from s3://<bucket>/jobs/avatar-build/<run>/src.tgz
#   3. in parallel: stage_weights.py (HF -> S3) and docker build + push evp/avatar:<tag>
#   4. write status.json + log to the same S3 run prefix, then power off.
set -uo pipefail
RUN=__RUN__; BUCKET=__BUCKET__; TAG=__TAG__; CAP_MIN=__CAP_MIN__; REPO=__REPO__; ONLY=__ONLY__
P="s3://$BUCKET/jobs/avatar-build/$RUN"
export AWS_DEFAULT_REGION=us-east-1 HOME=/root
LOG=/var/log/avatar-build.log; exec >> $LOG 2>&1
shutdown -h +$CAP_MIN "avatar builder hard cap"
log() { echo "$(date -u +%FT%TZ) $*"; }
push_log() { aws s3 cp --only-show-errors $LOG "$P/build.log" || true; }
( while sleep 60; do push_log; done ) &
finish() { log "finish: $1"; echo "{\"result\": \"$1\", \"tag\": \"$TAG\", \"ts\": $(date +%s)}" > /tmp/status.json
  aws s3 cp --only-show-errors /tmp/status.json "$P/status.json"; push_log; sleep 5; shutdown -h now; exit 0; }

# Work disk: the large gp3 data volume (/dev/sdf). It can attach a few seconds after boot,
# so wait for a second disk of >= 300 GB rather than racing the first lsblk.
DEV=""
for _ in $(seq 1 30); do
  DEV=$(lsblk -dpnbo NAME,SIZE | awk '$2 >= 300*1024^3 {print $1}' | head -1)
  [ -n "$DEV" ] && break; sleep 5
done
log "lsblk: $(lsblk -dpno NAME,SIZE | tr '\n' ';') -> $DEV"
[ -n "$DEV" ] || finish "no data disk"
mkfs.xfs -f "$DEV" && mkdir -p /work && mount "$DEV" /work || finish "mount failed"
log "work disk $DEV $(df -h /work | tail -1)"

dnf install -y -q docker git python3-pip tar gzip || finish "dnf failed"
mkdir -p /work/docker && echo '{"data-root": "/work/docker"}' > /etc/docker/daemon.json
systemctl start docker
curl -fsSL https://github.com/peak/s5cmd/releases/download/v2.3.0/s5cmd_2.3.0_Linux-64bit.tar.gz | tar -xz -C /usr/local/bin s5cmd
python3 -m pip install -q "huggingface_hub[hf_xet]==0.36.2" boto3 || finish "pip failed"
export HF_HUB_ENABLE_HF_TRANSFER=0 HF_XET_HIGH_PERFORMANCE=1 HF_HUB_DISABLE_PROGRESS_BARS=1

mkdir -p /work/src && aws s3 cp --only-show-errors "$P/src.tgz" /work/src.tgz && tar -xzf /work/src.tgz -C /work/src || finish "src fetch failed"

stage() { python3 /work/src/stage_weights.py /work/hf "$1" > /work/stage_$1.log 2>&1; echo $? > /work/stage_$1.rc; }
build() {
  cd /work/src/container
  aws ecr get-login-password | docker login -u AWS --password-stdin "${REPO%%/*}" >/dev/null || return 1
  DOCKER_BUILDKIT=0 docker build -t "$REPO:$TAG" . > /work/build.out 2>&1 || { tail -80 /work/build.out; return 1; }
  docker push -q "$REPO:$TAG" >> /work/build.out 2>&1 || return 1
  docker image inspect "$REPO:$TAG" --format '{{.Size}}' >> /work/build.out
}
log "start: weights + image build in parallel (ONLY=$ONLY)"
if [ "$ONLY" = image ]; then echo 0 > /work/stage_infinitetalk.rc; echo 0 > /work/stage_longcat.rc; S1=; S2=
else ( stage infinitetalk ) & S1=$!; ( stage longcat ) & S2=$!; fi
( build > /work/build.log 2>&1; echo $? > /work/build.rc ) & B=$!
while { [ -n "$S1" ] && kill -0 $S1 2>/dev/null; } || { [ -n "$S2" ] && kill -0 $S2 2>/dev/null; } || kill -0 $B 2>/dev/null; do
  sleep 60
  for f in /work/stage_infinitetalk.log /work/stage_longcat.log /work/build.log; do [ -f $f ] && { echo "--- $f"; tail -3 $f; }; done
  echo "--- disk $(df -h /work | tail -1)"
done
for f in /work/stage_infinitetalk.log /work/stage_longcat.log /work/build.log /work/build.out; do echo "===== $f"; tail -40 $f; done
R="it=$(cat /work/stage_infinitetalk.rc) lc=$(cat /work/stage_longcat.rc) build=$(cat /work/build.rc)"
finish "$R"

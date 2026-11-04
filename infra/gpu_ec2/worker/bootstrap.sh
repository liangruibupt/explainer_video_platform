#!/bin/bash
# EVP GPU worker bootstrap. Runs once per instance from the evp-worker systemd unit that
# the user-data installs. Idempotent: each stage leaves a marker in /opt/evp/state and is
# skipped when the marker exists. All output goes to /var/log/evp-worker.log.
#
# Stages: hard-cap poweroff -> driver check -> NVMe + docker data-root -> ECR login + pull
# -> weights S3 -> NVMe -> docker run (127.0.0.1:8080) -> exec worker.py (waits for /ping,
# then polls the S3 queue; idle shutdown after idle_shutdown_s).
set -uo pipefail
exec >> /var/log/evp-worker.log 2>&1
CFG=/opt/evp/config.json
ST=/opt/evp/state; mkdir -p "$ST"
cfg() { python3 -c "import json,sys;print(json.load(open('$CFG'))$1)"; }
REGION=$(cfg "['region']"); IMAGE=$(cfg "['image']"); WEIGHTS=$(cfg "['weights']")
MOUNT=$(cfg "['model_mount']"); PORT=$(cfg "['container_port']")
HARD_CAP_MIN=$(( $(cfg "['hard_cap_s']") / 60 ))
export AWS_DEFAULT_REGION="$REGION"
log() { echo "$(date -u +%FT%TZ) [bootstrap] $*"; }
stamp() { date +%s > "$ST/$1"; }
hb() { python3 /opt/evp/worker.py --heartbeat "$1" ${2:+--note "$2"} || true; }
die() { log "FATAL: $*"; hb failed "$*"; sleep 30; shutdown -h now; exit 1; }

# 1. Hard cap: never run longer than hard_cap_s from boot, whatever else happens.
if [ ! -f "$ST/cap" ]; then shutdown -h "+$HARD_CAP_MIN" "evp hard cap"; stamp cap; log "hard cap in ${HARD_CAP_MIN} min"; fi
stamp boot_seen 2>/dev/null || true

# 2. Python deps for the worker (boto3). AL2023 ships python3-boto3 in its repo.
python3 -c "import boto3" 2>/dev/null || dnf install -y -q python3-boto3 || python3 -m pip install -q boto3 \
  || die "cannot install boto3"
hb booting

# 3. Driver check (CUDA 13 base image + Blackwell need >= min_driver_major).
DRV=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1)
echo "$DRV" > "$ST/driver"; log "nvidia driver $DRV"
[ "${DRV%%.*}" -ge "$(cfg "['min_driver_major']")" ] || die "driver $DRV too old"

# 4. Local NVMe: DLAMI mounts the instance store at /opt/dlami/nvme. Fall back to
#    formatting the first unused NVMe disk ourselves.
NV=/opt/dlami/nvme
for _ in $(seq 1 3); do mountpoint -q "$NV" && break; sleep 5; done  # DLAMI did not auto-mount it in tests
if ! mountpoint -q "$NV"; then
  # The instance store disks report model "Amazon EC2 NVMe Instance Storage"; take the
  # first one with nothing mounted on it or its partitions.
  DEV=""
  for d in $(lsblk -dpno NAME,TYPE | awk '$2=="disk"{print $1}'); do
    lsblk -dno MODEL "$d" | grep -qi "instance storage" || continue
    [ -z "$(lsblk -no MOUNTPOINT "$d" | tr -d '[:space:]')" ] && { DEV=$d; break; }
  done
  log "lsblk: $(lsblk -dpno NAME,MODEL,SIZE | tr '\n' ';')"
  [ -n "$DEV" ] || die "no instance-store NVMe found"
  mkfs.xfs -f "$DEV" && mkdir -p "$NV" && mount "$DEV" "$NV" || die "mount $DEV failed"
fi
log "nvme: $(df -h "$NV" | tail -1)"
if [ ! -f "$ST/docker_root" ]; then
  mkdir -p "$NV/docker"
  python3 - <<'PY'
import json, os
p = "/etc/docker/daemon.json"
d = json.load(open(p)) if os.path.exists(p) and os.path.getsize(p) else {}
d["data-root"] = "/opt/dlami/nvme/docker"
json.dump(d, open(p, "w"), indent=2)
PY
  systemctl restart docker || die "docker restart failed"
  stamp docker_root
fi

# 5. ECR login + image pull, in parallel with 6. weights copy.
pull() {
  [ -f "$ST/pulled" ] && return 0
  local t0=$(date +%s) reg=${IMAGE%%/*}
  aws ecr get-login-password | docker login -u AWS --password-stdin "$reg" >/dev/null || return 1
  docker pull -q "$IMAGE" || return 1
  echo $(( $(date +%s) - t0 )) > "$ST/pull_s"; stamp pulled; log "image pulled in $(cat $ST/pull_s) s"
}
weights() {
  [ -f "$ST/weights" ] && return 0
  local t0=$(date +%s)
  mkdir -p "$NV/model"
  # The CRT client saturates the NIC far better than the classic one (~120 MB/s measured
  # with 64 concurrent requests on g7e.4xlarge); older CLIs ignore these keys.
  aws configure set default.s3.preferred_transfer_client crt
  aws configure set default.s3.target_bandwidth 40Gb/s
  aws configure set default.s3.max_concurrent_requests 64
  aws configure set default.s3.multipart_chunksize 64MB
  aws s3 cp --recursive --only-show-errors "$WEIGHTS" "$NV/model/" || return 1
  echo $(( $(date +%s) - t0 )) > "$ST/weights_s"; stamp weights
  log "weights copied in $(cat $ST/weights_s) s ($(du -sh $NV/model | cut -f1))"
}
hb booting "pulling image + copying weights"
( while sleep 60; do hb booting "pulling image + copying weights"; done ) & HBP=$!
pull > "$ST/pull.log" 2>&1 & PP=$!
weights > "$ST/weights.log" 2>&1 & WP=$!
wait $PP || die "image pull failed: $(tail -3 $ST/pull.log)"
wait $WP || die "weight copy failed: $(tail -3 $ST/weights.log)"
kill $HBP 2>/dev/null
cat "$ST/pull.log" "$ST/weights.log"

# 6. Start the adapter container (it launches sglang itself). Port bound to loopback only.
if ! docker ps --format '{{.Names}}' | grep -qx h3; then
  docker rm -f h3 >/dev/null 2>&1 || true
  ENVS=$(python3 -c "import json;print(' '.join('-e %s=%s'%kv for kv in json.load(open('$CFG'))['env'].items()))")
  # shellcheck disable=SC2086
  docker run -d --name h3 --gpus all --ipc host \
    -v "$NV/model:$MOUNT:ro" -p "127.0.0.1:$PORT:8080" $ENVS "$IMAGE" || die "docker run failed"
  stamp container_started; log "container started"
fi
hb loading "sglang loading weights"

# 7. Hand over to the poll loop (waits for /ping, then serves the queue). If it ever
#    crashes, power off rather than leave a GPU running with nobody serving the queue.
python3 /opt/evp/worker.py || die "worker.py exited rc=$?"

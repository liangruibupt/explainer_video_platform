# Digital human (数字人) worker image: InfiniteTalk + LongCat-Video-Avatar-1.5 behind one
# HTTP adapter on 8080 (/ping, /invocations). Weights are NOT in the image: the adapter
# fetches the requested model's pinned S3 prefix to /weights (instance NVMe) on first use.
# CUDA 12.8 so the same image runs on L40S (g6e) and RTX PRO 6000 Blackwell (g7e).
# Ubuntu 24.04 for its system Python 3.12 (pillow >= 13 needs >= 3.11); everything goes into
# /opt/venv, so PEP 668 does not apply.
FROM nvidia/cuda:12.8.1-cudnn-devel-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates git ffmpeg libsndfile1 \
      python3 python3-venv python3-dev build-essential \
      && rm -rf /var/lib/apt/lists/* && python3 -m venv /opt/venv \
      && /opt/venv/bin/python -c "import sys; assert sys.version_info[:2] == (3, 12), sys.version"
ENV PATH=/opt/venv/bin:$PATH

# s5cmd: fast S3 -> NVMe weight fetch with the instance role.
RUN curl -fsSL https://github.com/peak/s5cmd/releases/download/v2.3.0/s5cmd_2.3.0_Linux-64bit.tar.gz \
      | tar -xz -C /usr/local/bin s5cmd

RUN pip install -U pip==25.2 && pip install --index-url https://download.pytorch.org/whl/cu128 \
      torch==2.7.1 torchvision==0.22.1 torchaudio==2.7.1 xformers==0.0.31.post1
# flash-attn <= 2.8.0 is what xformers 0.0.31 accepts.
RUN pip install https://github.com/Dao-AILab/flash-attention/releases/download/v2.8.0.post2/flash_attn-2.8.0.post2+cu12torch2.7cxx11abiTRUE-cp312-cp312-linux_x86_64.whl
COPY requirements.txt /tmp/requirements.txt
# diffq 0.2.4 (pulled in by audio-separator) has no cp312 wheel: pip builds its small Cython
# extension from the sdist (build-essential + python3-dev above). moviepy 1.0.3 is sdist-only.
RUN pip install -r /tmp/requirements.txt

# Model code at pinned commits.
ARG IT_SHA=50aa0a94184315407a991ae804d9b58d6d311ba8
ARG LC_SHA=6b3f4b8582a8bc3f20f795735f5383716c4ba794
RUN git clone https://github.com/MeiGen-AI/InfiniteTalk.git /opt/infinitetalk && git -C /opt/infinitetalk checkout $IT_SHA \
 && git clone https://github.com/meituan-longcat/LongCat-Video.git /opt/longcat && git -C /opt/longcat checkout $LC_SHA
# Python 3.11+ patch: wan/multitalk.py imports inspect.ArgSpec (removed in 3.11) and never
# uses it. Drop that one line (idempotent), then fail the build if a removed inspect API is
# still referenced anywhere in either codebase.
RUN sed -i '/^from inspect import ArgSpec$/d' /opt/infinitetalk/wan/multitalk.py \
 && ! grep -rnE 'ArgSpec|getargspec|formatargspec' --include=*.py /opt/infinitetalk /opt/longcat

COPY serve.py it_runner.py lc_runner.py common.py check_imports.py /app/
WORKDIR /app
# Import check at build time (CPU only, libcuda stub): catches dependency breakage before a
# GPU is paid for. The model code imports from its repo root (src/, wan/, longcat_video/).
ENV PYTHONPATH=/app:/opt/infinitetalk:/opt/longcat
RUN mkdir -p /tmp/stub && ln -s /usr/local/cuda/lib64/stubs/libcuda.so /tmp/stub/libcuda.so.1 \
 && LD_LIBRARY_PATH=/tmp/stub:/usr/local/cuda/lib64/stubs python check_imports.py && rm -rf /tmp/stub
EXPOSE 8080
ENTRYPOINT ["uvicorn", "serve:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]

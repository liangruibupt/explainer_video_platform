# YuE2-3B music inference container for SageMaker. Weights are NOT in the image:
# SageMaker downloads them from S3 to /opt/ml/model/{model,vae} (ModelDataSource, uncompressed).
# CUDA 12.8 so the same image runs on L4 (g6), A10G (g5) and L40S (g6e).
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends software-properties-common \
      && add-apt-repository -y ppa:deadsnakes/ppa \
      && apt-get update && apt-get install -y --no-install-recommends python3.11 python3.11-venv \
           libsndfile1 ffmpeg \
      && rm -rf /var/lib/apt/lists/* \
      && python3.11 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

# The official inference wheel (from the m-a-p/YuE2-3B repo) and its exact pins.
COPY yue2_infer-0.1.5-py3-none-any.whl requirements.txt /tmp/
RUN pip install --index-url https://download.pytorch.org/whl/cu128 torch==2.10.0 \
      && pip install -r /tmp/requirements.txt \
      && pip install --no-deps /tmp/yue2_infer-0.1.5-py3-none-any.whl

COPY serve.py /app/serve.py
WORKDIR /app
EXPOSE 8080
# SageMaker starts the container as `docker run IMAGE serve`; the script ignores that argument.
RUN printf '#!/bin/sh\nexec uvicorn serve:app --host 0.0.0.0 --port 8080 --workers 1\n' > /app/start \
      && chmod +x /app/start
ENTRYPOINT ["/app/start"]

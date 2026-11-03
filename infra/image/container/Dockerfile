# Qwen-Image 2.1 inference container for SageMaker. Weights are NOT in the image:
# SageMaker downloads them from S3 to /opt/ml/model (ModelDataSource, uncompressed).
# CUDA 12.8 so the same image runs on L40S (g6e) and the Blackwell RTX PRO GPUs (g7, g7e).
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends python3.11 python3.11-venv git \
      && rm -rf /var/lib/apt/lists/* \
      && python3.11 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

COPY requirements.txt /tmp/requirements.txt
RUN pip install --index-url https://download.pytorch.org/whl/cu128 torch==2.8.0 \
      && pip install -r /tmp/requirements.txt

COPY serve.py /app/serve.py
WORKDIR /app
EXPOSE 8080
# SageMaker starts the container as `docker run IMAGE serve`; the script ignores that argument.
RUN printf '#!/bin/sh\nexec uvicorn serve:app --host 0.0.0.0 --port 8080 --workers 1\n' > /app/start \
      && chmod +x /app/start
ENTRYPOINT ["/app/start"]

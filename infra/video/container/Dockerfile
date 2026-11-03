# MiniMax-H3 video inference container for SageMaker async.
#
# FROM the project's base image, which already bakes in: the sglang H3 source patches,
# SageAttention built for sm_120 (Blackwell g7e), and the NVFP4 python fixes. Weights
# are NOT in the image -- SageMaker downloads them from S3 to /opt/ml/model
# (ModelDataSource, uncompressed): the FL2VA partition under MiniMax-H3/ and the NVFP4
# Turbo transformer under nvfp4/.
#
# This image only adds a small FastAPI adapter (serve.py) that launches `sglang serve`
# on 127.0.0.1:8000 and exposes SageMaker's /ping and /invocations on :8080.
FROM 747411437379.dkr.ecr.ap-south-1.amazonaws.com/evp/minimax-h3:h3-c0b6474

ENV PYTHONUNBUFFERED=1

# The adapter talks HTTP to sglang and needs only fastapi + uvicorn on top of the base
# image's Python environment. --no-deps keeps it from touching torch/sglang/etc.
RUN pip install --no-cache-dir fastapi==0.162.0 uvicorn==0.34.0 \
    || pip install --no-cache-dir fastapi uvicorn

COPY serve.py /app/serve.py
WORKDIR /app
EXPOSE 8080

# SageMaker starts the container as `docker run IMAGE serve`; the script ignores that
# argument and always runs the adapter on :8080.
RUN printf '#!/bin/sh\nexec uvicorn serve:app --host 0.0.0.0 --port 8080 --workers 1\n' \
      > /app/start && chmod +x /app/start
ENTRYPOINT ["/app/start"]

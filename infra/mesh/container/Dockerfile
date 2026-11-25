# Image-to-3D (图生 3D) worker image: Hunyuan3D-2 shape (2mini / 2.0) + optional Paint texture,
# behind one HTTP adapter on 8080 (/ping, /invocations). Weights are NOT in the image: the
# worker bootstrap copies s3://.../weights/hunyuan3d-2/<sha>/ to NVMe and mounts it read-only
# at /opt/ml/model (HY3DGEN_MODELS). CUDA 12.8 so one image runs on L40S (g6e, sm_89) and
# Blackwell RTX PRO (g7 / g7e, sm_120).
FROM nvidia/cuda:12.8.1-cudnn-devel-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates git \
      python3 python3-venv python3-dev build-essential ninja-build libgl1 libglib2.0-0 libegl1 \
      && rm -rf /var/lib/apt/lists/* && python3 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

RUN pip install -U pip==25.2 && pip install --index-url https://download.pytorch.org/whl/cu128 \
      torch==2.7.1 torchvision==0.22.1
COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt

# Model code at a pinned commit. Not pip-installed (its setup.py floats diffusers/transformers
# and pulls gradio); the package is imported from the checkout via PYTHONPATH.
ARG HY_SHA=f8db63096c8282cb27354314d896feba5ba6ff8a
RUN git clone https://github.com/Tencent-Hunyuan/Hunyuan3D-2.git /opt/hunyuan3d \
 && git -C /opt/hunyuan3d checkout $HY_SHA

# Paint's two native extensions. No GPU on the builder, so the arch list is explicit:
# 8.9 = L40S, 12.0 = RTX PRO 4500 / 6000 Blackwell. mesh_processor is built in place: the
# renderer imports it relatively and would otherwise fall back to the slow pure-python copy.
ENV TORCH_CUDA_ARCH_LIST="8.9;12.0" MAX_JOBS=8
RUN cd /opt/hunyuan3d/hy3dgen/texgen/custom_rasterizer && pip install --no-build-isolation . \
 && cd /opt/hunyuan3d/hy3dgen/texgen/differentiable_renderer && python setup.py build_ext --inplace \
 && ls /opt/hunyuan3d/hy3dgen/texgen/differentiable_renderer/mesh_processor*.so

# rembg's u2net model (background removal), baked in so the worker needs no GitHub access.
ENV U2NET_HOME=/opt/u2net
RUN mkdir -p $U2NET_HOME && curl -fsSL -o $U2NET_HOME/u2net.onnx \
      https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net.onnx \
 && python -c "import hashlib,sys;h=hashlib.md5(open('$U2NET_HOME/u2net.onnx','rb').read()).hexdigest();print('u2net md5',h);sys.exit(h!='60024c5c889badc19c04ad937298a77b')"

COPY serve.py check_imports.py /app/
WORKDIR /app
ENV PYTHONPATH=/app:/opt/hunyuan3d HY3DGEN_MODELS=/opt/ml/model HF_HUB_DISABLE_SYMLINKS=1 \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
# Import check at build time (CPU only, libcuda stub): catches dependency breakage before a
# GPU is paid for. serve.py re-runs it on the GPU at startup.
RUN mkdir -p /tmp/stub && ln -s /usr/local/cuda/lib64/stubs/libcuda.so /tmp/stub/libcuda.so.1 \
 && LD_LIBRARY_PATH=/tmp/stub:/usr/local/cuda/lib64/stubs python check_imports.py && rm -rf /tmp/stub
EXPOSE 8080
ENTRYPOINT ["uvicorn", "serve:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]

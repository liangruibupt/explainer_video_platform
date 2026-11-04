"""Client for the image module (Qwen-Image 2.1) on the self-managed EC2 GPU backend.

The worker config is infra/gpu_ec2/modules/image.json. generate() queues a job in S3,
starts the worker instance if none is running and waits for the PNGs, so it works the
same whether the worker is warm or must start from 0. The SageMaker async endpoint
(infra/image/deploy.py) is retired for this module and kept only as a fallback.
Credentials come from the machine's AWS profile or role.
"""
from . import gpu_client

MODULE = "image"


def status() -> dict:
    """{'deployed', 'state': ready|starting|idle|failed|unavailable, 'instances', 'desired',
    'instance_type'}."""
    return gpu_client.status(MODULE)


def generate(prompt: str, ratio: str = "16:9", mode: str = "t2i", seed: int = 42,
             steps: int = 40, n: int = 1, timeout: int = 2400, on_wait=None) -> list:
    """Blocking: returns a list of PNG bytes. on_wait(seconds) is called while waiting."""
    res = gpu_client.run(MODULE, {"mode": mode, "prompt": prompt, "ratio": ratio, "seed": seed,
                                  "steps": steps, "n": n}, timeout, on_wait)
    return gpu_client.gpu(MODULE).fetch(res, "images")

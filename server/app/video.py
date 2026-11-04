"""Client for the video module (MiniMax H3) on the self-managed EC2 GPU backend.

Worker config: infra/gpu_ec2/modules/video.json (ap-south-1). Without first_frame the
request is text-to-video+audio (t2va); with a first frame (PNG bytes) it is fl2va.
"""
import base64

from . import gpu_client

MODULE = "video"


def status() -> dict:
    """{'deployed', 'state': ready|starting|idle|failed|unavailable, 'instances', 'desired',
    'instance_type'}."""
    return gpu_client.status(MODULE)


def generate(prompt: str, aspect: str = "16:9", short_edge: int = 768, duration: float = 5.0,
             seed: int = 7, first_frame: bytes = None, timeout: int = 3600,
             on_wait=None) -> bytes:
    """Blocking: returns the MP4 bytes (video with audio)."""
    req = {"mode": "fl2va" if first_frame else "t2va", "prompt": prompt, "aspect": aspect,
           "short_edge": short_edge, "duration": duration, "steps": 8, "seed": seed}
    if first_frame:
        req["first_frame"] = base64.b64encode(first_frame).decode()
    res = gpu_client.run(MODULE, req, timeout, on_wait)
    return gpu_client.gpu(MODULE).fetch(res, "video_b64")[0]

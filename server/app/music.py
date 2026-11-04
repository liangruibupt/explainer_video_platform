"""Client for the music module (YuE2-3B) on the self-managed EC2 GPU backend.

Worker config: infra/gpu_ec2/modules/music.json. Request/response fields follow
infra/music/container/serve.py. Instrumental background music: lyrics with section tags
only ("[Intro]\\n\\n[Instrumental]\\n\\n[Outro]") and a style ending with
"instrumental, no vocals". The SageMaker endpoint (infra/music/deploy.py) is retired for
this module and kept only as a fallback.
"""
from . import gpu_client

MODULE = "music"


def status() -> dict:
    """{'deployed', 'state': ready|starting|idle|failed|unavailable, 'instances', 'desired',
    'instance_type'}."""
    return gpu_client.status(MODULE)


def generate(style: str, lyrics: str, cot: str = "full", seed: int = 831001,
             timeout: int = 3600, on_wait=None) -> dict:
    """Blocking: returns {"flac": bytes, "seconds", "sample_rate", "abc", "format"}.
    "flac" holds the audio bytes; "format" is "mp3" only for very long songs (over 40 MB)."""
    res = gpu_client.run(MODULE, {"style": style, "lyrics": lyrics, "cot": cot, "seed": seed},
                         timeout, on_wait)
    audio = gpu_client.gpu(MODULE).fetch(res, "audio_b64")[0]
    return {"flac": audio, "seconds": res.get("seconds"), "sample_rate": res.get("sample_rate"),
            "abc": res.get("abc"), "format": res.get("format", "flac")}

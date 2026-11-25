"""Build-time import check (CPU only, libcuda stub): catches dependency breakage before a
GPU is paid for. Tries every import and reports all failures at once, then exits 1.

xfuser / yunchang ask for the GPU name and compute capability at import (InfiniteTalk's
`wan` imports them). On the CPU builder that hits the libcuda stub, so such a module is
retried once with those two queries faked; only if it still needs CUDA is it a SKIP (the
container's startup check re-runs everything on the real GPU)."""
import importlib, sys, traceback, types

sys.path[:0] = ["/app", "/opt/infinitetalk", "/opt/longcat"]
sys.modules["kokoro"] = types.SimpleNamespace(KPipeline=None)

MODS = ["flash_attn", "xformers.ops", "pynvml", "xfuser.core.distributed", "optimum.quanto",
        "wan", "wan.configs", "src.audio_analysis.wav2vec2", "wan.distributed.xdit_context_parallel",
        "longcat_video.pipeline_longcat_video_avatar", "longcat_video.modules.quantization",
        "longcat_video.audio_process", "longcat_video.context_parallel.context_parallel_util",
        "audio_separator.separator", "serve", "it_runner", "lc_runner"]


def _stub_error(e):
    return "CUDA driver is a stub" in str(e) or "cudaGetDeviceCount" in str(e)


def _import_fake_device(m):
    import torch
    saved = torch.cuda.get_device_name, torch.cuda.get_device_capability
    torch.cuda.get_device_name = lambda *a, **k: "NVIDIA RTX PRO 6000 Blackwell (build check)"
    torch.cuda.get_device_capability = lambda *a, **k: (12, 0)
    try:
        importlib.import_module(m)
    finally:
        torch.cuda.get_device_name, torch.cuda.get_device_capability = saved


bad = []
for m in MODS:
    try:
        importlib.import_module(m)
        print("ok  ", m, flush=True)
    except Exception as e:  # noqa: BLE001
        if _stub_error(e):
            try:
                _import_fake_device(m)
                print("ok  ", m, "(device queries faked)", flush=True)
                continue
            except Exception as e2:  # noqa: BLE001
                if _stub_error(e2):
                    print("SKIP", m, "(touches CUDA at import; checked on the GPU)", flush=True)
                    continue
                e = e2
        bad.append(m)
        print("FAIL", m, flush=True)
        traceback.print_exception(e, limit=3)
if bad:
    sys.exit(f"import failures: {bad}")
print("imports ok")

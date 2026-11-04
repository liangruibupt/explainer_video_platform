"""Build-time import check (CPU only, libcuda stub): catches dependency breakage before a
GPU is paid for. Tries every import and reports all failures at once, then exits 1."""
import importlib, sys, traceback, types

sys.path[:0] = ["/app", "/opt/infinitetalk", "/opt/longcat"]
sys.modules["kokoro"] = types.SimpleNamespace(KPipeline=None)

MODS = ["flash_attn", "xformers.ops", "pynvml", "xfuser.core.distributed", "optimum.quanto",
        "wan", "wan.configs", "src.audio_analysis.wav2vec2", "wan.distributed.xdit_context_parallel",
        "longcat_video.pipeline_longcat_video_avatar", "longcat_video.modules.quantization",
        "longcat_video.audio_process", "longcat_video.context_parallel.context_parallel_util",
        "audio_separator.separator", "serve", "it_runner", "lc_runner"]
bad = []
for m in MODS:
    try:
        importlib.import_module(m)
        print("ok  ", m, flush=True)
    except Exception as e:  # noqa: BLE001
        if "CUDA driver is a stub" in str(e) or "cudaGetDeviceCount" in str(e):
            print("SKIP", m, "(touches CUDA at import; checked on the GPU)", flush=True)
            continue
        bad.append(m)
        print("FAIL", m, flush=True)
        traceback.print_exc(limit=3)
if bad:
    sys.exit(f"import failures: {bad}")
print("imports ok")

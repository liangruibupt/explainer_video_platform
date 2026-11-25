"""Import check (build time on CPU with a libcuda stub, and again on the GPU at startup):
tries every import the adapter needs and reports all failures at once, then exits 1."""
import importlib, sys, traceback

sys.path[:0] = ["/app", "/opt/hunyuan3d"]
MODS = ["torch", "diffusers", "transformers", "trimesh", "pymeshlab", "xatlas", "pygltflib",
        "omegaconf", "rembg", "onnxruntime", "pynvml", "custom_rasterizer", "custom_rasterizer_kernel",
        "hy3dgen.rembg", "hy3dgen.shapegen", "hy3dgen.texgen",
        "hy3dgen.texgen.differentiable_renderer.mesh_processor", "serve"]
bad = []
for m in MODS:
    try:
        mod = importlib.import_module(m)
        extra = ""
        if m == "hy3dgen.texgen.differentiable_renderer.mesh_processor":
            extra = mod.__file__  # must be the compiled .so, not the pure-python fallback
            if not extra.endswith(".so"):
                raise ImportError(f"mesh_processor is the python fallback: {extra}")
        print("ok  ", m, getattr(mod, "__version__", ""), extra, flush=True)
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

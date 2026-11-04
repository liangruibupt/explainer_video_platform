"""LongCat-Video-Avatar-1.5 runner (meituan-longcat/LongCat-Video), kept resident.

Mirrors run_demo_avatar_single_audio_to_video.py: stage 1 ai2v (93 frames at 25 fps),
then video continuation (generate_avc, 80 new frames each) until the audio is covered.
`--model_type avatar-v1.5 --use_distill` is fixed (8 steps, cfg 1.0), so `fast` changes
nothing here. `int8` picks the INT8 DiT (`--use_int8`); default bf16 on a >=60 GB GPU,
INT8 below that (L40S 48 GB).
"""
import math, os, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, "/opt/longcat")

import numpy as np
import torch

from common import log

NUM_FRAMES, COND_FRAMES, FPS = 93, 13, 25
NEG = ("Close-up, Bright tones, overexposed, static, blurred details, subtitles, style, works, paintings, "
       "images, static, overall gray, worst quality, low quality, JPEG compression residue, ugly, incomplete, "
       "extra fingers, poorly drawn hands, poorly drawn faces, deformed, disfigured, misshapen limbs, fused "
       "fingers, still picture, messy background, three legs, many people in the background, walking backwards")


class LongCat:
    name = "longcat"

    def __init__(self, wdir: Path):
        self.w = wdir
        self.pipe, self.variant, self.sep = None, None, None

    def _load(self, int8):
        variant = "int8" if int8 else "bf16"
        if self.variant == variant:
            return 0.0
        from transformers import AutoTokenizer, UMT5EncoderModel
        from longcat_video.pipeline_longcat_video_avatar import LongCatVideoAvatarPipeline
        from longcat_video.modules.scheduling_flow_match_euler_discrete import FlowMatchEulerDiscreteScheduler
        from longcat_video.modules.autoencoder_kl_wan import AutoencoderKLWan
        from longcat_video.modules.avatar.longcat_video_dit_avatar import LongCatVideoAvatarTransformer3DModel
        from longcat_video.modules.quantization import load_quantized_dit
        from longcat_video.context_parallel import context_parallel_util
        from longcat_video.audio_process import get_audio_encoder, get_audio_feature_extractor
        from audio_separator.separator import Separator
        t0 = time.time()
        self.unload()
        context_parallel_util.init_context_parallel(context_parallel_size=1, global_rank=0, world_size=1)
        split = context_parallel_util.get_optimal_split(1)
        base, av = str(self.w / "LongCat-Video"), str(self.w / "LongCat-Video-Avatar-1.5")
        bf = torch.bfloat16
        tok = AutoTokenizer.from_pretrained(base, subfolder="tokenizer", torch_dtype=bf)
        te = UMT5EncoderModel.from_pretrained(base, subfolder="text_encoder", torch_dtype=bf)
        vae = AutoencoderKLWan.from_pretrained(base, subfolder="vae", torch_dtype=bf)
        sch = FlowMatchEulerDiscreteScheduler.from_pretrained(av, subfolder="scheduler", torch_dtype=bf)
        if int8:
            dit = load_quantized_dit(av, subfolder="base_model_int8", cp_split_hw=split)
        else:
            dit = LongCatVideoAvatarTransformer3DModel.from_pretrained(av, subfolder="base_model",
                                                                       cp_split_hw=split, torch_dtype=bf)
        dit.load_lora(os.path.join(av, "lora", "dmd_lora.safetensors"), "dmd", multiplier=1.0,
                      lora_network_dim=128, lora_network_alpha=64)
        dit.enable_loras(["dmd"])
        wh = os.path.join(av, "whisper-large-v3")
        enc = get_audio_encoder(wh, "avatar-v1.5").to(0)
        fe = get_audio_feature_extractor(wh, "avatar-v1.5")
        self.pipe = LongCatVideoAvatarPipeline(tokenizer=tok, text_encoder=te, vae=vae, scheduler=sch, dit=dit,
                                               audio_encoder=enc, audio_feature_extractor=fe,
                                               model_type="avatar-v1.5")
        self.pipe.to(0)
        sepdir = Path("/tmp/lc_sep"); sepdir.mkdir(exist_ok=True)
        self.sep = Separator(output_dir=str(sepdir), output_single_stem="vocals",
                             model_file_dir=os.path.join(av, "vocal_separator"))
        self.sep.load_model("Kim_Vocal_2.onnx")
        self.sepdir = sepdir
        self.variant = variant
        dt = time.time() - t0
        log("longcat loaded", variant, f"{dt:.0f} s")
        return dt

    def unload(self):
        self.pipe, self.variant, self.sep = None, None, None
        import gc; gc.collect(); torch.cuda.empty_cache()

    def generate(self, image_path, video_path, wav16k, prompt, size, fast, seed, audio_cfg, text_cfg, tmp,
                 int8=None, gpu_gb=96.0):
        import librosa
        from diffusers.utils import load_image
        from PIL import Image
        if video_path:
            # Reference video: LongCat's ai2v takes an image, so condition on the first frame.
            image_path = str(Path(tmp) / "ref_frame0.png")
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", video_path, "-frames:v", "1",
                            image_path], check=True)
        load_s = self._load(int8=(gpu_gb < 60) if int8 is None else bool(int8))
        # Vocal extraction (the reference pipeline always runs it; clean TTS passes through).
        outs = self.sep.separate(str(wav16k))
        vocal = self.sepdir / outs[0] if outs else Path(wav16k)
        if not vocal.exists():
            vocal = Path(outs[0]) if outs else Path(wav16k)
        speech, sr = librosa.load(str(vocal), sr=16000)
        dur = len(speech) / sr
        n_seg = max(1, math.ceil((dur * FPS - NUM_FRAMES) / (NUM_FRAMES - COND_FRAMES)) + 1)
        gen_dur = NUM_FRAMES / FPS + (n_seg - 1) * (NUM_FRAMES - COND_FRAMES) / FPS
        pad = math.ceil((gen_dur - dur) * sr)
        if pad > 0:
            speech = np.append(speech, np.zeros(pad, dtype=speech.dtype))
        full = self.pipe.get_audio_embedding(speech, fps=FPS, device=0, sample_rate=sr, model_type="avatar-v1.5")
        if torch.isnan(full).any():
            raise ValueError("broken audio embedding (nan)")
        idx = torch.arange(5) - 2

        def window(start):
            c = torch.arange(start, start + NUM_FRAMES).unsqueeze(1) + idx.unsqueeze(0)
            return full[torch.clamp(c, 0, full.shape[0] - 1)][None].to(0)

        g = torch.Generator(device=0); g.manual_seed(seed)
        res = "720p" if size == "720" else "480p"
        common = dict(prompt=prompt, negative_prompt=NEG, num_inference_steps=8, text_guidance_scale=1.0,
                      audio_guidance_scale=1.0, generator=g, output_type="both", use_distill=True)
        out, latent = self.pipe.generate_ai2v(image=load_image(str(image_path)), resolution=res,
                                              num_frames=NUM_FRAMES, audio_emb=window(0), **common)
        frames = list((out[0] * 255).astype(np.uint8))
        cur = [Image.fromarray(f) for f in frames]
        ref = latent[:, :, :1].clone()
        w, h = cur[0].size
        start = 0
        for s in range(1, n_seg):
            start += NUM_FRAMES - COND_FRAMES
            log(f"longcat segment {s + 1}/{n_seg}")
            out, latent = self.pipe.generate_avc(
                video=cur, video_latent=latent, height=h, width=w, num_frames=NUM_FRAMES,
                num_cond_frames=COND_FRAMES, use_kv_cache=True, offload_kv_cache=False, enhance_hf=False,
                audio_emb=window(start), ref_latent=ref, ref_img_index=10, mask_frame_range=3, **common)
            new = list((out[0] * 255).astype(np.uint8))
            frames.extend(new[COND_FRAMES:])
            cur = [Image.fromarray(f) for f in new]
        return {"frames": frames, "fps": FPS, "audio": str(wav16k), "load_s": load_s, "segments": n_seg,
                "steps": 8, "dit": self.variant}

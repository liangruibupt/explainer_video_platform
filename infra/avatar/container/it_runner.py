"""InfiniteTalk runner (MeiGen-AI/InfiniteTalk on Wan2.1-I2V-14B-480P), kept resident.

Mirrors generate_infinitetalk.py (streaming mode, single person) without the CLI and
without Kokoro TTS. Two resident variants are not affordable (14B each), so the
lightx2v LoRA is merged into the weights for the fast path and the base model is
reloaded when a request asks for the other variant ("full", 40 steps).
"""
import os, sys, time, types
from pathlib import Path

sys.path.insert(0, "/opt/infinitetalk")
sys.modules.setdefault("kokoro", types.SimpleNamespace(KPipeline=None))  # TTS unused

import numpy as np
import soundfile as sf
import torch

from common import log

LORA = "Wan21_T2V_14B_lightx2v_cfg_step_distill_lora_rank32.safetensors"


class InfiniteTalk:
    name = "infinitetalk"

    def __init__(self, wdir: Path):
        self.w = wdir
        self.pipe, self.variant = None, None
        self.feat = self.enc = None

    def _load(self, fast):
        import wan
        from wan.configs import WAN_CONFIGS
        from transformers import Wav2Vec2FeatureExtractor
        from src.audio_analysis.wav2vec2 import Wav2Vec2Model
        variant = "fast" if fast else "full"
        if self.variant == variant:
            return 0.0
        t0 = time.time()
        self.unload_pipe()
        cfg = WAN_CONFIGS["infinitetalk-14B"]
        # 96 GB (g7e): everything resident on the GPU. 48 GB (g6e): DiT starts on CPU and
        # T5 stays on CPU; generate() then offloads between stages.
        small = torch.cuda.get_device_properties(0).total_memory < 60 * 2**30
        self.pipe = wan.InfiniteTalkPipeline(
            config=cfg, checkpoint_dir=str(self.w / "Wan2.1-I2V-14B-480P"), quant_dir=None,
            device_id=0, rank=0, t5_fsdp=False, dit_fsdp=False, use_usp=False, t5_cpu=small,
            init_on_cpu=small,
            lora_dir=[str(self.w / "lora" / LORA)] if fast else None, lora_scales=[1.0],
            quant=None, dit_path=None,
            infinitetalk_dir=str(self.w / "InfiniteTalk" / "single" / "infinitetalk.safetensors"))
        if self.feat is None:
            wv = str(self.w / "chinese-wav2vec2-base")
            self.enc = Wav2Vec2Model.from_pretrained(wv, local_files_only=True).to("cpu")
            self.enc.feature_extractor._freeze_parameters()
            self.feat = Wav2Vec2FeatureExtractor.from_pretrained(wv, local_files_only=True)
        self.variant = variant
        dt = time.time() - t0
        log("infinitetalk loaded", variant, f"{dt:.0f} s")
        return dt

    def unload_pipe(self):
        self.pipe, self.variant = None, None
        import gc; gc.collect(); torch.cuda.empty_cache()

    def unload(self):
        self.unload_pipe()
        self.feat = self.enc = None

    def _embed(self, wav16k_path, tmp):
        import librosa, pyloudnorm as pyln
        from einops import rearrange
        speech, sr = librosa.load(wav16k_path, sr=16000)
        loud = pyln.Meter(sr).integrated_loudness(speech)
        if abs(loud) <= 100:
            speech = pyln.normalize.loudness(speech, loud, -23)
        vlen = len(speech) / sr * 25
        x = torch.from_numpy(np.squeeze(self.feat(speech, sampling_rate=sr).input_values)).float()[None]
        with torch.no_grad():
            e = self.enc(x, seq_len=int(vlen), output_hidden_states=True)
        emb = rearrange(torch.stack(e.hidden_states[1:], dim=1).squeeze(0), "b s d -> s b d").cpu()
        p = tmp / "emb.pt"; torch.save(emb, p)
        a = tmp / "speech.wav"; sf.write(a, speech, 16000)
        return str(p), str(a), emb.shape[0]

    def generate(self, image_path, video_path, wav16k, prompt, size, fast, seed, audio_cfg, text_cfg, tmp,
                 int8=None, gpu_gb=96.0):
        load_s = self._load(fast)
        emb, speech, n_audio = self._embed(wav16k, tmp)
        size_b = "infinitetalk-720" if size == "720" else "infinitetalk-480"
        steps = 4 if fast else 40
        shift = 2.0 if fast else (11.0 if size == "720" else 7.0)
        text_cfg = text_cfg if text_cfg is not None else (1.0 if fast else 5.0)
        audio_cfg = audio_cfg if audio_cfg is not None else (2.0 if fast else 4.0)
        extra = types.SimpleNamespace(use_teacache=False, teacache_thresh=0.2, size=size_b,
                                      use_apg=False, apg_momentum=-0.75, apg_norm_threshold=55)
        clip = {"prompt": prompt, "cond_video": video_path or image_path,
                "cond_audio": {"person1": emb}, "video_audio": speech}
        video = self.pipe.generate_infinitetalk(
            clip, size_buckget=size_b, motion_frame=9, frame_num=81, shift=shift,
            sampling_steps=steps, text_guide_scale=text_cfg, audio_guide_scale=audio_cfg,
            seed=seed, offload_model=gpu_gb < 60, max_frames_num=max(n_audio, 81) + 81,
            color_correction_strength=1.0, extra_args=extra)
        # C T H W in [-1, 1] -> T H W C uint8
        v = ((video.float().clamp(-1, 1) + 1) * 127.5).to(torch.uint8).permute(1, 2, 3, 0).cpu().numpy()
        return {"frames": v, "fps": 25, "audio": speech, "load_s": load_s,
                "steps": steps, "shift": shift, "text_cfg": text_cfg, "audio_cfg": audio_cfg}

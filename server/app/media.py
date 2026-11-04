"""Stand-alone video and music generations for the AI 视频 and 音乐 pages.

Stored like images.py: data/videos/<id>/ and data/music/<id>/, each with req.json and
state.json. A background thread waits for the GPU worker (which may start from 0) and
writes 0.mp4 (video) or 0.flac / 0.mp3 + score.abc (music) when it is done.
"""
import json
import threading
import time

from .pipeline import DATA


class Store:
    def __init__(self, folder: str, generate):
        self.root = DATA / folder
        self.generate = generate  # (req, on_wait) -> list of (file name, bytes), extra state

    def _state(self, d, **kw):
        p = d / "state.json"
        s = json.loads(p.read_text()) if p.exists() else {}
        s.update(kw, updated=time.time())
        p.write_text(json.dumps(s, ensure_ascii=False))
        return s

    def create(self, item_id: str, req: dict) -> dict:
        d = self.root / item_id
        d.mkdir(parents=True, exist_ok=False)
        (d / "req.json").write_text(json.dumps(req, ensure_ascii=False))
        s = self._state(d, id=item_id, status="queued", created=time.time(), files=[],
                        error=None, **req)
        threading.Thread(target=self._run, args=(item_id,), daemon=True).start()
        return s

    def _run(self, item_id: str) -> None:
        d = self.root / item_id
        req = json.loads((d / "req.json").read_text())
        self._state(d, status="running")
        try:
            t0 = time.time()
            files, extra = self.generate(req, lambda sec: self._state(d, waited=sec))
            for name, data in files:
                (d / name).write_bytes(data)
            self._state(d, status="done", files=[n for n, _ in files if not n.endswith(".abc")],
                        seconds=round(time.time() - t0), **extra)
        except Exception as e:  # noqa: BLE001 - shown on the page
            self._state(d, status="failed", error=str(e)[:500])

    def get(self, item_id: str) -> dict:
        p = self.root / item_id / "state.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def list_all(self, limit: int = 40) -> list:
        if not self.root.exists():
            return []
        out = [json.loads(p.read_text()) for p in self.root.glob("*/state.json")]
        return sorted(out, key=lambda s: s.get("created", 0), reverse=True)[:limit]


def _video(req, on_wait):
    from . import video
    mp4 = video.generate(req["prompt"], aspect=req["aspect"], short_edge=768, duration=5.0,
                         seed=req["seed"], on_wait=on_wait)
    return [("0.mp4", mp4)], {}


TAIL = "ambient, no drums, instrumental, no vocals"
INSTRUMENTAL = "[Intro]\n\n[Instrumental]\n\n[Outro]"


def _style_with_arrangement(req):
    """Style sent to YuE2: the user's style plus English tags made from the arrangement note.
    For instrumentals the no-vocals tail stays at the very end."""
    style, note = req["style"].strip(), (req.get("arrangement") or "").strip()
    if not note:
        return style, ""
    from . import llm
    tags = llm.arrangement_tags(note)
    instrumental = req["lyrics"].strip() == INSTRUMENTAL or style.lower().endswith(TAIL)
    base = style[: -len(TAIL)].rstrip(" ,") if style.lower().endswith(TAIL) else style
    final = f"{base}, {tags}" if base else tags
    if instrumental:
        final = f"{final}, {TAIL}"
    return final, tags


def _music(req, on_wait):
    from . import music
    style, tags = _style_with_arrangement(req)
    r = music.generate(style, req["lyrics"], cot=req["cot"], seed=req["seed"],
                       on_wait=on_wait)
    ext = "mp3" if r.get("format") == "mp3" else "flac"
    files = [(f"0.{ext}", r["flac"])]
    if r.get("abc"):
        files.append(("score.abc", str(r["abc"]).encode()))
    return files, {"audio_seconds": r.get("seconds"), "sent_style": style,
                   "arrangement_tags": tags}


videos = Store("videos", _video)
musics = Store("music", _music)

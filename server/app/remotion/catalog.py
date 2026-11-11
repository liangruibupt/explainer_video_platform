"""Index of the video-shotcraft shot cards (docs/REMOTION_CONTRACT.md §2, part A).

A card is references/shots/<category>/<card>.md (front matter: name, 一句话, 适用, 时长, 能量,
标签) plus its Remotion demo, demos/<category>/<card>/*.tsx. Card id = "<category>/<card>".
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

TOOLS = Path(os.environ.get("EVP_TOOLS", Path.home() / ".cache" / "explainer-video"))
SHOTCRAFT = Path(os.environ.get("EVP_SHOTCRAFT", TOOLS / "shotcraft"))

# Demo imports that the generated project serves from src/lib/ (copied shotcraft files).
LIB_FIXTURES = {"Polish", "Look", "Motion", "Brand"}
# Demo imports that mean "this card is built around fake product UI / textures / extra packages".
_UI_IMPORT = re.compile(r"_fixtures/(Fixtures|PageCam2D)|_textures/|assets/lib/|@remotion/")


@dataclass
class Card:
    id: str
    category: str
    name: str
    summary: str
    use: str
    duration: str
    seconds: float | None
    energy: str
    md: Path
    demos: list[Path] = field(default_factory=list)
    needs_ui: bool = False

    def line(self) -> str:
        """One compact catalog line for the card-picking prompt."""
        flag = " [UI-mock]" if self.needs_ui else ""
        return f"{self.id}{flag} | {self.summary} | 适用: {self.use} | {self.duration}"


def parse_front_matter(text: str) -> dict:
    """The `---` block at the top of a card: `key: value` lines (ASCII or full-width colon)."""
    m = re.match(r"\s*---\s*\n(.*?)\n---", text, re.S)
    out = {}
    for ln in (m.group(1).splitlines() if m else []):
        kv = re.split(r"[:：]", ln, maxsplit=1)
        if len(kv) == 2:
            out[kv[0].strip()] = kv[1].strip()
    return out


def seconds_of(duration: str) -> float | None:
    """First plausible length in seconds from a 时长 string ("4–5s", "约 7.5s（225f）", "120f")."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:[–\-~]\s*\d+(?:\.\d+)?\s*)?s", duration)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+)\s*f", duration)
    return round(int(m.group(1)) / 30, 1) if m else None


def _card(md: Path, root: Path) -> Card:
    cat, name = md.parent.name, md.stem
    fm = parse_front_matter(md.read_text(encoding="utf-8"))
    demo_dir = root / "demos" / cat / name
    demos = sorted(demo_dir.glob("*.tsx")) if demo_dir.is_dir() else []
    src = "".join(p.read_text(encoding="utf-8", errors="replace") for p in demos)
    dur = fm.get("时长", "")
    return Card(id=f"{cat}/{name}", category=cat, name=fm.get("name", name),
                summary=fm.get("一句话", ""), use=fm.get("适用", ""), duration=dur,
                seconds=seconds_of(dur), energy=fm.get("能量", ""), md=md, demos=demos,
                needs_ui=bool(_UI_IMPORT.search(src)))


@lru_cache(maxsize=4)
def _load(root: str) -> tuple:
    r = Path(root)
    mds = sorted((r / "references" / "shots").glob("*/*.md"))
    return tuple(_card(p, r) for p in mds)


def cards(root: Path | None = None) -> list[Card]:
    """Every card that has a Remotion demo, sorted by id (cached per checkout)."""
    return [c for c in _load(str(root or SHOTCRAFT)) if c.demos]


def by_id(root: Path | None = None) -> dict[str, Card]:
    return {c.id: c for c in cards(root)}


def compact(root: Path | None = None) -> str:
    return "\n".join(c.line() for c in cards(root))


def demo_source(card: Card, limit: int = 24000) -> str:
    """The card's demo .tsx files, concatenated and trimmed to `limit` characters."""
    parts, n = [], 0
    for p in card.demos:
        t = p.read_text(encoding="utf-8", errors="replace")
        head = f"// ===== {p.relative_to(p.parents[2])} =====\n"
        room = limit - n - len(head)
        if room <= 500:
            parts.append(f"// ===== {p.name}: omitted (prompt size) =====\n")
            continue
        if len(t) > room:
            t = t[:room] + "\n// ... (trimmed)\n"
        parts.append(head + t)
        n += len(head) + len(t)
    return "\n".join(parts)

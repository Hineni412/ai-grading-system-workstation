"""Render full-resolution region crops for bridge request directories.

Usage (project runtime):

    runtime\\python\\python.exe -X utf8 agent_bridge\\crop_tiles.py [req_dir ...]

Without arguments, renders crops for every request dir under
``session_4/requests`` that has a ``request.json`` but no ``served.json``.
Crops are written to ``<req_dir>/crops/`` at native scan resolution:

* ``hybrid_major_batch``  -> ``crop_<item>_<part_id>.png`` per target sub-item
* ``objective_paper_recognition`` -> ``page_<page>_strip.png`` plus
  ``q_<qid>_<page>.png`` per question region
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image

BRIDGE_DIR = Path(__file__).resolve().parent / "session_4"
PAPERS_INDEX = BRIDGE_DIR / "papers_index.json"


def _crop(image: Image.Image, box: dict, pad: int = 6) -> Image.Image:
    left = max(0, int(round(box["x"])) - pad)
    top = max(0, int(round(box["y"])) - pad)
    right = min(image.width, int(round(box["x"] + box["w"])) + pad)
    bottom = min(image.height, int(round(box["y"] + box["h"])) + pad)
    if right <= left or bottom <= top:
        return None
    return image.crop((left, top, right, bottom))


def render_request(req_dir: Path, index: dict) -> list[Path]:
    request = json.loads((req_dir / "request.json").read_text(encoding="utf-8"))
    manifest = request.get("manifest") or {}
    out_dir = req_dir / "crops"
    out_dir.mkdir(exist_ok=True)
    produced: list[Path] = []
    mode = manifest.get("mode")

    if mode == "hybrid_major_batch":
        for item in manifest.get("items", []):
            entry = index.get(str(item.get("paper_key"))) or {}
            for si in item.get("sub_items", []):
                if not si.get("is_target"):
                    continue
                page = str(si.get("page") or "front")
                src = entry.get(page)
                if not src:
                    continue
                name = f"crop_{int(item.get('item_index') or 0):02d}_{si.get('part_id')}.png"
                target = out_dir / name
                if target.exists():
                    produced.append(target)
                    continue
                with Image.open(src) as image:
                    tile = _crop(image, si.get("bbox") or {})
                    if tile is not None:
                        tile.save(target, format="PNG")
                        produced.append(target)

    elif mode == "objective_paper_recognition":
        entry = index.get(str(manifest.get("paper_key"))) or {}
        for page_record in manifest.get("pages", []):
            page = str(page_record.get("page") or "front")
            src = entry.get(page)
            if not src:
                continue
            sb = page_record.get("source_bbox") or {}
            cb = page_record.get("composite_bbox") or sb
            scale = (cb.get("w") or sb.get("w") or 1) / float(sb.get("w") or 1)
            with Image.open(src) as image:
                strip = _crop(image, sb, pad=0)
                if strip is not None:
                    target = out_dir / f"page_{page}_strip.png"
                    if not target.exists():
                        strip.save(target, format="PNG")
                    produced.append(target)
                for qr in page_record.get("question_regions", []):
                    b = qr.get("bbox") or {}
                    box = {
                        "x": sb.get("x", 0) + (b.get("x", 0) - cb.get("x", 0)) / scale,
                        "y": sb.get("y", 0) + (b.get("y", 0) - cb.get("y", 0)) / scale,
                        "w": (b.get("w") or 0) / scale,
                        "h": (b.get("h") or 0) / scale,
                    }
                    qid = str(qr.get("question_id") or "?")
                    target = out_dir / f"q_{qid}_{page}.png"
                    if target.exists():
                        produced.append(target)
                        continue
                    tile = _crop(image, box)
                    if tile is not None:
                        tile.save(target, format="PNG")
                        produced.append(target)
    return produced


def main(argv: list[str]) -> int:
    if not PAPERS_INDEX.exists():
        print("papers_index.json missing; run run_session4.py first")
        return 1
    index = json.loads(PAPERS_INDEX.read_text(encoding="utf-8"))
    if argv:
        req_dirs = [Path(arg) for arg in argv]
    else:
        requests_root = BRIDGE_DIR / "requests"
        req_dirs = [
            p
            for p in sorted(requests_root.iterdir())
            if p.is_dir()
            and (p / "request.json").is_file()
            and not (p / "served.json").exists()
        ]
    for req_dir in req_dirs:
        produced = render_request(req_dir, index)
        print(f"{req_dir.name}: {len(produced)} crops")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

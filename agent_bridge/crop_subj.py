# -*- coding: utf-8 -*-
"""Crop a pixel band out of a subjective atlas for close inspection.

usage: crop_subj.py <request_dir> <py0> <py1> [px0 px1] [tag]
Saves to <request_dir>/zoom_<tag>.png
"""
import sys
from pathlib import Path

from PIL import Image


def main() -> None:
    req_dir = Path(sys.argv[1])
    y0, y1 = int(sys.argv[2]), int(sys.argv[3])
    x0 = int(sys.argv[4]) if len(sys.argv) > 4 else 0
    x1 = int(sys.argv[5]) if len(sys.argv) > 5 else 0
    tag = sys.argv[6] if len(sys.argv) > 6 else f"{y0}_{y1}"

    atlas = Image.open(req_dir / "atlas_0.jpg")
    W, H = atlas.size
    if not x1:
        x1 = W
    crop = atlas.crop((x0, y0, x1, min(y1, H)))
    scale = max(1, int(1500 / max(1, crop.width)))
    if scale > 1:
        crop = crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)
    out = req_dir / f"zoom_{tag}.png"
    crop.save(out)
    print(out, atlas.size, "->", crop.size)


if __name__ == "__main__":
    main()

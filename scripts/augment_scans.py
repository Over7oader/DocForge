"""Synthetic scans from digital train PDFs: a fresh OCR+LLM comparison set with unchanged gold.

Usage: python -m scripts.augment_scans --source runs/ensemble-ownership-14b --out runs/aug-scans --count 45

Each selected digital PDF is rendered (first OCR_MAX_PAGES pages) and degraded like a scan:
small rotation, blur, noise, contrast change and JPEG compression, with a fixed seed. No values
are edited, so the original gold applies. Use it to compare OCR/LLM configurations, never to tune
rules or prompts (the source documents are train data).
"""

import argparse
import io
import json
import random
import shutil
from pathlib import Path

import cv2
import numpy as np
import pypdfium2 as pdfium
from PIL import Image

from doxforge import settings

_BLUR_SHARE = 0.7  # część stron lekko rozmyta (skaner/aparat), reszta ostra


def _degrade(page: np.ndarray, rng: random.Random, *, hard: bool = False) -> Image.Image:
    gray = cv2.cvtColor(page, cv2.COLOR_RGB2GRAY)
    if hard:  # gorszy skaner: 150 dpi przeskalowane z powrotem do rozdzielczości OCR
        small = cv2.resize(gray, None, fx=0.75, fy=0.75, interpolation=cv2.INTER_AREA)
        gray = cv2.resize(small, (gray.shape[1], gray.shape[0]), interpolation=cv2.INTER_LINEAR)
    height, width = gray.shape
    angle = rng.uniform(-3.0, 3.0) if hard else rng.uniform(-2.0, 2.0)
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
    gray = cv2.warpAffine(gray, matrix, (width, height), borderValue=255)
    if rng.random() < _BLUR_SHARE:
        gray = cv2.GaussianBlur(gray, (3, 3), rng.uniform(0.6, 1.3) if hard else rng.uniform(0.4, 1.0))
    contrast, brightness = rng.uniform(0.75, 1.0), rng.uniform(-10, 25)
    noisy = gray.astype(np.float32) * contrast + brightness
    sigma = rng.uniform(8, 18) if hard else rng.uniform(4, 12)
    noisy += np.random.default_rng(rng.randrange(2**32)).normal(0, sigma, gray.shape)
    image = Image.fromarray(np.clip(noisy, 0, 255).astype(np.uint8))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=rng.randint(25, 50) if hard else rng.randint(35, 70))
    return Image.open(io.BytesIO(buffer.getvalue()))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--count", type=int, default=45)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--hard", action="store_true", help="150 dpi, mocniejsze rozmycie, szum i obrót")
    args = parser.parse_args()
    manifest = json.loads((args.source / "manifest.json").read_text())
    digital = sorted(k for k, v in manifest.items() if v["source"] == "digital")
    rng = random.Random(args.seed)
    chosen = sorted(rng.sample(digital, args.count))
    args.out.mkdir(parents=True, exist_ok=False)
    selected = {}
    for relative in chosen:
        pdf = args.source / "input" / Path(relative).with_suffix(".pdf")
        target = args.out / "input" / Path(relative).with_suffix(".pdf")
        target.parent.mkdir(parents=True, exist_ok=True)
        with pdfium.PdfDocument(pdf) as document:
            pages = [
                _degrade(
                    document[i].render(scale=settings.OCR_DPI / 72).to_numpy()[:, :, :3].copy(),
                    rng,
                    hard=args.hard,
                )
                for i in range(min(len(document), settings.OCR_MAX_PAGES))
            ]
        pages[0].save(target, "PDF", save_all=True, append_images=pages[1:], resolution=settings.OCR_DPI)
        gold = args.out / "gold" / relative
        gold.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.source / "gold" / relative, gold)
        selected[relative] = {**manifest[relative], "source": "scan", "augmented_from": "digital"}
    (args.out / "manifest.json").write_text(json.dumps(selected, ensure_ascii=False, indent=2) + "\n")
    print(f"Wrote {len(selected)} synthetic scans to {args.out}")


if __name__ == "__main__":
    main()

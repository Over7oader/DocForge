"""Frozen OCR-only benchmark: new synthetic images and optional known train scans, no LLM API.

Use --train-run runs/typed-14b for 15 known train scans; labels/holdout are never read.
Character error on synthetic text measures OCR, not extraction F1 or unseen generalization.
"""

import argparse
import json
import time
import unicodedata
from dataclasses import asdict
from pathlib import Path

import cv2
import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFont
from rapidfuzz.distance import Levenshtein

from doxforge import ocr, settings


def _normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).casefold().split())


def _synthetic() -> list[tuple[str, np.ndarray, str]]:
    # Fixed independent strings. No names/values from the competition data.
    lines = [
        "PEŁNOMOCNICTWO",
        "Łucja Żółkiewska i Michał Świątek reprezentują spółkę.",
        "Numer postępowania: ABC/41/2030",
        "Ogłoszenie: 2030/BZP 00123456/02",
        "REGON: 123456789; KRS: 0000123456",
        "Odwołanie dotyczy Opisu Przedmiotu Zamówienia (OPZ).",
        "Kwota: 15 432,10 PLN; data: 2030-04-17",
        "W imieniu spółki podpisuje się: Łucja Żółkiewska",
    ]
    image = Image.new("RGB", (1654, 2339), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 36)
    for i, text in enumerate(lines):
        draw.text((90, 140 + i * 100), text, fill="black", font=font)
    expected = "\n".join(lines)
    normal = np.asarray(image)[:, :, ::-1].copy()
    rotated = np.asarray(image.rotate(2.0, resample=Image.Resampling.BICUBIC, fillcolor="white"))[
        :, :, ::-1
    ].copy()
    rng = np.random.default_rng(20261122)
    noisy = np.clip(normal.astype(float) * 0.55 + 100 + rng.normal(0, 6, normal.shape), 0, 255).astype(
        np.uint8
    )
    blurred = cv2.GaussianBlur(normal, (3, 3), 0.8)
    return [
        (name, array, expected)
        for name, array in (
            ("synthetic-clean", normal),
            ("synthetic-skew", rotated),
            ("synthetic-low-contrast", noisy),
            ("synthetic-blur", blurred),
        )
    ]


def _render(pdf: Path) -> np.ndarray:
    with pdfium.PdfDocument(pdf) as document:
        page = document[0]
        bitmap = page.render(scale=settings.OCR_DPI / 72)
        image = bitmap.to_numpy().copy()
        bitmap.close()
        page.close()
    return image


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--train-run", type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    ocr.prepare_models()
    samples = _synthetic()
    if args.train_run:
        manifest = json.loads((args.train_run / "manifest.json").read_text())
        for name, item in sorted(manifest.items()):
            if item["source"] == "scan" and item["split"] == "train":
                samples.append((name, _render(args.train_run / "input" / Path(name).with_suffix(".pdf")), ""))
    rows = []
    for index, (name, image, expected) in enumerate(samples):
        start = time.monotonic()
        result = ocr.recognize_page(image)
        elapsed = time.monotonic() - start
        observed = {
            engine: "\n".join(line.text for line in sorted(lines, key=lambda row: (row.center, row.left)))
            for engine, lines in result.observations.items()
        }
        observed["ensemble3"] = result.text
        errors = {}
        if expected:
            target = _normalize(expected)
            errors = {
                engine: Levenshtein.distance(target, _normalize(text)) / len(target)
                for engine, text in observed.items()
            }
        row = {
            "sample": name,
            "wall_seconds": elapsed,
            "engine_seconds": result.seconds,
            "failed": result.failed,
            "character_error_rate": errors,
        }
        rows.append(row)
        (args.out / f"page-{index:02}.json").write_text(
            json.dumps({"sample": name, "expected": expected, **asdict(result)}, ensure_ascii=False, indent=2)
            + "\n"
        )
        (args.out / "report.json").write_text(
            json.dumps(
                {
                    "warning": "OCR only: known train + synthetic; no extraction F1 or holdout claim.",
                    "cache_version": ocr.cache_version(),
                    "samples": rows,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
        print(json.dumps(row, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

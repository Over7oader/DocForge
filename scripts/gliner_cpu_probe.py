"""Offline CPU-only encoder experiment on fixed new Polish strings; not full document F1.

Run from the isolated environment described in requirements-gliner-research.txt.
The local checkpoint must be downloaded separately from the pinned public revision.
No threshold selection, fine-tuning, generator labels or competition holdout are used.
"""

import argparse
import hashlib
import json
import os
import platform
import statistics
import time
from pathlib import Path

SAMPLES = [
    {
        "text": "W imieniu Rzeczna Technika sp. z o.o. podpisuje Julia Żak. Pełnomocnikiem jest Łukasz Gaj.",
        "person": ["Julia Żak", "Łukasz Gaj"],
        "organization": ["Rzeczna Technika sp. z o.o."],
    },
    {
        "text": "Zamawiający: Instytut Badań Gleby. Odwołujący: Laboratorium Nadbrzeżne. Pełnomocnik: Anna Cicha.",
        "person": ["Anna Cicha"],
        "organization": ["Instytut Badań Gleby", "Laboratorium Nadbrzeżne"],
    },
    {
        "text": "Marta Wróbel oraz Paweł Łoś udzielają pełnomocnictwa Julii Żak.",
        "person": ["Marta Wróbel", "Paweł Łoś", "Julii Żak"],
        "organization": [],
    },
    {
        "text": "Zespół Usług Rzecznych reprezentuje Alicja Bąk, a Spółdzielnię Wysoka Łąka reprezentuje Adam Ćwik.",
        "person": ["Alicja Bąk", "Adam Ćwik"],
        "organization": ["Zespół Usług Rzecznych", "Spółdzielnię Wysoka Łąka"],
    },
    {
        "text": "Odwołanie dotyczy opisu przedmiotu zamówienia. Wpis wynosi 15 000 złotych.",
        "person": [],
        "organization": [],
    },
    {
        "text": "The applicant is Riverstone Laboratory, represented by Julia Żak and Adam Ćwik.",
        "person": ["Julia Żak", "Adam Ćwik"],
        "organization": ["Riverstone Laboratory"],
    },
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, default=Path("runs/cpu-research/gliner-model"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=1)
    args = parser.parse_args()
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    import torch  # noqa: PLC0415 - select CPU and offline mode before native-library import
    from gliner2 import AutoExtractor  # noqa: PLC0415

    torch.set_num_threads(args.threads)
    torch.set_num_interop_threads(1)
    args.out.mkdir(parents=True, exist_ok=False)
    # Freeze expected spans before loading or asking the model.
    encoded = json.dumps(SAMPLES, ensure_ascii=False, sort_keys=True).encode()
    (args.out / "samples.json").write_bytes(encoded)
    start = time.monotonic()
    extractor = AutoExtractor.from_pretrained(
        str(args.model.resolve()),
        map_location="cpu",
        local_files_only=True,
        attention_backend="standard",
        quantize=False,
    )
    extractor.eval()
    load_seconds = time.monotonic() - start
    assert all(p.device.type == "cpu" for p in extractor.parameters())
    parameters = sum(p.numel() for p in extractor.parameters())
    rows = []
    with torch.inference_mode():
        for sample in SAMPLES:
            started = time.monotonic()
            result = extractor.extract_entities(
                sample["text"],
                ["person", "organization"],
                include_confidence=True,
                include_spans=True,
            )
            rows.append(
                {
                    "text": sample["text"],
                    "expected": {k: v for k, v in sample.items() if k != "text"},
                    "result": result,
                    "seconds": time.monotonic() - started,
                }
            )
    report = {
        "warning": "New synthetic span probe only; not extraction F1 or proof of generalization.",
        "checkpoint": "fastino/gliner2.5-multi-v1",
        "revision": "7b005274e82da37dcf1f5fd9d6a2ef413c6a004c",
        "samples_sha256": hashlib.sha256(encoded).hexdigest(),
        "python": platform.python_version(),
        "torch": torch.__version__,
        "cpu_threads": args.threads,
        "parameters": parameters,
        "load_seconds": load_seconds,
        "mean_seconds": statistics.mean(r["seconds"] for r in rows),
        "rows": rows,
    }
    (args.out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

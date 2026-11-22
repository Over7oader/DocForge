"""Manifest zbioru organizatora (katalog `dane` repo hacknation-2026) i przygotowanie przebiegu.

Przebieg ewaluacji odtwarza protokół organizatora: `input/<sprawa>/<plik>.pdf` na wejściu,
wzorce w `gold/<sprawa>/<plik>.json`. Opcjonalnie nazwy plików są losowe, bo klasyfikacja
nie może zależeć od nazwy pliku (README organizatora, §3).
"""

import json
import random
import shutil
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from doxforge import settings


class Source(StrEnum):
    DIGITAL = "digital"
    SCAN = "scan"
    NEGATIVE = "negative"


class Split(StrEnum):
    TRAIN = "train"
    HOLDOUT = "holdout"


class EvaluationConfig(BaseModel):
    """Zawartość config/evaluation.yaml."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    holdout_negatives: frozenset[str]
    holdout_scans: frozenset[str]  # numery skanów (nazwy katalogów, np. "0006")


@dataclass(frozen=True)
class Sample:
    case_id: str
    pdf: Path
    gold: Path | None  # None: dokument negatywny, oczekiwany pusty wynik
    source: Source
    split: Split


# Nazwa PDF w katalogu przyklad_XX -> plik etykiety. Pozostałe PDF-y w sprawie to negatywy.
_CASE_LABELS = {
    "odwolanie": "odwolanie.json",
    "pelnomocnictwo": "pelnomocnictwo.json",
    "potwierdzenie_przelewu": "przelew.json",
    "przelew": "przelew.json",
}


def load_evaluation_config(path: Path = settings.EVALUATION_CONFIG) -> EvaluationConfig:
    return EvaluationConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def load_manifest(dataset_dir: Path, config: EvaluationConfig) -> list[Sample]:
    """Lista dokumentów zbioru z etykietą, źródłem i przydziałem do części train/holdout."""
    samples = []
    for case in sorted(dataset_dir.glob("przyklad_*")):
        for pdf in sorted(case.glob("*.pdf")):
            label = _CASE_LABELS.get(pdf.stem)
            if label is None:
                samples.append(Sample(case.name, pdf, None, Source.NEGATIVE, Split.TRAIN))
                continue
            gold = case / label
            if not gold.is_file():
                raise FileNotFoundError(f"Missing label {gold}")
            samples.append(Sample(case.name, pdf, gold, Source.DIGITAL, Split.TRAIN))

    for pdf in sorted((dataset_dir / "skany").glob("*/*/*.pdf")):
        gold = pdf.with_suffix(".json")
        if not gold.is_file():
            raise FileNotFoundError(f"Missing label {gold}")
        case_id = f"skan_{pdf.parent.parent.name}_{pdf.parent.name}"
        split = Split.HOLDOUT if pdf.parent.name in config.holdout_scans else Split.TRAIN
        samples.append(Sample(case_id, pdf, gold, Source.SCAN, split))

    negatives = sorted((dataset_dir / "negatywne").glob("*.pdf"))
    unknown = config.holdout_negatives - {pdf.name for pdf in negatives}
    if unknown:
        raise ValueError(f"Holdout negatives not found in {dataset_dir / 'negatywne'}: {sorted(unknown)}")
    for index, pdf in enumerate(negatives, start=1):
        split = Split.HOLDOUT if pdf.name in config.holdout_negatives else Split.TRAIN
        samples.append(Sample(f"negatywny_{index:02d}", pdf, None, Source.NEGATIVE, split))
    return samples


def prepare_run(samples: list[Sample], run_dir: Path, *, shuffle_names: bool, seed: int) -> None:
    """Tworzy `run_dir/input`, `run_dir/gold` i `run_dir/manifest.json` dla wybranych dokumentów."""
    if run_dir.exists() and any(run_dir.iterdir()):
        raise FileExistsError(f"Run directory {run_dir} is not empty")
    rng = random.Random(seed)
    manifest: dict[str, dict[str, str]] = {}
    for sample in samples:
        stem = f"dokument_{rng.getrandbits(32):08x}" if shuffle_names else sample.pdf.stem
        rel = Path(sample.case_id) / f"{stem}.json"
        if rel.as_posix() in manifest:
            raise ValueError(f"Duplicate document name {rel} in run")
        target_pdf = run_dir / "input" / rel.with_suffix(".pdf")
        target_pdf.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(sample.pdf, target_pdf)

        gold = run_dir / "gold" / rel
        gold.parent.mkdir(parents=True, exist_ok=True)
        if sample.gold is None:
            gold.write_text(json.dumps(settings.EMPTY_RESULT) + "\n", encoding="utf-8")
        else:
            shutil.copyfile(sample.gold, gold)

        manifest[rel.as_posix()] = {
            "source": sample.source,
            "split": sample.split,
            "original": f"{sample.pdf.parent.name}/{sample.pdf.name}",
        }
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

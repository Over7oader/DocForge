"""Contest deadline covers local OCR and LLM; retain valid, atomically completed results."""

import json
import logging
import math
import multiprocessing
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from pydantic import JsonValue

from doxforge import pipeline, settings
from doxforge.doc_types import DocType, DocTypeConfig
from doxforge.ingest import TextSource, ocr_page_count
from doxforge.llm.client import LLMSettings
from doxforge.llm.ensemble import build_client
from doxforge.ocr import OCRPace, load_ocr_settings

logger = logging.getLogger(__name__)


@dataclass
class Candidate:
    target: Path
    prepared: pipeline.Prepared
    priority: tuple[bool, int]


def _missing(value: JsonValue) -> int:
    if isinstance(value, dict):
        return sum(map(_missing, value.values()))
    if isinstance(value, list):
        return sum(map(_missing, value))
    return int(value is None)


def _baseline(
    pdf: Path, target: Path, types: dict[str, DocType], temp: str, pace: OCRPace | None
) -> Candidate | None:
    try:
        prepared = pipeline.prepare(pdf, types, pace)
        result = (
            pipeline.constrain_result(prepared, types[prepared.kind], prepared.result)
            if prepared
            else settings.EMPTY_RESULT
        )
    except Exception:
        # One broken document (PDF, OCR, post-processing) must not stop the batch.
        logger.exception("Local extraction failed for %s", pdf)
        prepared, result = None, settings.EMPTY_RESULT
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=temp, delete=False, mode="w", encoding="utf-8") as staging:
        json.dump(result, staging, ensure_ascii=False, indent=2)
        staging.write("\n")
    Path(staging.name).replace(target)
    if prepared and types[prepared.kind].config.llm:
        scanned = any(p.source == TextSource.OCR for p in prepared.document.pages)
        return Candidate(target, prepared, (scanned, _missing(result)))
    return None


def _enrich(
    candidates: list[Candidate],
    configs: dict[str, DocTypeConfig],
    cfg: LLMSettings,
    temp: str,
    log_level: int,
) -> None:
    logging.basicConfig(level=log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    types = {name: DocType(config) for name, config in configs.items()}
    client = build_client(cfg)

    def update(item: Candidate) -> None:
        prepared = item.prepared
        try:
            result = pipeline.finish(prepared, types[prepared.kind], client)
            if result == prepared.result:
                return
            # Write on the same filesystem. Only complete improvements reach output.
            with tempfile.NamedTemporaryFile(dir=temp, delete=False, mode="w", encoding="utf-8") as staging:
                json.dump(result, staging, indent=2, ensure_ascii=False)
                staging.write("\n")
            Path(staging.name).replace(item.target)
        except Exception:
            logger.exception("Keeping baseline for %s", item.target)

    try:
        with ThreadPoolExecutor(max_workers=settings.PIPELINE_WORKERS) as executor:
            list(executor.map(update, candidates))
    finally:
        client.close()


@dataclass(frozen=True)
class WorkerJob:
    """Everything the spawned worker needs (picklable)."""

    paths: list[tuple[Path, Path]]
    configs: dict[str, DocTypeConfig]
    cfg: LLMSettings | None
    temp: str
    log_level: int
    seconds: float


def _run_phases(job: WorkerJob) -> None:
    logging.basicConfig(level=job.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    types = {name: DocType(config) for name, config in job.configs.items()}
    pace = None
    if load_ocr_settings().mode == "ensemble3":
        pages = sum(map(ocr_page_count, (pdf for pdf, _ in job.paths)))
        reserve = settings.LLM_PHASE_RESERVE_S if job.cfg is not None else 0
        pace = OCRPace(pages, job.seconds - reserve)
        logger.info("OCR plan: %d page(s), %.0f s before the LLM phase", pages, job.seconds - reserve)
    with ThreadPoolExecutor(max_workers=settings.PIPELINE_WORKERS) as executor:
        futures = [
            executor.submit(_baseline, pdf, target, types, job.temp, pace) for pdf, target in job.paths
        ]
        candidates = [c for future in futures if (c := future.result()) is not None]
    logger.info("Local pass completed %d files; %d candidates for LLM", len(job.paths), len(candidates))
    if not candidates or job.cfg is None:
        return
    candidates.sort(key=lambda item: item.priority, reverse=True)
    _enrich(candidates, job.configs, job.cfg, job.temp, job.log_level)


def run(
    input_dir: Path, output_dir: Path, types: dict[str, DocType], cfg: LLMSettings | None, seconds: float
) -> None:
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("Time budget must be finite and positive")
    deadline = time.monotonic() + seconds
    pdfs = sorted(p for p in input_dir.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf")
    targets = [output_dir / p.relative_to(input_dir).with_suffix(".json") for p in pdfs]
    if len(set(targets)) != len(targets):
        raise ValueError("PDF names collide in the output tree")
    # A deadline during native OCR must leave valid JSON for every input, even when no
    # extraction has completed. Empty placeholders do not count as successful extraction.
    for target in targets:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}\n", encoding="utf-8")
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir) as temp:
        configs = {name: dt.config for name, dt in types.items()}
        worker = multiprocessing.get_context("spawn").Process(
            target=_run_phases,
            args=(
                WorkerJob(
                    list(zip(pdfs, targets, strict=True)),
                    configs,
                    cfg,
                    temp,
                    logging.getLogger().getEffectiveLevel(),
                    deadline - time.monotonic(),
                ),
            ),
        )
        worker.start()
        try:
            worker.join(max(0, deadline - time.monotonic()))
            if worker.is_alive():
                logger.warning(
                    "OCR/LLM time budget exhausted; keeping completed results and empty placeholders"
                )
            elif worker.exitcode:
                raise RuntimeError(f"Extraction worker exited with code {worker.exitcode}")
        finally:
            if worker.is_alive():
                worker.kill()
            worker.join()

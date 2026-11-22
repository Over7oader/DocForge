"""Local CPU OCR: PP-OCRv6 Small + PP-OCRv5 Latin Mobile + Tesseract LSTM.

Wynik OCR to luźne ramki tekstu; składamy je w linie w kolejności czytania. Skany bywają
przekrzywione o 1-2°, co na szerokości strony przesuwa linię o kilkadziesiąt pikseli, więc
przed grupowaniem obracamy środki ramek o kąt oszacowany z samych ramek.
"""

import csv
import io
import logging
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import cache
from itertools import pairwise
from statistics import median
from typing import TYPE_CHECKING

import cv2
import numpy as np
from numpy.typing import NDArray
from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR
from rapidocr.utils.output import RapidOCROutput

from doxforge import settings
from doxforge.ocr_consensus import Decision, OCRLine, consensus
from doxforge.ocr_models import ensure_tessdata

if TYPE_CHECKING:
    import tesserocr

logger = logging.getLogger(__name__)
_MAX_THREADS = 16
_TSV_COLUMNS = 12
_COLOR_NDIM = 3
_MAJORITY = 2


@dataclass(frozen=True)
class _Box:
    text: str
    x_left: float
    x_right: float
    y_center: float
    height: float
    score: float = 1.0


# Pages are serialized. The three independent engines use bounded native threads per page.
_LOCK = threading.Lock()


@cache
def _engine() -> RapidOCR:
    # PP-OCRv6 small: na skanach zbioru poprawnie czyta polskie znaki (ł, ą, ż), których model
    # PP-OCRv5 „latin” nie rozpoznawał (Białystok -> Biatystok), przy tym samym czasie na stronę.
    return RapidOCR(params={"Rec.ocr_version": OCRVersion.PPOCRV6, "Rec.model_type": ModelType.SMALL})


@dataclass(frozen=True)
class OCRSettings:
    mode: str = "ensemble3"
    threads: int = 1


def load_ocr_settings() -> OCRSettings:
    mode = os.environ.get("DOXFORGE_OCR", "ensemble3")
    threads = int(os.environ.get("DOXFORGE_OCR_THREADS", "1"))
    if mode not in {"single", "ensemble3"} or not 1 <= threads <= _MAX_THREADS:
        raise ValueError("OCR requires DOXFORGE_OCR=single|ensemble3 and DOXFORGE_OCR_THREADS=1..16")
    return OCRSettings(mode, threads)


def cache_version() -> str:
    config = load_ocr_settings()
    if config.mode == "single":
        return settings.OCR_CACHE_VERSION
    return (
        f"ensemble3-v4-{settings.OCR_DPI}dpi-score{settings.OCR_MIN_BOX_SCORE}"
        f"-tess-best-e12c65a9-threads{config.threads}"
    )


@cache
def _ensemble_engine(version: str, threads: int) -> RapidOCR:
    params = {
        "EngineConfig.onnxruntime.intra_op_num_threads": threads,
        "EngineConfig.onnxruntime.inter_op_num_threads": 1,
        "Rec.ocr_version": OCRVersion.PPOCRV6,
        "Rec.model_type": ModelType.SMALL,
    }
    if version == "ppocr_v5":
        params.update(
            {
                "Det.ocr_version": OCRVersion.PPOCRV5,
                "Det.model_type": ModelType.MOBILE,
                "Rec.ocr_version": OCRVersion.PPOCRV5,
                "Rec.model_type": ModelType.MOBILE,
                "Rec.lang_type": LangRec.LATIN,
            }
        )
    return RapidOCR(params=params)


@cache
def _tesseract() -> "tesserocr.PyTessBaseAPI":
    # Bound OpenMP before loading the native library; PP-OCR threads are configured separately.
    os.environ.setdefault("OMP_THREAD_LIMIT", "1")
    import tesserocr  # noqa: PLC0415 - OpenMP must be bounded before loading the native library.

    api = tesserocr.PyTessBaseAPI(
        path=str(ensure_tessdata()), lang="pol+eng", oem=tesserocr.OEM.LSTM_ONLY, psm=tesserocr.PSM.AUTO
    )
    api.SetVariable("classify_enable_learning", "0")
    api.SetVariable("classify_enable_adaptive_matcher", "0")
    return api


@dataclass(frozen=True)
class Observations:
    quads: NDArray[np.float64]
    texts: list[str]
    scores: list[float]


def _rapid(image: NDArray[np.uint8], version: str, threads: int) -> Observations:
    result = _ensemble_engine(version, threads)(image)
    if not isinstance(result, RapidOCROutput):
        raise TypeError(f"Unexpected RapidOCR result type {type(result).__name__}")
    if result.boxes is None or result.txts is None or result.scores is None:
        return Observations(np.empty((0, 4, 2)), [], [])
    return Observations(np.asarray(result.boxes, dtype=np.float64), list(result.txts), list(result.scores))


def _parse_tsv(tsv: str) -> Observations:
    # Preserve Tesseract's native line membership. Word heights/diacritics differ too much
    # for center-based re-grouping, especially in small print and rotated pages.
    groups: dict[tuple[str, ...], list[tuple[str, float, int, int, int, int]]] = {}
    # QUOTE_NONE: an OCR-read `"` would otherwise open a quoted field and swallow later rows.
    for row in csv.reader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        if len(row) < _TSV_COLUMNS or row[0] != "5" or not row[11].strip():
            continue
        x, y, width, height = map(int, row[6:10])
        score = float(row[10]) / 100
        if score < 0 or width <= 0 or height <= 0:
            continue
        groups.setdefault(tuple(row[1:5]), []).append((row[11], score, x, y, width, height))
    quads, texts, scores = [], [], []
    for words in groups.values():
        left = min(word[2] for word in words)
        right = max(word[2] + word[4] for word in words)
        centers = np.asarray([(w[2] + w[4] / 2, w[3] + w[5] / 2) for w in words])
        slope = 0.0
        if len(words) > 1 and float(np.ptp(centers[:, 0])) > 1:
            slope = float(np.polyfit(centers[:, 0], centers[:, 1], 1)[0])
        intercept = float(np.median(centers[:, 1] - slope * centers[:, 0]))
        half_height = max(w[5] for w in words) / 2
        y_left, y_right = slope * left + intercept, slope * right + intercept
        quads.append(
            [
                [left, y_left - half_height],
                [right, y_right - half_height],
                [right, y_right + half_height],
                [left, y_left + half_height],
            ]
        )
        texts.append(" ".join(w[0] for w in words))
        scores.append(sum(len(w[0]) * w[1] for w in words) / sum(len(w[0]) for w in words))
    return Observations(np.asarray(quads, dtype=np.float64).reshape(-1, 4, 2), texts, scores)


def _tess(image: NDArray[np.uint8]) -> Observations:
    api = _tesseract()
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == _COLOR_NDIM else image
    data = np.ascontiguousarray(gray).tobytes()
    api.ClearAdaptiveClassifier()
    api.SetImageBytes(data, gray.shape[1], gray.shape[0], 1, gray.shape[1])
    api.SetSourceResolution(settings.OCR_DPI)
    if not api.Recognize(timeout=20_000):
        raise TimeoutError("Tesseract page recognition exceeded 20 s")
    return _parse_tsv(api.GetTSVText(0))


@cache
def _workers() -> ThreadPoolExecutor:
    # Each engine owns a separate model. Pages remain serialized by _LOCK.
    return ThreadPoolExecutor(max_workers=3, thread_name_prefix="ocr")


@dataclass(frozen=True)
class PageOCR:
    text: str
    decisions: list[Decision]
    seconds: dict[str, float]
    failed: tuple[str, ...]
    observations: dict[str, list[OCRLine]] = field(default_factory=dict)
    # Ensemble mode fell back to a single engine to meet the deadline; not a cached ensemble reading.
    degraded: bool = False


def _timed(image: NDArray[np.uint8], engine: str, threads: int) -> tuple[Observations, float]:
    start = time.monotonic()
    result = _tess(image) if engine == "tesseract" else _rapid(image, engine, threads)
    return result, time.monotonic() - start


def _skew_angle(quads: NDArray[np.float64]) -> float:
    """Mediana kąta górnych krawędzi szerokich ramek (radiany)."""
    angles = [
        math.atan2(q[1][1] - q[0][1], q[1][0] - q[0][0])
        for q in quads
        if q[1][0] - q[0][0] > 3 * abs(q[3][1] - q[0][1])
    ]
    return median(angles) if angles else 0.0


def _boxes(
    quads: NDArray[np.float64],
    texts: list[str],
    scores: list[float] | None = None,
    angle: float | None = None,
) -> list[_Box]:
    angle = _skew_angle(quads) if angle is None else angle
    cos, sin = math.cos(-angle), math.sin(-angle)
    boxes = []
    for index, (quad, text) in enumerate(zip(quads, texts, strict=True)):
        xs = [x * cos - y * sin for x, y in quad]
        ys = [x * sin + y * cos for x, y in quad]
        boxes.append(
            _Box(
                text,
                min(xs),
                max(xs),
                (min(ys) + max(ys)) / 2,
                max(ys) - min(ys),
                scores[index] if scores is not None else 1.0,
            )
        )
    return boxes


def _box_groups(boxes: list[_Box]) -> list[list[_Box]]:
    """Grupuje ramki w linie po środku w pionie; odstęp szerszy niż ok. 2 znaki zapisuje jako 3 spacje."""
    lines: list[list[_Box]] = []
    for box in sorted(boxes, key=lambda b: b.y_center):
        current = lines[-1] if lines else None
        if current and abs(box.y_center - median(b.y_center for b in current)) < 0.5 * min(
            box.height, median(b.height for b in current)
        ):
            current.append(box)
        else:
            lines.append([box])
    return lines


def _join(line: list[_Box]) -> str:
    parts = sorted(line, key=lambda b: b.x_left)
    text = parts[0].text
    for previous, box in pairwise(parts):
        char_width = (previous.x_right - previous.x_left) / max(len(previous.text), 1)
        text += ("   " if box.x_left - previous.x_right > 2 * char_width else " ") + box.text
    return text


def _lines(boxes: list[_Box]) -> list[str]:
    return [_join(line) for line in _box_groups(boxes)]


def _observed_lines(engine: str, observation: Observations, angle: float) -> list[OCRLine]:
    keep = [i for i, score in enumerate(observation.scores) if score >= settings.OCR_MIN_BOX_SCORE]
    boxes = _boxes(
        observation.quads[keep],
        [observation.texts[i] for i in keep],
        [observation.scores[i] for i in keep],
        angle,
    )
    groups = [[box] for box in boxes] if engine == "tesseract" else _box_groups(boxes)
    return [
        OCRLine(
            engine,
            _join(line),
            sum(box.score * len(box.text) for box in line) / max(1, sum(len(box.text) for box in line)),
            min(box.x_left for box in line),
            min(box.y_center - box.height / 2 for box in line),
            max(box.x_right for box in line),
            max(box.y_center + box.height / 2 for box in line),
        )
        for line in groups
    ]


class OCRPace:
    """Chooses ensemble or single-engine OCR per page so the planned pages fit before a deadline.

    The ensemble costs ~2.7x a single engine (16.4 vs 6.0 s/page on 2 vCPU). When the remaining
    pages would not fit at the ensemble pace, later pages use PP-OCRv6 alone: a slightly worse
    reading beats an empty result for documents never reached.
    """

    def __init__(self, pages: int, seconds: float) -> None:
        self.remaining = pages
        self.deadline = time.monotonic() + seconds
        self.ensemble_seconds = settings.OCR_ENSEMBLE_PAGE_S
        self.single_seconds = settings.OCR_SINGLE_PAGE_S

    def use_ensemble(self) -> bool:
        # Greedy: ensemble for this page only if all later pages still fit at single-engine pace.
        left = self.deadline - time.monotonic()
        return self.ensemble_seconds + max(0, self.remaining - 1) * self.single_seconds <= left

    def record(self, *, ensemble: bool, seconds: float) -> None:
        self.remaining = max(0, self.remaining - 1)
        # Exponential average: the machine may be slower than the measured prior.
        if ensemble:
            self.ensemble_seconds = 0.7 * self.ensemble_seconds + 0.3 * seconds
        else:
            self.single_seconds = 0.7 * self.single_seconds + 0.3 * seconds


def recognize_page(image: NDArray[np.uint8], pace: OCRPace | None = None) -> PageOCR:
    """Three independent observations, spatial voting and diagnostics, without an LLM."""
    config = load_ocr_settings()
    with _LOCK:
        if config.mode == "single":
            start = time.monotonic()
            return PageOCR(_single(image), [], {"ppocr_v6": time.monotonic() - start}, ())
        if pace is not None and not pace.use_ensemble():
            start = time.monotonic()
            text = _single(image)
            pace.record(ensemble=False, seconds=time.monotonic() - start)
            logger.warning("OCR ensemble skipped to meet the time budget; single engine used")
            return PageOCR(text, [], {"ppocr_v6": time.monotonic() - start}, (), degraded=True)
        page_start = time.monotonic()
        # Configure process-wide Tesseract parameters before running native work in parallel.
        failed = []
        engines = ["ppocr_v6", "ppocr_v5", "tesseract"]
        try:
            _tesseract()
        except Exception:
            logger.exception("Tesseract initialization failed; retaining PP-OCR observations")
            engines.remove("tesseract")
            failed.append("tesseract")
        futures = {name: _workers().submit(_timed, image, name, config.threads) for name in engines}
        results, seconds = {}, {}
        for name, future in futures.items():
            try:
                results[name], seconds[name] = future.result()
            except Exception:
                logger.exception("OCR engine %s failed; retaining other observations", name)
                failed.append(name)
        if not results:
            raise RuntimeError("All OCR engines failed")
        anchor = results.get("ppocr_v6", next(iter(results.values())))
        angle = _skew_angle(anchor.quads)
        observations = {name: _observed_lines(name, result, angle) for name, result in results.items()}
        lines = [line for rows in observations.values() for line in rows]
        decisions = consensus(lines)
        text = "\n".join(decision.selected.text for decision in decisions)
        logger.info(
            "OCR ensemble: %d rows, %d agreed, %d secondary, seconds=%s, failed=%s",
            len(decisions),
            sum(d.votes >= _MAJORITY for d in decisions),
            sum(d.selected.engine != "ppocr_v6" for d in decisions),
            seconds,
            failed,
        )
        if pace is not None:
            pace.record(ensemble=True, seconds=time.monotonic() - page_start)
        return PageOCR(text, decisions, seconds, tuple(failed), observations)


def prepare_models() -> None:
    """Download and initialize every model before starting the timed contest run."""
    config = load_ocr_settings()
    with _LOCK:
        if config.mode == "ensemble3":
            _tesseract()
            engines = [_ensemble_engine(name, config.threads) for name in ("ppocr_v6", "ppocr_v5")]
        else:
            engines = [_engine()]
        for engine in engines:
            engine._load_det_model()  # type: ignore[no-untyped-call]
            engine._load_cls_model()  # type: ignore[no-untyped-call]
            engine._load_rec_model()  # type: ignore[no-untyped-call]


def _single(image: NDArray[np.uint8]) -> str:
    """Tekst obrazu strony, linia po linii; ramki o niskiej pewności (np. prześwit z odwrotu) pomijane."""
    result = _engine()(image)
    if not isinstance(result, RapidOCROutput):
        raise TypeError(f"Unexpected RapidOCR result type {type(result).__name__}")
    if result.boxes is None or result.txts is None or result.scores is None:
        return ""
    keep = [i for i, score in enumerate(result.scores) if score >= settings.OCR_MIN_BOX_SCORE]
    quads = np.asarray(result.boxes, dtype=np.float64)[keep]
    return "\n".join(_lines(_boxes(quads, [result.txts[i] for i in keep])))


def ocr_image(image: NDArray[np.uint8]) -> str:
    return recognize_page(image).text

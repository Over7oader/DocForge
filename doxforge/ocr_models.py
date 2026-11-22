"""Pinned public OCR weights; model downloads never send document content."""

import hashlib
import tempfile
from pathlib import Path

import httpx

from doxforge import settings

TESS_REVISION = "e12c65a915945e4c28e237a9b52bc4a8f39a0cec"
TESS_HASHES = {
    "pol": "e80cc4cefbdface06e9223f43f089556b9dcf104020fbc0a200f6863c57d4405",
    "eng": "8280aed0782fe27257a68ea10fe7ef324ca0f8d85bd2fd145d1c2b560bcb66ba",
}
MODEL_DIR = settings.PROJECT_ROOT / ".cache" / "ocr-models" / "tessdata_best"


def ensure_tessdata() -> Path:
    """Download once, verify SHA-256 and atomically publish each pinned model."""
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for language, expected in TESS_HASHES.items():
        target = MODEL_DIR / f"{language}.traineddata"
        if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == expected:
            continue
        url = (
            "https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/"
            f"{TESS_REVISION}/{language}.traineddata"
        )
        staging: Path | None = None
        try:
            with httpx.stream("GET", url, follow_redirects=True, timeout=120) as response:
                response.raise_for_status()
                digest = hashlib.sha256()
                with tempfile.NamedTemporaryFile(dir=MODEL_DIR, delete=False) as handle:
                    staging = Path(handle.name)
                    for chunk in response.iter_bytes():
                        handle.write(chunk)
                        digest.update(chunk)
            if digest.hexdigest() != expected:
                raise ValueError(f"OCR model checksum mismatch: {language}")
            staging.replace(target)
        finally:
            if staging is not None:
                staging.unlink(missing_ok=True)
    return MODEL_DIR

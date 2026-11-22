import json
import os
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from doxforge import budget, settings
from doxforge.doc_types import load_doc_types


def _slow_local_stage(job):
    # A native OCR call can block independently of whether an LLM is enabled.
    job.paths[0][1].write_text('{"completed": true}\n')
    (Path(job.temp) / "partial.tmp").write_text('{"unfinished":')
    time.sleep(60)


def test_deadline_covers_local_ocr_without_llm_and_preserves_valid_outputs(tmp_path, monkeypatch):
    inputs, outputs = tmp_path / "input", tmp_path / "output"
    inputs.mkdir()
    for name in ("one.pdf", "two.pdf"):
        (inputs / name).write_bytes(b"fake PDF handled by fake worker")
    monkeypatch.setattr(budget, "_run_phases", _slow_local_stage)
    start = time.monotonic()
    budget.run(inputs, outputs, load_doc_types(), None, 3)
    assert time.monotonic() - start < 8
    assert json.loads((outputs / "one.json").read_text()) == {"completed": True}
    assert json.loads((outputs / "two.json").read_text()) == {}
    assert sorted(path.name for path in outputs.iterdir()) == ["one.json", "two.json"]


def test_api_cooldown_preserves_baseline_files(tmp_path: Path):
    fixture = Path(__file__).parent / "fixtures/document_variants"
    inputs = tmp_path / "input"
    outputs = tmp_path / "output"
    inputs.mkdir()
    for stem in ("a7", "f2"):
        shutil.copyfile(fixture / "input" / f"{stem}.pdf", inputs / f"{stem}.pdf")
    attempted = threading.Event()

    class Limited(BaseHTTPRequestHandler):
        def do_POST(self):
            attempted.set()
            self.send_response(429)
            self.send_header("Retry-After", "120")
            self.end_headers()

    with ThreadingHTTPServer(("127.0.0.1", 0), Limited) as server:
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "doxforge",
                    "run",
                    "--input",
                    str(inputs),
                    "--output",
                    str(outputs),
                    "--budget-seconds",
                    "6",
                ],
                env={
                    **os.environ,
                    "DOXFORGE_LLM": "openai_compatible",
                    "LLM_MODEL": "simulated-cooldown",
                    "LLM_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1/",
                    "LLM_API_KEY": "test",
                },
                cwd=settings.PROJECT_ROOT,
                check=True,
                timeout=30,
            )
        finally:
            server.shutdown()
            thread.join()
    assert attempted.is_set()
    assert sorted(p.name for p in outputs.iterdir()) == ["a7.json", "f2.json"]
    for result in outputs.iterdir():
        assert json.loads(result.read_text()) == json.loads((fixture / "gold" / result.name).read_text())

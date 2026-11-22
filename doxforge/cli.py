"""Interfejs wiersza poleceń: `python -m doxforge <polecenie>`."""

import argparse
import json
import logging
from pathlib import Path

from dotenv import load_dotenv

from doxforge import budget, ocr, pipeline, settings
from doxforge.doc_types import load_doc_types
from doxforge.evaluation.dataset import Split, load_evaluation_config, load_manifest, prepare_run
from doxforge.evaluation.scorer import DISCORD, PRIMARY, format_report, score_run, summarize
from doxforge.llm.client import load_llm_settings
from doxforge.llm.ensemble import build_client

logger = logging.getLogger(__name__)


def _run(args: argparse.Namespace) -> None:
    # Błędna konfiguracja (klasy, zmienne LLM) przerywa start, zanim cokolwiek przetworzymy.
    doc_types = load_doc_types()
    llm_settings = load_llm_settings()
    ocr.load_ocr_settings()
    if args.budget_seconds is not None:
        budget.run(args.input, args.output, doc_types, llm_settings, args.budget_seconds)
        return
    if llm_settings is None:
        logger.info("LLM disabled (DOXFORGE_LLM=none), rules only")
        pipeline.run(args.input, args.output, doc_types)
        return
    logger.info("LLM: %s at %s", llm_settings.model, llm_settings.base_url)
    client = build_client(llm_settings)
    try:
        pipeline.run(args.input, args.output, doc_types, client)
    finally:
        client.close()


def _prepare_eval(args: argparse.Namespace) -> None:
    samples = load_manifest(args.dataset, load_evaluation_config())
    if args.split != "all":
        samples = [s for s in samples if s.split == Split(args.split)]
    prepare_run(samples, args.out, shuffle_names=not args.keep_names, seed=args.seed)
    logger.info("Prepared %d documents in %s", len(samples), args.out)


def _score(args: argparse.Namespace) -> None:
    run_dir: Path = args.run
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    sources = {rel: entry["source"] for rel, entry in manifest.items()}
    scores = score_run(run_dir / "gold", args.pred or run_dir / "output", load_doc_types(), sources)
    summary = summarize(scores, DISCORD if args.metric == "discord-proxy" else PRIMARY)
    (run_dir / "report.json").write_text(
        json.dumps(summary.as_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(format_report(summary))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="doxforge", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true", help="logi na poziomie DEBUG")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="przetwórz PDF-y z katalogu input do output")
    run.add_argument("--input", type=Path, default=settings.DEFAULT_INPUT_DIR)
    run.add_argument("--output", type=Path, default=settings.DEFAULT_OUTPUT_DIR)
    run.add_argument("--budget-seconds", type=int, help="budżet całej partii: OCR, ekstrakcja lokalna i LLM")
    run.set_defaults(handler=_run)

    prepare = commands.add_parser("prepare-eval", help="utwórz przebieg ewaluacji ze zbioru organizatora")
    prepare.add_argument("--out", type=Path, required=True, help="pusty katalog przebiegu, np. runs/m1")
    prepare.add_argument("--split", choices=[*Split, "all"], default=Split.TRAIN)
    prepare.add_argument("--dataset", type=Path, default=settings.DATASET_DIR)
    prepare.add_argument("--keep-names", action="store_true", help="zachowaj oryginalne nazwy plików")
    prepare.add_argument("--seed", type=int, default=0)
    prepare.set_defaults(handler=_prepare_eval)

    score = commands.add_parser("score", help="policz F1 przebiegu (gold vs output)")
    score.add_argument("run", type=Path, help="katalog przebiegu z gold/ i manifest.json")
    score.add_argument("--pred", type=Path, help="katalog wyników (domyślnie <run>/output)")
    score.add_argument("--metric", choices=["historical", "discord-proxy"], default="historical")
    score.set_defaults(handler=_score)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    load_dotenv(settings.ENV_FILE)  # zmienne już ustawione w środowisku mają pierwszeństwo
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args.handler(args)

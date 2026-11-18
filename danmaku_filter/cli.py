"""Command line interface for the danmaku filtering prototype."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .classify import get_classifier
from .dmproto import parse_segment
from .fetch import fetch_all_segments, fetch_danmaku, get_video_info, load_danmaku, save_jsonl
from .feedback import FeedbackStore
from .metrics import prf
from .taxonomy import CATEGORY_IDS, FALLBACK_ID, spec


def _read_jsonl(path: str) -> list[dict]:
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def cmd_taxonomy(args: argparse.Namespace) -> int:
    print(json.dumps(spec(), ensure_ascii=False, indent=2))
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    if args.source == "list":
        items = fetch_danmaku(args.bvid)
    else:
        info = get_video_info(args.bvid)
        segments = fetch_all_segments(info["aid"], info["cid"], info["duration"])
        items = [dm for segment in segments for dm in parse_segment(segment)]
        print(f"segments: {len(segments)}")
    save_jsonl(args.out, (dm.to_dict() for dm in items))
    print(f"fetched {len(items)} danmaku -> {args.out}")
    return 0


def cmd_classify(args: argparse.Namespace) -> int:
    items = load_danmaku(args.input)
    if args.limit:
        items = items[: args.limit]
    kwargs = {"video_context": args.context} if args.backend == "llm" else {}
    classifier = get_classifier(args.backend, **kwargs)
    feedback = FeedbackStore(args.feedback) if args.feedback else None
    # Batch first: chat-completions providers bill and rate-limit per request, and
    # batching is what makes a whole video cheap enough to precompute.
    results = classifier.classify_batch(items)
    rows = []
    for dm, result in zip(items, results):
        if feedback is not None:
            result = feedback.apply(dm, result)
        rows.append({"text": dm.text, "time_ms": dm.time_ms, **result.to_dict()})
    save_jsonl(args.out, rows)
    print(f"classified {len(rows)} danmaku -> {args.out}")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    gold = _read_jsonl(args.gold)
    predictions = _read_jsonl(args.pred)
    labels = CATEGORY_IDS + [FALLBACK_ID]
    report = prf(gold, predictions, labels)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def cmd_precompute(args: argparse.Namespace) -> int:
    from .pipeline import precompute

    summary = precompute(
        args.bvid,
        backend=args.backend,
        tags_dir=args.tags_dir,
        limit=args.limit,
        video_context=args.context,
        max_segments=args.max_segments,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_reclassify(args: argparse.Namespace) -> int:
    from .pipeline import reclassify

    summary = reclassify(
        args.bvid,
        backend=args.backend,
        tags_dir=args.tags_dir,
        limit=args.limit,
        video_context=args.context,
        dry_run=args.dry_run,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import serve

    serve(
        host=args.host,
        port=args.port,
        tags_dir=args.tags_dir,
        online_backend=args.online_backend,
        models_dir=args.models_dir,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m danmaku_filter.cli",
        description="Danmaku filtering prototype: fetch, classify, evaluate, serve.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    taxonomy = subparsers.add_parser("taxonomy", help="print the category spec as JSON")
    taxonomy.set_defaults(func=cmd_taxonomy)

    fetch = subparsers.add_parser("fetch", help="download danmaku for a Bilibili video")
    fetch.add_argument("--bvid", required=True)
    fetch.add_argument("--out", required=True)
    fetch.add_argument(
        "--source",
        choices=("seg", "list"),
        default="seg",
        help="seg = full seg.so segments (default); list = legacy list.so sample",
    )
    fetch.set_defaults(func=cmd_fetch)

    classify = subparsers.add_parser("classify", help="label danmaku with rules or an LLM")
    classify.add_argument("--input", required=True)
    classify.add_argument("--out", required=True)
    classify.add_argument("--backend", choices=("rules", "llm"), default="rules")
    classify.add_argument("--feedback", default=None, help="JSONL of user corrections to apply")
    classify.add_argument("--context", default="", help="video title or description for the LLM prompt")
    classify.add_argument("--limit", type=int, default=0)
    classify.set_defaults(func=cmd_classify)

    evaluate = subparsers.add_parser("eval", help="score predictions against a gold set")
    evaluate.add_argument("--gold", required=True)
    evaluate.add_argument("--pred", required=True)
    evaluate.set_defaults(func=cmd_eval)

    precompute = subparsers.add_parser(
        "precompute", help="fetch + classify a whole video into a local tag cache"
    )
    precompute.add_argument("--bvid", required=True)
    precompute.add_argument("--backend", choices=("rules", "llm"), default="rules")
    precompute.add_argument("--tags-dir", default="data/tags")
    precompute.add_argument("--limit", type=int, default=0)
    precompute.add_argument("--context", default="")
    precompute.add_argument("--max-segments", type=int, default=60)
    precompute.set_defaults(func=cmd_precompute)

    reclassify = subparsers.add_parser(
        "reclassify", help="re-label an existing tag cache without refetching"
    )
    reclassify.add_argument("--bvid", required=True)
    reclassify.add_argument("--backend", choices=("rules", "llm"), default="llm")
    reclassify.add_argument("--tags-dir", default="data/tags")
    reclassify.add_argument("--limit", type=int, default=0)
    reclassify.add_argument("--context", default="")
    reclassify.add_argument(
        "--dry-run",
        action="store_true",
        help="report the new distribution without writing the cache",
    )
    reclassify.set_defaults(func=cmd_reclassify)

    serve = subparsers.add_parser("serve", help="run the local policy server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--tags-dir", default="data/tags")
    serve.add_argument(
        "--online-backend",
        default="rules",
        choices=("rules", "llm", "auto", "linear"),
        help="labeller for comments missing from the offline cache",
    )
    serve.add_argument(
        "--models-dir",
        default=None,
        help="trained linear models (default: <tags-dir>/../models)",
    )
    serve.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

"""Command-line collection, local feedback and bounded Twenty Inbox delivery."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import __version__
from .company_size import load_company_sizes
from .config import (
    APPROVED_SOURCE_NAMES,
    require_live_sources,
    DATABASE_PATH,
    DEFAULT_FROM_DATE,
    DEFAULT_TIMEZONE,
    DEFAULT_TO_DATE,
    FEEDBACK_PATH,
    INBOX_EXPORTS_DIR,
    REPORTS_DIR,
    TELEGRAM_SEARCH_SOURCE,
)
from .detail_enrichment import DetailEnrichmentStats, enrich_run_details
from .digest_details import load_digest_details
from .inbox import build_project_inbox_items, write_project_inbox_export
from .reporting import write_reports
from .reporting_v2 import write_v2_reports
from .selection import evaluate_rows
from .selection_v2 import build_candidate_items
from .storage import Storage
from .tdlib_source import TdlibSource
from .feedback import evaluate_feedback, load_feedback
from .feedback_cli import add_feedback_commands
from .morning import add_morning_command


def boundaries(from_date: str, to_date: str, timezone_name: str) -> tuple[datetime, datetime]:
    zone = ZoneInfo(timezone_name)
    start_local = datetime.combine(date.fromisoformat(from_date), time.min, tzinfo=zone)
    end_local = datetime.combine(date.fromisoformat(to_date), time.min, tzinfo=zone)
    if end_local <= start_local:
        raise ValueError("to-date must be later than from-date")
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def new_run_id(label: str) -> str:
    return f"{label}-{uuid.uuid4().hex[:12]}"


def selected_source_names(requested: list[str] | None) -> tuple[str, ...]:
    if not APPROVED_SOURCE_NAMES:
        raise ValueError("Configure approved sources before live collection")
    if not requested:
        return APPROVED_SOURCE_NAMES
    unknown = [name for name in requested if name not in APPROVED_SOURCE_NAMES]
    if unknown:
        raise ValueError(f"source is not approved: {', '.join(unknown)}")
    return tuple(dict.fromkeys(requested))


def compare_runs(storage: Storage, primary_run_id: str, reconciliation_run_id: str) -> dict[str, object]:
    summary = storage.run_summary(primary_run_id)
    comparisons: dict[str, object] = {"sources": {}}
    all_sources_match = True
    for source in summary["sources"]:
        approved_name = str(source["approved_name"])
        chat_id = int(source["chat_id"])
        primary_keys = storage.message_keys(primary_run_id, chat_id)
        reconciliation_keys = storage.message_keys(reconciliation_run_id, chat_id)
        matches = primary_keys == reconciliation_keys
        all_sources_match = all_sources_match and matches
        comparisons["sources"][approved_name] = {
            "chat_id": chat_id,
            "primary_count": len(primary_keys),
            "reconciliation_count": len(reconciliation_keys),
            "matches": matches,
            "only_primary": len(primary_keys - reconciliation_keys),
            "only_reconciliation": len(reconciliation_keys - primary_keys),
        }
    comparisons["all_sources_match"] = all_sources_match
    return comparisons


def run_pipeline(args: argparse.Namespace) -> int:
    start, end = boundaries(args.from_date, args.to_date, args.timezone)
    database_path = Path(args.database).expanduser().resolve()
    reports_dir = Path(args.reports_dir).expanduser().resolve()
    telegram_source = Path(args.telegram_source).expanduser().resolve()
    primary_run_id = new_run_id("primary")
    reconciliation_run_id = new_run_id("reconcile")
    period_label = f"{args.from_date}-to-{args.to_date}"
    approved_names = selected_source_names(args.sources)

    require_live_sources()
    with Storage(database_path) as storage, TdlibSource(telegram_source) as telegram:
        resolved = []
        for approved_name in approved_names:
            source = telegram.resolve_exact_source(approved_name)
            storage.upsert_source(approved_name, source)
            resolved.append((approved_name, source))

        for run_id, purpose in (
            (primary_run_id, f"{period_label}-primary"),
            (reconciliation_run_id, f"{period_label}-reconciliation"),
        ):
            storage.begin_run(run_id, purpose, start, end)
            try:
                for _, source in resolved:
                    result = telegram.collect_chat(
                        storage=storage,
                        run_id=run_id,
                        source=source,
                        start=start,
                        end=end,
                    )
                    status = (
                        "complete"
                        if result.ended_before_start or result.history_exhausted
                        else "incomplete"
                    )
                    storage.finish_source(run_id, result, status=status)
                    if status != "complete":
                        raise RuntimeError(
                            f"Collection did not prove the lower boundary for chat_id={source.chat_id}"
                        )
            except BaseException as exc:
                storage.finish_run(run_id, "failed", str(exc))
                raise
            storage.finish_run(run_id, "complete")

        comparisons = compare_runs(storage, primary_run_id, reconciliation_run_id)
        all_sources_match = bool(comparisons["all_sources_match"])
        if not all_sources_match:
            raise RuntimeError("Reconciliation did not produce the same message identifiers")

        evaluations = evaluate_rows(storage.messages_for_run(primary_run_id))
        storage.replace_evaluations(primary_run_id, evaluations)
        for chat_id, message_id in storage.candidate_keys(primary_run_id):
            link = telegram.get_message_link(chat_id, message_id)
            if link:
                storage.set_message_link(primary_run_id, chat_id, message_id, link)
        storage.commit()

        markdown_path, json_path = write_reports(
            storage,
            primary_run_id=primary_run_id,
            reconciliation_run_id=reconciliation_run_id,
            comparison=comparisons,
            reports_dir=reports_dir,
        )
        summary = storage.run_summary(primary_run_id)

    output = {
        "status": "complete",
        "version": __version__,
        "database": str(database_path),
        "preview_report": str(markdown_path),
        "collection_report": str(json_path),
        "primary_run_id": primary_run_id,
        "reconciliation_run_id": reconciliation_run_id,
        "all_sources_match": all_sources_match,
        "sources": comparisons["sources"],
        "decisions": summary["decisions"],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def rebuild_reports(args: argparse.Namespace) -> int:
    database_path = Path(args.database).expanduser().resolve()
    reports_dir = Path(args.reports_dir).expanduser().resolve()
    with Storage(database_path) as storage:
        comparisons = compare_runs(
            storage, args.primary_run_id, args.reconciliation_run_id
        )
        markdown_path, json_path = write_reports(
            storage,
            primary_run_id=args.primary_run_id,
            reconciliation_run_id=args.reconciliation_run_id,
            comparison=comparisons,
            reports_dir=reports_dir,
        )
    print(
        json.dumps(
            {
                "status": "complete",
                "preview_report": str(markdown_path),
                "collection_report": str(json_path),
                "all_sources_match": comparisons["all_sources_match"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def tune_selection(args: argparse.Namespace) -> int:
    digest_details = load_digest_details(
        Path(args.digest_details).expanduser().resolve() if args.digest_details else None
    )
    company_sizes = load_company_sizes(
        Path(args.company_sizes).expanduser().resolve() if args.company_sizes else None
    )
    database_path = Path(args.database).expanduser().resolve()
    reports_dir = Path(args.reports_dir).expanduser().resolve()
    feedback_path = Path(args.feedback).expanduser().resolve()
    telegram_source = Path(args.telegram_source).expanduser().resolve()
    with Storage(database_path) as storage:
        rows = storage.messages_for_run(args.run_id)
        with TdlibSource(telegram_source) as telegram:
            detail_stats = (
                DetailEnrichmentStats()
                if args.skip_detail_enrichment
                else enrich_run_details(
                    storage,
                    run_id=args.run_id,
                    rows=rows,
                    telegram=telegram,
                )
            )
            items = build_candidate_items(
                storage.messages_for_run(args.run_id), company_sizes=company_sizes,
                digest_details=digest_details,
            )
            feedback_result = (
                None
                if args.skip_feedback
                else evaluate_feedback(load_feedback(feedback_path), items)
            )
            storage.replace_candidate_items(args.run_id, items)
            for chat_id, message_id in storage.candidate_item_message_keys(args.run_id):
                link = telegram.get_message_link(chat_id, message_id)
                if link:
                    storage.set_candidate_item_link(args.run_id, chat_id, message_id, link)
        storage.commit()
        markdown_path, json_path = write_v2_reports(
            storage,
            run_id=args.run_id,
            feedback_result=feedback_result,
            reports_dir=reports_dir,
            report_prefix=args.report_prefix,
            detail_enrichment=detail_stats.as_dict(),
        )
        selection_summary = storage.candidate_item_summary(args.run_id)
    print(
        json.dumps(
            {
                "status": "complete",
                "version": __version__,
                "preview_report": str(markdown_path),
                "quality_report": str(json_path),
                "selection": selection_summary,
                "detail_enrichment": detail_stats.as_dict(),
                "feedback_check": feedback_result,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def export_project_inbox(args: argparse.Namespace) -> int:
    if args.limit is not None and args.limit < 1:
        raise ValueError("limit must be at least 1")
    database_path = Path(args.database).expanduser().resolve()
    output_path = (
        Path(args.output).expanduser().resolve()
        if args.output
        else INBOX_EXPORTS_DIR / f"{args.run_id}.json"
    )
    selected_types = set(args.types or ())
    with Storage(database_path) as storage:
        items = build_project_inbox_items(
            storage.report_candidate_items(args.run_id),
            run_id=args.run_id,
        )
        if selected_types:
            items = [item for item in items if item.item_type in selected_types]
        if args.limit is not None:
            items = items[: args.limit]
        storage.upsert_project_inbox_items(items)
        write_project_inbox_export(items, output_path)
    print(
        json.dumps(
            {
                "status": "complete",
                "version": __version__,
                "run_id": args.run_id,
                "items": len(items),
                "output": str(output_path),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="radar",
        description="Telegram Project Radar: collection, feedback and bounded CRM delivery",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    from .demo import command as demo_command
    demo = subparsers.add_parser("demo", help="offline walkthrough with invented messages; no credentials needed")
    demo.add_argument("--output-dir", default=".radar/demo")
    demo.set_defaults(handler=demo_command)
    run = subparsers.add_parser("run", help="collect, reconcile, and build a local preview")
    run.add_argument("--from-date", default=DEFAULT_FROM_DATE)
    run.add_argument("--to-date", default=DEFAULT_TO_DATE)
    run.add_argument("--timezone", default=DEFAULT_TIMEZONE)
    run.add_argument("--database", default=str(DATABASE_PATH))
    run.add_argument("--reports-dir", default=str(REPORTS_DIR))
    run.add_argument("--telegram-source", default=str(TELEGRAM_SEARCH_SOURCE))
    run.add_argument(
        "--source",
        action="append",
        dest="sources",
        help="collect one approved source; repeat to select more than one",
    )
    run.set_defaults(handler=run_pipeline)
    report = subparsers.add_parser("report", help="rebuild private reports from stored runs")
    report.add_argument("--primary-run-id", required=True)
    report.add_argument("--reconciliation-run-id", required=True)
    report.add_argument("--database", default=str(DATABASE_PATH))
    report.add_argument("--reports-dir", default=str(REPORTS_DIR))
    report.set_defaults(handler=rebuild_reports)
    tune = subparsers.add_parser("tune", help="build selector v2 from stored messages")
    tune.add_argument("--run-id", required=True)
    tune.add_argument("--feedback", default=str(FEEDBACK_PATH))
    tune.add_argument("--skip-feedback", action="store_true")
    tune.add_argument(
        "--digest-details",
        help="Local verified descriptions bound to exact digest messages and blocks",
    )
    tune.add_argument(
        "--company-sizes",
        help="JSON registry of verified company-wide headcounts (default: bundled facts)",
    )
    tune.add_argument("--report-prefix", default="august-2026")
    tune.add_argument("--database", default=str(DATABASE_PATH))
    tune.add_argument("--reports-dir", default=str(REPORTS_DIR))
    tune.add_argument("--telegram-source", default=str(TELEGRAM_SEARCH_SOURCE))
    tune.add_argument(
        "--skip-detail-enrichment",
        action="store_true",
        help="rebuild selection without reading linked vacancy pages",
    )
    tune.set_defaults(handler=tune_selection)
    inbox = subparsers.add_parser(
        "inbox", help="build a clean Project Inbox export from a selected run"
    )
    inbox.add_argument("--run-id", required=True)
    inbox.add_argument("--database", default=str(DATABASE_PATH))
    inbox.add_argument("--output")
    inbox.add_argument(
        "--type",
        action="append",
        dest="types",
        choices=("opportunity", "digest_item", "person", "company"),
        help="include one candidate type; repeat to include several",
    )
    inbox.add_argument("--limit", type=int)
    inbox.set_defaults(handler=export_project_inbox)
    add_feedback_commands(subparsers)
    add_morning_command(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except Exception as exc:
        print(f"radar: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

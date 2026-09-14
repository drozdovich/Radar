"""Local-only CLI for stage 2.6. No Telegram, CRM or model clients."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from . import __version__
from .feedback_features import make_snapshot, validate_snapshot
from .feedback_preview import build_preview
from .feedback_store import FeedbackStore, now, write_private


def read_json(path: str | Path):
    try:
        return json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("cannot read feedback input JSON; contents omitted for privacy") from None


def read_snapshots(paths: list[str]) -> list[dict]:
    snapshots = {}
    for path in paths:
        document = read_json(path)
        values = document.get("learning_snapshots", document.get("snapshots"))
        if values is None:
            # Old exports have no trustworthy immutable feature/rules snapshot.
            if str(document.get("schema_version", "")).startswith("project-inbox-"):
                continue
            raise ValueError("snapshot input requires snapshots or learning_snapshots")
        for value in values:
            checked = validate_snapshot(value)
            previous = snapshots.get(checked["snapshot_id"])
            if previous is None or checked["captured_at"] < previous["captured_at"]:
                snapshots[checked["snapshot_id"]] = checked
    return list(snapshots.values())


def read_events(path: str | None) -> list[dict]:
    if not path:
        return []
    value = read_json(path)
    rows = value if isinstance(value, list) else value.get("events")
    if not isinstance(rows, list):
        raise ValueError("decision export must be an array or an events object")
    return rows


def legacy_datasets(paths: list[str] | None = None) -> list[dict]:
    if paths:
        return [read_json(path) for path in paths]
    # Latest file per dataset; v1 remains intact, and is not counted alongside v2.
    selected = {}
    for path in sorted(Path(".radar/feedback").glob("*-review-v*.json")):
        value = read_json(path)
        number = int(re.search(r"-v(\d+)\.json$", path.name)[1])
        key = value["dataset_run_id"]
        if key not in selected or number > selected[key][0]:
            selected[key] = (number, value)
    return [v[1] for _, v in sorted(selected.items())]


def markdown_report(report: dict, version: str) -> str:
    blockers = {
        "no_proposals": "Нет конкретных предложений правил.",
        "incomplete_training_examples": "Есть примеры без причины, однозначного снимка или достаточного признака.",
        "approval_conflict": "Предложение затрагивает одобрение; оно отключено в безопасном preview.",
        "accumulated_legacy_regression_incomplete": "Не хватает снимков для проверки всей накопленной исторической разметки.",
        "regression_detected": "Обнаружена регрессия ранее подтверждённого решения.",
        "no_independent_new_days": "Нет независимых новых дней либо обнаружено пересечение с обучением.",
        "new_day_labels_incomplete_or_mismatched": "На новых днях нужны подтверждённые Approve и Reject без расхождений.",
        "no_training_approvals": "В обучающей разметке нет подтверждённых положительных примеров.",
    }
    lines = ["# Локальный feedback preview", "", f"Версия приложения: {__version__}", "",
             f"Версия отчёта: `{version}`", "", f"Разметка: `{report['label_revision']}`", "",
             "Production и Telegram не изменены. Улучшение качества на реальных данных не установлено.", "",
             f"Изменённых снимков: **{report['changed_count']}**. Сохранено одобрений: **{len(report['approvals_preserved'])}**.", "",
             "## Предложения", "", "| Правило | Scope | Точное условие | Действие | Конфликт с Approve |",
             "|---|---|---|---|---|"]
    for rule in report["proposals"]:
        lines.append(f"| `{rule['rule_id']}` | {rule['scope']} | `{json.dumps(rule['conditions'], ensure_ascii=False, sort_keys=True)}` | {rule['action']} {rule['score_delta']} | {len(rule['blocked_by_approvals'])} |")
    lines += ["", "Similar уменьшает score на 10 и сохраняет видимость. Штраф не суммируется от повторных примеров.", "",
              "## До и после", "", "| Кандидат | День | До | После | Причина изменения |", "|---|---|---|---|---|"]
    for row in report["impact"]:
        before, after = row["before"], row["after"]
        lines.append(f"| `{row['candidate_id']}` | {row['day']} | {before['decision']} / {before['score']} | {after['decision']} / {after['score']} | {', '.join(row['rules']) or 'без изменения'} |")
    lines += ["", "Сохранённые одобрения: " + (", ".join(f"`{v}`" for v in report["approvals_preserved"]) or "нет подтверждённых примеров"), "",
              "## Контроль применения", ""]
    lines += ["- " + blockers[b] for b in report["gate"]["blockers"]]
    if not report["gate"]["blockers"]:
        lines.append("Проверки разрешают только явный локальный пробный выбор этой версии.")
    for dataset in report["legacy_regression"]["datasets"]:
        lines.append(f"- Исторический набор `{dataset['run_id']}`: {dataset['total_labels']} решений; без снимков: {dataset['missing_snapshots']}; регрессий среди доступных: {len(dataset['regressions'])}.")
    lines += ["", f"Новые дни: {report['new_days']['candidate_count']} снимков, {report['new_days']['labelled_count']} решений; разделение с обучением: {report['new_days']['separate_new_days']}.", "",
              "## Примеры, из которых правило пока не строится", "",
              "| Кандидат | Что отсутствует или требует уточнения |", "|---|---|"]
    issues = {
        "missing_original_snapshot": "Не найден исходный снимок до решения с нужной версией правил.",
        "ambiguous_snapshot": "Найдено несколько разных снимков; нужна точная привязка.",
        "ambiguous_decision_order": "Разные решения имеют одинаковое время.",
        "missing_or_unknown_reason": "Причина отказа отсутствует или не распознана; она не додумывается.",
        "missing_or_unknown_scope": "Область действия отсутствует или не распознана.",
        "unverified_selector_version": "Не подтверждена исходная версия отбора.",
        "specific_feature_required": "Нет однозначного поддерживаемого признака для этой причины.",
        "second_similarity_feature_required": "Нужен второй явный признак сходства.",
    }
    for pending in report["pending"]:
        lines.append(f"| `{pending['candidate_id']}` | {issues[pending['reason']]} |")
    lines += ["", "Старые версии разметки и отчётов неизменяемы. `feedback-select` выбирает только локальную sandbox-версию; действующий selector её не читает. `feedback-rollback --to baseline` возвращает локальную базовую версию.", ""]
    return "\n".join(lines)


def save_report(store: FeedbackStore, report: dict, directory: Path) -> tuple[str, Path, Path]:
    version = store.save_bundle(report)
    json_path = write_private(directory / f"{version}.json", report)
    md_path = directory / f"{version}.md"
    text = markdown_report(report, version)
    if md_path.exists() and md_path.read_text() != text:
        raise ValueError("refusing to replace an existing preview")
    if not md_path.exists():
        import os
        with os.fdopen(os.open(md_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
            stream.write(text)
    return version, json_path, md_path


def import_command(args) -> dict:
    with FeedbackStore(Path(args.store).expanduser()) as store:
        revision = store.import_events(read_events(args.events), read_snapshots(args.snapshots or []), set(args.exclude_id or []))
        output = write_private(store.path.parent / "labels" / f"{revision['revision']}.json", revision)
    return {"revision": revision["revision"], "labels": len(revision["labels"]),
            "excluded_events": len(revision["excluded"]), "incomplete": sum(bool(l["issue"]) for l in revision["labels"]),
            "output": str(output.resolve())}


def preview_command(args) -> dict:
    with FeedbackStore(Path(args.store).expanduser()) as store:
        report = build_preview(store.revision(args.revision), read_snapshots(args.candidates or []),
                               holdout=read_snapshots(args.holdout or []), holdout_events=read_events(args.holdout_events),
                               legacy=legacy_datasets(args.legacy_feedback), selected_rules=args.rule)
        version, path, markdown = save_report(store, report, Path(args.output_dir).expanduser())
    return {"preview_version": version, "changed": report["changed_count"], "gate": report["gate"],
            "json": str(path.resolve()), "report": str(markdown.resolve())}


def corpus_command(args) -> dict:
    """Read saved candidate features for regression only, without trusting them as original history."""
    from .inbox import stable_candidate_id
    from .normalization import compact_text
    path = Path(args.database).expanduser().resolve()
    snapshots = []
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        for run in args.run_id:
            rows = connection.execute("""SELECT ci.*, m.sent_at FROM candidate_items ci
                JOIN messages m USING(chat_id,message_id) WHERE ci.run_id=?""", (run,)).fetchall()
            if not rows:
                raise ValueError("requested regression run has no stored candidates")
            for row in rows:
                locator = {k: row[k] for k in ("chat_id", "message_id", "item_index")}
                first_line = next((l for l in row["item_text"].splitlines() if l.strip()), "")
                snapshot = make_snapshot({"candidate_id": stable_candidate_id(**locator, fingerprint=row["fingerprint"]),
                    "run_id": run, "rules_version": "legacy-stored-unknown", "title": compact_text(first_line),
                    "summary": row["item_text"], "type": row["item_type"], "score": row["score"],
                    "selection_decision": row["decision"], "published_at": row["sent_at"], "locator": locator},
                    now(), selector_version="unknown")
                snapshots.append(snapshot)
    output = write_private(Path(args.output).expanduser(), {"snapshots": snapshots, "purpose": "regression_only"})
    return {"snapshots": len(snapshots), "output": str(output.resolve())}


def dispatch(args) -> int:
    try:
        if args.command == "feedback-import":
            output = import_command(args)
        elif args.command == "feedback-preview":
            output = preview_command(args)
        elif args.command == "feedback-corpus":
            output = corpus_command(args)
        elif args.command == "feedback-demo":
            from .feedback_demo import run_demo
            output = run_demo(Path(args.output_dir).expanduser())
        else:
            with FeedbackStore(Path(args.store).expanduser()) as store:
                output = store.select_local(args.to, rollback=args.command == "feedback-rollback")
    except (KeyError, TypeError, AttributeError, sqlite3.Error):
        raise ValueError("invalid or inaccessible feedback data; contents omitted for privacy") from None
    print(json.dumps({"version": __version__, "production_changed": False, **output}, ensure_ascii=False, indent=2))
    return 0


def add_feedback_commands(subparsers):
    for name in ("import", "preview", "select", "rollback", "corpus", "demo"):
        parser = subparsers.add_parser("feedback-" + name, help=f"local feedback {name}; no production writes")
        parser.set_defaults(handler=dispatch)
        if name not in {"corpus", "demo"}:
            parser.add_argument("--store", default=".radar/feedback/ledger.sqlite3")
        if name == "import":
            parser.add_argument("--events", required=True)
            parser.add_argument("--snapshots", action="append")
            parser.add_argument("--exclude-id", action="append", help="known test candidate or record ID; permanent exclusion")
        elif name == "preview":
            parser.add_argument("--revision", help="immutable label revision; defaults to latest")
            parser.add_argument("--candidates", action="append")
            parser.add_argument("--holdout", action="append")
            parser.add_argument("--holdout-events")
            parser.add_argument("--legacy-feedback", action="append", help="defaults to latest revision of every repository dataset")
            parser.add_argument("--rule", action="append", help="explicit proposal subset")
            parser.add_argument("--output-dir", default=".radar/feedback/previews")
        elif name in {"select", "rollback"}:
            parser.add_argument("--to", required=True, help="preview version, or baseline")
            if name == "select":
                parser.add_argument("--confirm-local", action="store_true", required=True, help="explicit sandbox selection only")
        elif name == "corpus":
            parser.add_argument("--database", default=".radar/radar.sqlite3")
            parser.add_argument("--run-id", action="append", required=True)
            parser.add_argument("--output", required=True)
        else:
            parser.add_argument("--output-dir", default=".radar/feedback-demo")

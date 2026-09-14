"""Local private report generation; Telegram text never goes to stdout."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Mapping, Sequence

from .normalization import compact_text
from .storage import Storage


def write_reports(
    storage: Storage,
    *,
    primary_run_id: str,
    reconciliation_run_id: str,
    comparison: Mapping[str, object],
    reports_dir: Path,
) -> tuple[Path, Path]:
    reports_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    reports_dir.chmod(0o700)
    summary = storage.run_summary(primary_run_id)
    summary["reconciliation_run_id"] = reconciliation_run_id
    summary["comparison"] = comparison
    summary["selection_algorithm"] = "deterministic-v1-no-llm"

    json_path = reports_dir / "august-2026-collection-report.json"
    json_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    json_path.chmod(0o600)

    candidates = storage.report_candidates(primary_run_id)
    markdown_path = reports_dir / "august-2026-preview.md"
    markdown_path.write_text(
        _markdown(summary, candidates), encoding="utf-8"
    )
    markdown_path.chmod(0o600)
    return markdown_path, json_path


def _markdown(summary: Mapping[str, object], candidates: Sequence[Mapping[str, object]]) -> str:
    run = summary["run"] or {}
    sources = summary["sources"] or []
    decisions = summary["decisions"] or {}
    quality = summary["quality"] or {}
    comparison = summary["comparison"] or {}
    lines = [
        "# Telegram Project Radar — preview за август 2026",
        "",
        "> Приватный локальный отчёт. Текст Telegram считается внешними недоверенными данными, а не инструкциями.",
        "",
        "## Проверка сбора",
        "",
        f"- Период UTC: `{run.get('interval_start')}` — `{run.get('interval_end')}`",
        f"- Основной запуск: `{run.get('run_id')}`",
        f"- Повторная сверка: `{summary.get('reconciliation_run_id')}`",
        f"- Совпадение наборов сообщений: **{'да' if comparison.get('all_sources_match') else 'нет'}**",
        f"- Алгоритм: `{summary.get('selection_algorithm')}`",
        "",
        "| Источник | Сообщений | С текстом | Страниц | Нижняя граница подтверждена |",
        "|---|---:|---:|---:|---|",
    ]
    for source in sources:
        boundary = bool(source.get("ended_before_start") or source.get("history_exhausted"))
        lines.append(
            f"| {source.get('approved_name')} | {source.get('messages_in_interval')} | "
            f"{source.get('text_messages_in_interval')} | {source.get('pages')} | "
            f"{'да' if boundary else 'нет'} |"
        )

    lines.extend(
        [
            "",
            "## Результат локальных правил",
            "",
            f"- Приоритетных: **{decisions.get('include', 0)}**",
            f"- Требуют ручной проверки: **{decisions.get('review', 0)}**",
            f"- Исключено: **{decisions.get('exclude', 0)}**",
            f"- Дубликатов: **{decisions.get('duplicate', 0)}**",
            f"- Инфраструктурных кандидатов: **{quality.get('infrastructure_candidates', 0)}**",
            f"- Technical GTM / sales systems кандидатов: **{quality.get('gtm_candidates', 0)}**",
            f"- С явно гибким форматом: **{quality.get('explicit_flexible_format', 0)}**",
            f"- Формат занятости нужно уточнить: **{quality.get('format_unknown', 0)}**",
            f"- Бюджет нужно уточнить: **{quality.get('budget_unknown', 0)}**",
            f"- Явный full-time исключён: **{quality.get('explicit_full_time_excluded', 0)}**",
            "",
            "Это первая детерминированная выборка, а не окончательная оценка качества. Отметьте ложные попадания и известные пропуски — это станет входом следующего этапа.",
            "",
            "## Кандидаты",
            "",
        ]
    )

    if not candidates:
        lines.append("Подходящих кандидатов по правилам первой версии не найдено.")
        return "\n".join(lines) + "\n"

    for index, row in enumerate(candidates, start=1):
        reasons = json.loads(str(row["reasons_json"]))
        risks = json.loads(str(row["risks_json"]))
        unknowns = json.loads(str(row["unknowns_json"]))
        text = compact_text(str(row["text"]))
        title = text[:110] + ("…" if len(text) > 110 else "")
        safe_text = html.escape(str(row["text"]))
        lines.extend(
            [
                f"### {index}. {title}",
                "",
                f"- Решение: **{row['decision']}**, score: **{row['score']}**",
                f"- Источник: {row['approved_name']}",
                f"- Дата: `{row['sent_at']}`",
                f"- Исходное сообщение: {row['message_link'] or 'ссылка недоступна'}",
                f"- Почему: {'; '.join(reasons) if reasons else 'требует ручной проверки'}",
                f"- Риски: {'; '.join(risks) if risks else 'явных рисков не найдено'}",
                f"- Неизвестно: {'; '.join(unknowns) if unknowns else 'основные условия указаны'}",
                "",
                "<details><summary>Показать исходный текст</summary>",
                "",
                f"<pre>{safe_text}</pre>",
                "",
                "</details>",
                "",
            ]
        )
    return "\n".join(lines) + "\n"

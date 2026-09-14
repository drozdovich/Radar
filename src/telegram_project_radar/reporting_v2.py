"""Private, type-separated report for selector version 2."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Mapping, Sequence

from .normalization import compact_text
from .storage import Storage


TYPE_NAMES = {
    "opportunity": "Отдельные проекты и вакансии",
    "digest_item": "Позиции, выделенные из дайджестов",
    "person": "Люди для внимания",
    "company": "Компании для исследования",
}


def write_v2_reports(
    storage: Storage,
    *,
    run_id: str,
    feedback_result: Mapping[str, object] | None,
    reports_dir: Path,
    report_prefix: str = "august-2026",
    detail_enrichment: Mapping[str, int] | None = None,
) -> tuple[Path, Path]:
    if re.fullmatch(r"[a-z0-9][a-z0-9-]*", report_prefix) is None:
        raise ValueError("report prefix must contain only lowercase letters, numbers, and hyphens")
    reports_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    reports_dir.chmod(0o700)
    collection = storage.run_summary(run_id)
    selection = storage.candidate_item_summary(run_id)
    summary = {
        "version": "deterministic-v2-no-llm",
        "run": collection["run"],
        "sources": collection["sources"],
        "selection": selection,
        "detail_enrichment": dict(detail_enrichment or {}),
        "feedback_check": dict(feedback_result) if feedback_result is not None else None,
        "quality_interpretation": (
            f"Calibration on the same {feedback_result.get('reviewed', 0)} user-reviewed messages; this is not an "
            "independent accuracy estimate for new messages."
            if feedback_result is not None
            else "Unseen-data check with no prior user labels applied."
        ),
    }
    json_path = reports_dir / f"{report_prefix}-quality-v2.json"
    json_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    json_path.chmod(0o600)

    items = storage.report_candidate_items(run_id)
    markdown_path = reports_dir / f"{report_prefix}-preview-v2.md"
    markdown_path.write_text(_markdown(summary, items), encoding="utf-8")
    markdown_path.chmod(0o600)
    return markdown_path, json_path


def _markdown(summary: Mapping[str, object], items: Sequence[Mapping[str, object]]) -> str:
    selection = summary["selection"]
    feedback = summary["feedback_check"]
    run = summary.get("run") or {}
    sources = summary.get("sources") or []
    detail_enrichment = summary.get("detail_enrichment") or {}
    collected = sum(int(source.get("messages_in_interval", 0)) for source in sources)
    text_messages = sum(int(source.get("text_messages_in_interval", 0)) for source in sources)
    lines = [
        "# Telegram Project Radar — preview v2",
        "",
        "> Приватный локальный отчёт. Версия 2 сначала определяет тип сообщения, затем оценивает профессиональное соответствие.",
        "",
        "## Период и сбор",
        "",
        f"- Период UTC: `{run.get('interval_start')}` — `{run.get('interval_end')}`",
        f"- Собрано сообщений: **{collected}**, из них с текстом или подписью: **{text_messages}**",
        f"- Идентификатор запуска: `{run.get('run_id')}`",
        "",
        "## Что изменилось",
        "",
        "- Проекты/вакансии и профессиональные контакты показаны отдельно.",
        "- Рекламные и редакционные публикации не получают score только за ключевые слова.",
        "- `Infrastructure` требует технического контекста.",
        "- Длинные дайджесты разобраны на отдельные позиции и не показаны целиком.",
        "- Похожие позиции из дайджеста скрываются, если уже найдена отдельная публикация.",
        "- Для RVC полное описание читается только по прямой allowlisted-ссылке из Telegram и сохраняется в локальный кэш.",
        "",
        "## Проверка полных описаний",
        "",
        f"- Карточек, для которых нужна проверка: **{detail_enrichment.get('targeted_messages', 0)}**",
        f"- Новых страниц загружено: **{detail_enrichment.get('fetched', 0)}**",
        f"- Взято из локального кэша: **{detail_enrichment.get('cache_hits', 0)}**",
        f"- Ошибок загрузки: **{detail_enrichment.get('failed', 0)}**",
        f"- Ранее подтверждённых недоступных страниц: **{detail_enrichment.get('cached_failures', 0)}**",
        f"- Без разрешённой прямой ссылки: **{detail_enrichment.get('messages_without_allowed_url', 0)}**",
        "",
    ]

    if feedback is None:
        lines.extend(
            [
                "## Независимый контрольный прогон",
                "",
                "- Эти сообщения не использовались при настройке правил v2.",
                "- Предыдущая пользовательская разметка к ним не применялась.",
                "- Качество определяется только после ручной проверки этой подборки.",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "## Контроль по вашей разметке",
                "",
                f"- Совпало с ожидаемым поведением: **{feedback.get('matched', 0)} из {feedback.get('reviewed', 0)}**",
                f"- Осталось расхождений: **{feedback.get('mismatched', 0)}**",
                f"- Номера расхождений из старого отчёта: `{', '.join(str(row['report_no']) for row in feedback.get('mismatches', [])) or 'нет'}`",
                f"- Это калибровка на тех же {feedback.get('reviewed', 0)} уже просмотренных сообщениях, а не независимая оценка точности на новых данных.",
                "",
            ]
        )

    lines.extend(
        [
            "## Объём новой подборки",
            "",
            f"- Отдельных проектов/вакансий: **{selection.get('by_type', {}).get('opportunity', 0)}**",
            f"- Позиций из дайджестов: **{selection.get('by_type', {}).get('digest_item', 0)}**",
            f"- Людей для внимания: **{selection.get('by_type', {}).get('person', 0)}**",
            f"- Компаний для исследования: **{selection.get('by_type', {}).get('company', 0)}**",
            f"- Элементов с прямой ссылкой: **{selection.get('items_with_links', 0)}**",
            "",
        ]
    )

    number = 0
    for item_type in ("opportunity", "digest_item", "person", "company"):
        section = [row for row in items if row["item_type"] == item_type]
        lines.extend([f"## {TYPE_NAMES[item_type]}", ""])
        if not section:
            lines.extend(["В этой категории ничего не найдено.", ""])
            continue
        for row in section:
            number += 1
            reasons = json.loads(str(row["reasons_json"]))
            risks = json.loads(str(row["risks_json"]))
            unknowns = json.loads(str(row["unknowns_json"]))
            item_text = str(row["item_text"])
            detail_url = str(row["detail_url"]) if row["detail_url"] else None
            detail_status = str(row["detail_status"]) if row["detail_status"] else None
            title = compact_text(item_text)[:110]
            if len(compact_text(item_text)) > 110:
                title += "…"
            lines.extend(
                [
                    f"### V2-{number}. {title}",
                    "",
                    f"- Тип: **{item_type}**; решение: **{row['decision']}**; score: **{row['score']}**",
                    f"- Источник: {row['approved_name']}",
                    f"- Дата: `{row['sent_at']}`",
                    f"- Исходное сообщение: {row['message_link'] or 'ссылка недоступна'}",
                    (
                        f"- Полное описание: [проверено и сохранено локально]({detail_url})"
                        if detail_url
                        else f"- Полное описание: {detail_status or 'не требовалось или ссылка недоступна'}"
                    ),
                    f"- Почему: {'; '.join(reasons)}",
                    f"- Риски: {'; '.join(risks) if risks else 'явных рисков не найдено'}",
                    f"- Неизвестно: {'; '.join(unknowns) if unknowns else 'основные условия указаны или неприменимы'}",
                    "",
                    "<details><summary>Показать соответствующий текст</summary>",
                    "",
                    f"<pre>{html.escape(item_text)}</pre>",
                    "",
                    "</details>",
                    "",
                ]
            )
    return "\n".join(lines) + "\n"

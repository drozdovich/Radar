"""Explainable deterministic candidate selection for the first preview."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import replace
from typing import Iterable, Mapping

from .models import Evaluation


FULL_TIME = (
    "full-time", "full time", "fulltime", "полная занятость",
    "полный рабочий день", "полный день", "tempo pieno", "tiempo completo",
)
OPPORTUNITY = (
    "вакан", "ищем", "требуется", "нужен", "нужна", "нужно", "задач",
    "проект", "работа", "подработ", "контракт", "фриланс", "консалт",
    "hiring", "looking for", "we need", "job", "role", "position", "project",
    "contract", "freelance", "consult", "opportunit", "cerchiamo", "lavoro",
    "buscamos", "necesitamos", "oferta", "colaboraci",
)
INFRASTRUCTURE = (
    "hosting", "хостинг", "hoster", "telecom", "телеком", "connectivity",
    "data center", "datacenter", "дата-центр", "дата центр", "colocation",
    "cloud", "облак", "server", "сервер", "vps", "network", "сетев",
    "devops", "sre", "sysadmin", "системн админ", "infrastructure", "инфраструктур",
    "kubernetes", "docker", "terraform", "ansible", "linux", "cdn", "edge",
    "waf", "ddos", "dns", "smtp", "monitoring", "мониторинг", "reliability",
    "routing", "маршрутиз", "ip transit", "managed service", "msp",
)
TECHNICAL_GTM = (
    "technical sales", "техническ продаж", "sales engineer", "pre-sales", "presales",
    "go-to-market", "go to market", "gtm", "market research", "исследован рынка",
    "lead sourcing", "лидоген", "enrichment", "обогащен", "qualification",
    "квалификац", "outbound", "crm", "hubspot", "apollo", "clay", "waalaxy",
    "sales automation", "автоматизац продаж", "business development", "partnership",
    "партнерств", "key account", "pipeline", "воронк", "customer discovery",
)
FLEXIBLE_FORMAT = (
    "part-time", "part time", "неполная занятость", "частичная занятость",
    "freelance", "фриланс", "contract", "контракт", "consult", "консалт",
    "project", "проектн", "разовая", "разово", "почас", "fractional",
)
REMOTE = ("remote", "удален", "удалён", "remoto", "da remoto")
BUDGET_RE = re.compile(r"(?:[$€£₽]|\b(?:usd|eur|rub|gbp)\b)\s*\d|\d\s*(?:[$€£₽]|usd|eur|rub|gbp)", re.I)
SPANISH_ADVANCED_RE = re.compile(r"(?:español|spanish).{0,35}(?:b2|c1|c2|fluido|fluent|nativo|native)", re.I | re.S)


def fold(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")


def contains_any(text: str, terms: Iterable[str]) -> bool:
    return any(term in text for term in terms)


def fingerprint(text: str) -> str:
    value = fold(text)
    value = re.sub(r"https?://\S+|t\.me/\S+|@[\w_]+|\S+@\S+", " ", value)
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    value = " ".join(value.split())
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def evaluate(text: str) -> Evaluation:
    value = fold(text)
    has_full_time = contains_any(value, FULL_TIME)
    has_opportunity = contains_any(value, OPPORTUNITY)
    has_infrastructure = contains_any(value, INFRASTRUCTURE)
    has_gtm = contains_any(value, TECHNICAL_GTM)
    has_flexible = contains_any(value, FLEXIBLE_FORMAT)

    score = 0
    reasons: list[str] = []
    risks: list[str] = []
    unknowns: list[str] = []

    if has_opportunity:
        score += 25
    if has_infrastructure:
        score += 45
        reasons.append("Профильная инфраструктурная область")
    if has_gtm:
        score += 45
        reasons.append("Профильная technical GTM / sales systems задача")
    if has_flexible:
        score += 15
        reasons.append("Указан проектный или гибкий формат")
    if contains_any(value, REMOTE):
        score += 5

    if has_full_time:
        return Evaluation(
            score=0,
            decision="exclude",
            reasons=("Явно указан full-time",),
            risks=(),
            unknowns=(),
            fingerprint=fingerprint(text),
        )

    if not has_opportunity:
        decision = "exclude"
    elif (has_infrastructure or has_gtm) and score >= 70:
        decision = "include"
    elif has_infrastructure or has_gtm:
        decision = "review"
    else:
        decision = "exclude"

    if decision in {"include", "review"}:
        if not has_flexible:
            unknowns.append("Неясно, допускается ли part-time / contract / freelance")
        if not BUDGET_RE.search(text):
            unknowns.append("Не указан бюджет или ставка")
        if not contains_any(value, REMOTE) and not contains_any(value, ("onsite", "on-site", "hybrid", "гибрид", "офис")):
            unknowns.append("Неясен формат remote / onsite / hybrid")
        if not re.search(r"\b\d+\s*(?:h|hours?|час|часов|дн|дней|days?)\b", value):
            unknowns.append("Неясна ожидаемая загрузка")
        if SPANISH_ADVANCED_RE.search(text):
            risks.append("Требуется испанский выше basic")
        if "hybrid" in value or "onsite" in value or "on-site" in value or "гибрид" in value or "офис" in value:
            risks.append("Локацию onsite / hybrid нужно проверить: допустима только Испания")

    return Evaluation(
        score=min(score, 100),
        decision=decision,
        reasons=tuple(reasons),
        risks=tuple(risks),
        unknowns=tuple(unknowns),
        fingerprint=fingerprint(text),
    )


def evaluate_rows(rows: Iterable[Mapping[str, object]]) -> list[tuple[int, int, Evaluation]]:
    results: list[tuple[int, int, Evaluation]] = []
    seen: set[str] = set()
    for row in rows:
        text = str(row["text"])
        result = evaluate(text)
        if result.decision in {"include", "review"}:
            if result.fingerprint in seen:
                result = replace(
                    result,
                    decision="duplicate",
                    reasons=result.reasons + ("Дубликат уже найденного сообщения",),
                )
            else:
                seen.add(result.fingerprint)
        results.append((int(row["chat_id"]), int(row["message_id"]), result))
    return results

"""Company-wide headcount facts checked by an operator against public sources.

No network access or inference from job descriptions: missing facts stay unknown.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

from .models import Evaluation

MAX_COMPANY_EMPLOYEES = 200
MAX_FACT_AGE_DAYS = 180
DEFAULT_COMPANY_SIZES = Path(__file__).with_name("company_sizes.json")


def company_key(name: str) -> str:
    # Exact company/alias only; never match a vendor mentioned in a skills list.
    return " ".join(name.casefold().split())


@dataclass(frozen=True)
class CompanySize:
    company: str
    minimum: int
    maximum: int | None
    source_url: str
    source_excerpt: str
    checked_on: date

    def status(self, on_date: date) -> str:
        if not 0 <= (on_date - self.checked_on).days <= MAX_FACT_AGE_DAYS:
            return "stale"
        if self.minimum > MAX_COMPANY_EMPLOYEES:
            return "too_large"
        if self.maximum is not None and self.maximum <= MAX_COMPANY_EMPLOYEES:
            return "within_limit"
        return "uncertain"

    def citation(self) -> str:
        size = (
            str(self.minimum) if self.maximum == self.minimum
            else f"{self.minimum}–{self.maximum}" if self.maximum is not None
            else f"не менее {self.minimum}"
        )
        return (
            f"Численность компании {self.company}: {size} сотрудников; "
            f"источник: {self.source_url}; проверено {self.checked_on.isoformat()}"
        )


def load_company_sizes(path: Path | None = None) -> dict[str, CompanySize]:
    payload = json.loads((path or DEFAULT_COMPANY_SIZES).read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("companies"), list):
        raise ValueError("Unsupported company-size registry")
    result: dict[str, CompanySize] = {}
    for row in payload["companies"]:
        minimum, maximum = row["employees_min"], row["employees_max"]
        if (
            type(minimum) is not int or minimum < 0
            or (maximum is not None and (type(maximum) is not int or maximum < minimum))
            or row.get("scope") != "company"
        ):
            raise ValueError("Company-wide employee bounds required")
        url = urlsplit(row["source_url"])
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("Public HTTPS source required")
        for field in ("company", "source_excerpt"):
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise ValueError(f"Missing company-size {field}")
        fact = CompanySize(
            row["company"], minimum, maximum, row["source_url"],
            row["source_excerpt"], date.fromisoformat(row["checked_on"]),
        )
        aliases = row.get("aliases", [])
        if not isinstance(aliases, list):
            raise ValueError("Company aliases must be a list")
        for name in [fact.company, *aliases]:
            if not isinstance(name, str) or not company_key(name):
                raise ValueError("Empty company name or alias")
            key = company_key(name)
            if key in result:
                raise ValueError("Ambiguous company name or alias")
            result[key] = fact
    return result


def apply_company_size(
    evaluation: Evaluation,
    company: str,
    facts: Mapping[str, CompanySize],
    *,
    on_date: date | None = None,
) -> Evaluation:
    fact = facts.get(company_key(company))
    status = fact.status(on_date or date.today()) if fact else "unknown"
    if fact and status == "too_large":
        return replace(
            evaluation, score=0, decision="exclude",
            reasons=evaluation.reasons + (
                f"Компания больше {MAX_COMPANY_EMPLOYEES} сотрудников — исключена. {fact.citation()}",
            ),
        )
    if evaluation.decision not in {"include", "review"}:
        return evaluation
    if fact and status == "within_limit":
        return replace(evaluation, reasons=evaluation.reasons + (fact.citation(),))
    unknown = "Размер компании неизвестен: нужно проверить численность всей компании"
    if not company:
        unknown += "; работодатель не указан явно"
    elif fact:
        why = "сведения требуют обновления" if status == "stale" else "диапазон не позволяет проверить порог 200"
        unknown += f"; {why}. {fact.citation()}"
    return replace(evaluation, unknowns=evaluation.unknowns + (unknown,))

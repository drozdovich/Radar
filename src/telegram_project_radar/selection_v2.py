"""Second deterministic selector: classify the object before scoring its fit."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Iterable, Mapping

from .models import CandidateItem, Evaluation
from .company_size import CompanySize, apply_company_size, load_company_sizes
from .digest_details import DigestDetails, text_hash, validate_digest_sources
from .normalization import compact_text
from .selection import BUDGET_RE, FLEXIBLE_FORMAT, FULL_TIME, REMOTE, fingerprint, fold


HIRING = (
    "ищем", "требуется", "нужен", "нужна", "нужны", "вакансия", "вакансія",
    "hiring", "looking for a", "looking for an", "we need", "vacancy", "position open",
    "cerchiamo", "buscamos", "necesitamos",
)
PERSON = (
    "я работаю", "работал в", "работала в", "мой опыт", "моя специализац",
    "занимаюсь", "открыт к", "открыта к", "буду рад", "буду рада", "ищу проект",
    "ищу работу", "ищу новую роль", "интересны проекты", "готов подключ",
    "i work", "i have worked", "my experience", "open to", "looking for a role",
    "looking for projects", "available for",
)
COLLABORATION = (
    "проект", "открыт к", "открыта к", "буду рад", "буду рада", "готов подключ",
    "пообщ", "знаком", "сотруднич", "консалт", "фриланс", "подработ",
    "open to", "available", "project", "collaborat", "consult", "freelance",
)
ADVERTISEMENT = (
    "наш оффер", "наши услуги", "наш сервис", "мы помогаем", "поможем вам",
    "подбор вакансий", "карьерная консультац", "записывайтесь", "оставьте заявку",
    "оставляйте заявку", "переходите по ссылке", "подписывайтесь", "почему выбирают нас",
    "our service", "our offer", "book a call", "sign up", "apply to our program",
)
NEGATIVE_MARKETING = (
    "performance marketing", "meta ads", "google ads", "таргет", "affiliate",
    "аффилиат", "видеомонтаж", "video production", "контент-мейкер", "content creator",
)
OUT_OF_SCOPE_TOOLS = (
    "salesforce", "azure", "mlops", "ml ops", "machine learning operations",
)
EXCLUDED_MARKETS = (
    "russia", "росси", "russian federation", "moscow", "москва", "india", "индия",
)
# A bounded list from the confirmed N8 case, not a general city geocoder.
RUSSIAN_CITY_RE = re.compile(
    r"(?<!\w)(?:калуг[аеуи]|орел|орл[аеу]|нижн(?:ий|ем|его)\s+новгород[аеу]?|"
    r"тул[аеуы]|kaluga|oryol|orel|nizhniy\s+novgorod|nizhny\s+novgorod|tula)(?!\w)"
)
LOCATION_CONTEXT = (
    "город", "формат", "локац", "место работы", "location", "based in",
    "гибрид", "hybrid", "onsite", "on-site", "офис", "office",
    "remote", "удален", "relocation", "релокац",
)
EXCLUDED_EMPLOYERS = ("mts", "мтс")
EXCLUDED_JOB_LOCATIONS = ("san francisco",)
EXCLUDED_ROLE_LEVELS = ("staff", "cto", "chief technology officer")
EXCLUDED_ROLE_TITLES = ("data posture monitoring manager", "editor coordinator")
LOW_PRIORITY_JOB_INDUSTRIES = ("education",)
RUB_RE = re.compile(r"(?:\brub\b|₽|\bруб(?:\.|л(?:ей|я)?|лей)?\b)", re.IGNORECASE)
ONSITE_FORMAT = ("office", "офис", "onsite", "on-site", "hybrid", "гибрид")
REQUIRED_RELOCATION = (
    "обязателен релок", "обязательна релокац", "обязательный релок",
    "must relocate", "relocation required", "mandatory relocation",
)
SPAIN_LOCATIONS = (
    "spain", "испания", "madrid", "мадрид", "barcelona", "барселона",
    "valencia", "валенсия", "malaga", "малага", "alicante", "аликанте",
    "sevilla", "seville", "bilbao",
)
COMPANY_ROLE_SIGNALS = (
    "devops", "sre", "site reliability", "network engineer", "monitoring",
    "infrastructure", "implementation and support", "implementation support",
)
COMPANY_RESEARCH_ROLE_SIGNALS = COMPANY_ROLE_SIGNALS + ("vpn engineer",)
COMPANY_GTM_ROLE_SIGNALS = (
    "technical sales", "sales engineer", "pre-sales", "presales",
    "gtm platform", "go-to-market platform", "go to market platform",
    "lead generation", "lead sourcing", "enrichment", "outbound",
    "sales automation", "revenue operations", "revops",
)
COMPANY_MARKET_RESEARCH_CONTEXT = (
    "b2b", "saas", "software", "hosting", "cloud", "telecom",
    "telecommunications", "data center",
)
COMPANY_NETWORK_SIGNALS = (
    "network", "networks", "networking", "network_security", "tcp/ip", "tcp_ip",
    "openvpn", "wireguard",
)
COMPANY_SECTOR_EXCLUSIONS = (
    "healthcare", "health & fitness", "entertainment", "media",
)
PRIORITY_COMPANY_SECTORS = (
    "hosting", "telecom", "telecommunications", "data center", "cloud infrastructure",
    "cloud computing",
)
CONFIRMED_RESEARCH_COMPANY_NAMES = ("texode",)
INFINITY_COMPANY_NAMES = ("aya games", "ggr global")
SCAM = (
    "быстрый заработок", "без опыта и вложений", "гарантированный доход",
    "пассивный доход", "крипто-сигнал", "crypto signal",
)
DIGEST = ("подборка ваканс", "вакансии недели", "еженедельн", "weekly jobs", "job digest")

INFRA_STRONG = (
    "hosting", "хостинг", "telecom", "telecommunications", "телеком", "data center", "datacenter",
    "дата-центр", "дата центр", "colocation", "managed hosting", "msp",
    "server", "сервер", "vps", "network", "сетев", "devops", "sre", "sysadmin",
    "системн админ", "kubernetes", "terraform", "ansible", "linux", "cdn", "edge",
    "waf", "ddos", "dns", "smtp", "monitoring", "мониторинг", "reliability",
    "routing", "маршрутиз", "ip transit", "internet provider", "провайдер",
    "cloudflare", "techops", "backup", "бэкап", "резервн копир", "gt cloud", "gtcloud",
    "jettycloud", "incident response",
)
INFRA_GENERIC = ("infrastructure", "инфраструктур", "cloud", "облачн", "администрир", "поддержк")
TECHNICAL_CONTEXT = (
    "it", "software", "tech", "техническ", "server", "сервер", "network", "сетев",
    "system", "систем", "cloud", "облачн", "telecom", "телеком", "hosting", "хостинг",
    "devops", "database", "баз дан", "api", "automation", "автоматизац", "b2b", "saas",
)
GTM_PRECISE = (
    "technical sales", "sales engineer", "pre-sales", "presales", "техническ продаж",
    "go-to-market", "go to market", "gtm", "lead sourcing", "lead generation",
    "enrichment", "обогащен",
    "qualification", "квалификац", "outbound", "crm", "hubspot", "apollo", "clay",
    "waalaxy", "sales automation", "автоматизац продаж", "pipeline", "ворон",
    "customer discovery", "market research", "исследован рынка", "mcp",
)
GTM_GENERIC = (
    "sales manager", "менеджер по продаж", "business developer", "business development",
    "bizdev", "partnership", "партнерств", "key account",
)
ROLE_WORDS = (
    "engineer", "инженер", "manager", "менеджер", "architect", "архитектор",
    "specialist", "специалист", "consultant", "консультант", "developer", "разработчик",
    "administrator", "администратор", "designer", "дизайнер", "head", "lead",
    "support", "поддержк",
)


def _term_present(value: str, term: str) -> bool:
    if re.fullmatch(r"[a-z0-9_-]+", term):
        return re.search(rf"(?<![\w-]){re.escape(term)}(?![\w-])", value) is not None
    return term in value


def _hits(value: str, terms: Iterable[str]) -> tuple[str, ...]:
    return tuple(term for term in terms if _term_present(value, term))


def has_any(value: str, terms: Iterable[str]) -> bool:
    return any(_term_present(value, term) for term in terms)


def profile_fit(text: str) -> tuple[int, tuple[str, ...]]:
    value = fold(text)
    infra_hits = _hits(value, INFRA_STRONG)
    generic_hits = _hits(value, INFRA_GENERIC)
    technical = has_any(value, TECHNICAL_CONTEXT)
    infra_strength = 0
    if infra_hits:
        infra_strength = 2 if len(set(infra_hits)) == 1 else 3
    if generic_hits and technical:
        infra_strength = max(infra_strength, 2 if len(set(generic_hits)) >= 2 else 1)

    gtm_precise = _hits(value, GTM_PRECISE)
    gtm_generic = _hits(value, GTM_GENERIC)
    gtm_strength = min(3, len(set(gtm_precise)))
    if gtm_generic and technical:
        gtm_strength = max(gtm_strength, 1)

    reasons: list[str] = []
    if infra_strength:
        reasons.append("Подтверждён технический infrastructure/telecom/hosting контекст")
    if gtm_strength:
        reasons.append("Подтверждена technical GTM / sales systems задача")
    return max(infra_strength, gtm_strength), tuple(reasons)


def is_digest(text: str) -> bool:
    value = fold(text)
    lines = text.splitlines()
    structural = len(text) >= 2200 and len(lines) >= 35 and sum(not line.strip() for line in lines) >= 8
    return structural and ("вакан" in value or has_any(value, DIGEST))


def is_structured_vacancy(text: str) -> bool:
    value = fold(text)
    has_skills = has_any(value, ("skills:",))
    has_languages = has_any(value, ("требуемые языки:", "required languages:"))
    has_role = has_any(value, ROLE_WORDS)
    return (has_skills and has_languages) or ((has_skills or has_languages) and has_role)


def structured_profile_text(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines[:2])


def structured_vacancy_fields(text: str) -> tuple[str, str, str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return "", "", ""
    title = re.sub(r"^[^\w.]+", "", lines[0], flags=re.UNICODE).strip()
    company_line = re.sub(r"^[^\w.]+", "", lines[1], flags=re.UNICODE).strip()
    company, separator, industry = company_line.partition("|")
    if not separator:
        return title, "", company_line
    return title, company.strip(), industry.strip()


def needs_detail_enrichment(text: str) -> bool:
    if not is_structured_vacancy(text):
        return False
    _, _, industry = structured_vacancy_fields(text)
    if has_any(fold(industry), PRIORITY_COMPANY_SECTORS):
        return True
    return evaluate_item(text, "opportunity").decision in {"include", "review"}


def has_excluded_market(value: str) -> bool:
    if has_any(value, EXCLUDED_MARKETS):
        return True
    for clause in re.split(r"[\n.!?;]+", value):
        if RUSSIAN_CITY_RE.search(clause) and (
            has_any(clause, LOCATION_CONTEXT) or RUSSIAN_CITY_RE.fullmatch(clause.strip())
        ):
            return True
    return False


def is_foreign_onsite(value: str) -> bool:
    # Infrastructure/product names do not describe physical attendance.
    value = re.sub(r"\b(?:hybrid cloud|гибридн\w* облак\w*|microsoft office|office 365|openoffice)\b", "", value)
    if has_any(value, REMOTE):
        # A single mixed condition ("hybrid in X / remote elsewhere") is not
        # unrestricted remote. Do not reinterpret separate site-format lists.
        return any(
            has_any(line, ONSITE_FORMAT) and has_any(line, REMOTE)
            and not has_any(line, SPAIN_LOCATIONS)
            for line in value.splitlines()
        )
    return (
        has_any(value, ONSITE_FORMAT)
        and not has_any(value, SPAIN_LOCATIONS)
    )


def requires_foreign_relocation(value: str) -> bool:
    return has_any(value, REQUIRED_RELOCATION) and not has_any(value, SPAIN_LOCATIONS)


def build_company_item(
    chat_id: int, message_id: int, text: str, detail_text: str | None = None
) -> CandidateItem | None:
    if not is_structured_vacancy(text):
        return None
    title, company, industry = structured_vacancy_fields(text)
    if not company:
        return None

    combined_text = f"{text}\n{detail_text}" if detail_text else text
    value = fold(combined_text)
    card_value = fold(text)
    detail_value = fold(detail_text or "")
    if (
        has_excluded_market(value) or has_any(value, EXCLUDED_JOB_LOCATIONS)
        or RUB_RE.search(combined_text)
        # A research company is distinct from this vacancy: preserve the
        # previously accepted remote alternatives for company research.
        or (is_foreign_onsite(card_value) and not has_any(card_value, REMOTE))
        or bool(detail_text and is_foreign_onsite(detail_value) and not has_any(detail_value, REMOTE))
        or bool(detail_text and requires_foreign_relocation(detail_value))
    ):
        return None
    fit_text = "\n".join(part for part in (title, industry) if part)
    strength, _ = profile_fit(fit_text)
    title_value = fold(title)
    company_value = fold(company)
    industry_value = fold(industry)
    if has_any(company_value, EXCLUDED_EMPLOYERS):
        return None
    has_remote = has_any(card_value, REMOTE)
    profile_role = (
        has_any(title_value, COMPANY_RESEARCH_ROLE_SIGNALS)
        or has_any(title_value, COMPANY_GTM_ROLE_SIGNALS)
        or (
            has_any(title_value, ("market research", "market researcher"))
            and has_any(industry_value, COMPANY_MARKET_RESEARCH_CONTEXT)
        )
    )
    useful_hiring_signal = has_remote and profile_role
    priority_sector_company = has_remote and has_any(
        industry_value, PRIORITY_COMPANY_SECTORS
    )
    confirmed_company = has_remote and has_any(
        company_value, CONFIRMED_RESEARCH_COMPANY_NAMES
    )
    network_research = (
        has_remote
        and profile_role
        and has_any(card_value, COMPANY_NETWORK_SIGNALS)
    )
    monitoring_research = has_remote and "data posture monitoring" in title_value
    infinity_route = has_remote and has_any(company_value, INFINITY_COMPANY_NAMES)
    excluded_sector = has_any(industry_value, COMPANY_SECTOR_EXCLUSIONS)
    if excluded_sector and not (network_research or infinity_route):
        return None
    if not (
        useful_hiring_signal
        or priority_sector_company
        or confirmed_company
        or network_research
        or monitoring_research
        or infinity_route
    ):
        return None

    reasons: list[str] = []
    if useful_hiring_signal:
        reasons.append("Компания нанимает на профильную роль")
    if priority_sector_company:
        reasons.append("Компания работает в приоритетной infrastructure/telecom/cloud-области")
    if confirmed_company:
        reasons.append("Компания явно подтверждена пользователем для исследования")
    if network_research:
        reasons.append("Приоритет: top research — найден сильный network-сигнал")
    if monitoring_research:
        reasons.append("Компания указана явно; найден профильный monitoring-сигнал")
    if infinity_route:
        reasons.append("Будущий маршрут: Infinity (внешняя система пока не подключается)")
    summary = f"{company}\nОтрасль: {industry or 'не указана'}\nСигнал: нанимает {title}"
    if network_research:
        summary += "\nПриоритет: top research"
    if infinity_route:
        summary += "\nБудущий маршрут: Infinity"
    evaluation = Evaluation(
        90 if network_research else (85 if infinity_route else 80),
        "include",
        tuple(reasons),
        (),
        ("Нужно изучить продукт, рынок и потенциальный повод для контакта",),
        fingerprint(summary),
    )
    return CandidateItem(chat_id, message_id, 1, "company", summary, evaluation)


def split_digest(text: str) -> list[str]:
    blocks = [compact_text(block) for block in re.split(r"\n\s*\n+", text)]
    return [block for block in blocks if len(block) >= 35]


def classify_message(text: str) -> str:
    value = fold(text)
    if is_digest(text):
        return "digest"
    hiring = has_any(value, HIRING)
    person = has_any(value, PERSON)
    advertisement = has_any(value, ADVERTISEMENT)
    structured_vacancy = is_structured_vacancy(text)
    editorial_offer = (
        "оффер" in value
        and "почему" in value
        and has_any(value, ("hr", "карьер"))
    )
    if editorial_offer:
        return "advertisement"
    if advertisement and not hiring and not structured_vacancy:
        return "advertisement"
    if hiring or structured_vacancy:
        return "opportunity"
    if person:
        return "person"
    return "noise"


def evaluate_item(
    text: str, item_type: str, detail_text: str | None = None
) -> Evaluation:
    detail_text = detail_text.strip() if detail_text else None
    detail_value = fold(detail_text or "")
    combined_text = f"{text}\n{detail_text}" if detail_text else text
    value = fold(combined_text)
    item_fingerprint = fingerprint(text)
    if has_any(value, FULL_TIME):
        reason = (
            "В полном описании явно указан full-time"
            if detail_text and has_any(detail_value, FULL_TIME)
            else "Явно указан full-time"
        )
        return Evaluation(0, "exclude", (reason,), (), (), item_fingerprint)
    if has_excluded_market(value) or RUB_RE.search(combined_text):
        source = "в полном описании" if detail_text and (
            has_excluded_market(detail_value) or RUB_RE.search(detail_text)
        ) else "в короткой карточке"
        return Evaluation(
            0,
            "exclude",
            (f"{source.capitalize()} найдена привязка к России (включая российские города), India или оплате в RUB",),
            (),
            (),
            item_fingerprint,
        )
    if has_any(value, SCAM):
        return Evaluation(0, "exclude", ("Обнаружены scam-like формулировки",), (), (), item_fingerprint)
    if item_type in {"opportunity", "digest_item"} and has_any(value, OUT_OF_SCOPE_TOOLS):
        return Evaluation(
            0,
            "exclude",
            ("Инструмент явно исключён из профессионального профиля",),
            (),
            (),
            item_fingerprint,
        )
    if item_type == "advertisement":
        return Evaluation(0, "exclude", ("Рекламный или промо-пост без конкретной возможности",), (), (), item_fingerprint)
    if item_type in {"opportunity", "digest_item"} and has_any(value, EXCLUDED_JOB_LOCATIONS):
        return Evaluation(
            0,
            "exclude",
            ("Локация San Francisco исключена из поиска вакансий",),
            (),
            (),
            item_fingerprint,
        )

    structured_vacancy = is_structured_vacancy(text)
    title = company = industry = ""
    if structured_vacancy:
        title, company, industry = structured_vacancy_fields(text)
    fit_text = (
        "\n".join(part for part in (title, detail_text) if part)
        if structured_vacancy
        else combined_text
    )
    fit_value = fold(fit_text)
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    first_line_value = fold(re.sub(r"^[^\w.]+", "", first_line, flags=re.UNICODE))
    if (
        item_type in {"opportunity", "digest_item"}
        and has_any(first_line_value, EXCLUDED_ROLE_LEVELS + EXCLUDED_ROLE_TITLES)
    ):
        return Evaluation(
            0,
            "exclude",
            ("Уровень или тип роли явно исключён из текущего профиля",),
            (),
            (),
            item_fingerprint,
        )
    if structured_vacancy and item_type in {"opportunity", "digest_item"}:
        title_value = fold(title)
        company_value = fold(company)
        industry_value = fold(industry)
        if company and has_any(company_value, EXCLUDED_EMPLOYERS):
            return Evaluation(
                0,
                "exclude",
                ("Работодатель MTS/МТС исключён из текущего поиска",),
                (),
                (),
                item_fingerprint,
            )
        strong_reliability_role = has_any(
            title_value, ("sre", "site reliability", "network engineer")
        )
        if has_any(industry_value, LOW_PRIORITY_JOB_INDUSTRIES) and not strong_reliability_role:
            return Evaluation(
                0,
                "exclude",
                ("Отраслевая вакансия не входит в текущий приоритет",),
                (),
                (),
                item_fingerprint,
            )
    has_remote = has_any(value, REMOTE)
    if (
        item_type in {"opportunity", "digest_item"}
        and (
            is_foreign_onsite(fold(text))
            or bool(detail_text and is_foreign_onsite(detail_value))
        )
    ):
        return Evaluation(
            0,
            "exclude",
            ("Onsite/office/hybrid вне Испании или без подтверждённой локации в Испании",),
            (),
            (),
            item_fingerprint,
        )
    strength, profile_reasons = profile_fit(fit_text)
    if structured_vacancy and has_any(fit_value, COMPANY_ROLE_SIGNALS):
        if strength < 2:
            strength = 2
            profile_reasons = profile_reasons + (
                "Подтверждена профильная infrastructure/reliability-роль",
            )
    if (
        structured_vacancy
        and has_any(fit_value, GTM_PRECISE)
        and has_any(fold(f"{title}\n{industry}"), TECHNICAL_CONTEXT)
    ):
        strength = max(strength, 2)
    if has_any(value, NEGATIVE_MARKETING) and strength < 2:
        return Evaluation(0, "exclude", ("Непрофильный marketing/content контекст",), (), (), item_fingerprint)
    if strength == 0:
        return Evaluation(0, "exclude", ("Нет подтверждённого профессионального совпадения",), (), (), item_fingerprint)

    has_flexible = has_any(value, FLEXIBLE_FORMAT)
    reasons = list(profile_reasons)
    risks: list[str] = []
    unknowns: list[str] = []
    if detail_text:
        reasons.append("Полное описание по прямой ссылке проверено")

    if item_type == "person":
        if strength < 2:
            return Evaluation(
                20,
                "exclude",
                ("Для профессионального контакта найден только один слабый профильный сигнал",),
                (),
                (),
                item_fingerprint,
            )
        if not has_any(value, COLLABORATION):
            return Evaluation(
                45,
                "review",
                tuple(reasons + ["Профильный человек, но неясен повод для контакта"]),
                (),
                ("Неясно, открыт ли человек к проектам или знакомству",),
                item_fingerprint,
            )
        score = 65 + min(strength, 3) * 10
        decision = "include" if strength >= 2 else "review"
        reasons.append("Есть сигнал открытости к проектам или профессиональному контакту")
    elif item_type in {"opportunity", "digest_item"}:
        if item_type == "digest_item" and not has_any(value, ROLE_WORDS + HIRING):
            return Evaluation(20, "exclude", ("Блок дайджеста не похож на отдельную позицию",), (), (), item_fingerprint)
        weak_digest_sales = (
            item_type == "digest_item"
            and strength == 1
            and has_any(value, GTM_GENERIC)
        )
        flexible_gtm_task = (
            item_type == "opportunity"
            and strength == 1
            and has_flexible
            and has_any(fit_value, GTM_PRECISE)
        )
        if strength < 2 and not (weak_digest_sales or flexible_gtm_task):
            return Evaluation(
                20,
                "exclude",
                ("Профильный сигнал слишком слаб без дополнительного технического контекста",),
                (),
                (),
                item_fingerprint,
            )
        score = 60 + min(strength, 3) * 10 + (10 if has_flexible else 0)
        decision = "include" if strength >= 2 or flexible_gtm_task else "review"
        if item_type == "digest_item":
            reasons.append("Выделено как отдельная позиция из дайджеста")
        if has_flexible:
            reasons.append("Явно указан гибкий или проектный формат")
    else:
        return Evaluation(0, "exclude", ("Нет явного предложения или профильной самопрезентации",), (), (), item_fingerprint)

    if item_type in {"opportunity", "digest_item"} and not has_flexible:
        unknowns.append("Неясно, допускается ли part-time / contract / freelance")
    if item_type in {"opportunity", "digest_item"} and not BUDGET_RE.search(combined_text):
        unknowns.append("Не указан бюджет или ставка")
    if item_type in {"opportunity", "digest_item"} and not has_remote and not has_any(value, ("onsite", "on-site", "hybrid", "гибрид", "офис")):
        unknowns.append("Неясен формат remote / onsite / hybrid")
    return Evaluation(min(score, 100), decision, tuple(reasons), tuple(risks), tuple(unknowns), item_fingerprint)


def _tokens(text: str) -> set[str]:
    value = fold(text)
    words = re.findall(r"[a-zа-я0-9]{3,}", value)
    stop = {
        "вакансия", "вакансии", "работа", "проект", "ищем", "нужен", "нужна",
        "looking", "hiring", "position", "project", "remote", "удаленно", "удалённо",
    }
    return {word for word in words if word not in stop}


def near_duplicate(left: str, right: str) -> bool:
    a, b = _tokens(left), _tokens(right)
    if min(len(a), len(b)) < 4:
        return False
    overlap = len(a & b)
    containment = overlap / min(len(a), len(b))
    jaccard = overlap / len(a | b)
    return containment >= 0.8 or jaccard >= 0.62


def company_dedup_key(text: str) -> str:
    key = fold(text.splitlines()[0])
    return re.sub(r"(?<![\w-])мтс(?![\w-])", "mts", key)


def company_name_for_size(text: str) -> str:
    if is_structured_vacancy(text):
        name = structured_vacancy_fields(text)[1]
        if name:
            return name
    labelled = re.search(r"(?:^|[\n;|])\s*(?:company|компания)\s*:\s*([^\n;|]+)", text, re.IGNORECASE)
    return labelled.group(1).strip() if labelled else ""


def build_candidate_items(
    rows: Iterable[Mapping[str, object]],
    *,
    company_sizes: Mapping[str, CompanySize] | None = None,
    digest_details: DigestDetails | None = None,
) -> list[CandidateItem]:
    rows = list(rows)
    descriptions = digest_details or {}
    validate_digest_sources(rows, descriptions)
    matched_details: set[tuple[int, int, int, str]] = set()
    facts = load_company_sizes() if company_sizes is None else company_sizes
    units: list[CandidateItem] = []
    for row in rows:
        chat_id = int(row["chat_id"])
        message_id = int(row["message_id"])
        text = str(row["text"])
        try:
            raw_detail_text = row["detail_text"]
        except (KeyError, IndexError):
            raw_detail_text = None
        detail_text = str(raw_detail_text) if raw_detail_text else None
        message_type = classify_message(text)
        if message_type == "digest":
            units.append(
                CandidateItem(
                    chat_id,
                    message_id,
                    0,
                    "digest",
                    "Дайджест разобран на отдельные смысловые блоки",
                    Evaluation(0, "exclude", ("Исходный дайджест не показывается целиком",), (), (), fingerprint(text)),
                )
            )
            for index, block in enumerate(split_digest(text), start=1):
                detail_key = (chat_id, message_id, index, text_hash(block))
                block_detail = descriptions.get(detail_key)
                if block_detail:
                    matched_details.add(detail_key)
                units.append(
                    CandidateItem(
                        chat_id,
                        message_id,
                        index,
                        "digest_item",
                        block,
                        apply_company_size(
                            evaluate_item(block, "digest_item", block_detail.text if block_detail else None),
                            company_name_for_size(block), facts,
                        ),
                    )
                )
        else:
            evaluation = evaluate_item(text, message_type, detail_text)
            if message_type == "opportunity":
                evaluation = apply_company_size(evaluation, company_name_for_size(text), facts)
            units.append(
                CandidateItem(
                    chat_id,
                    message_id,
                    0,
                    message_type,
                    text,
                    evaluation,
                )
            )
            company_item = build_company_item(chat_id, message_id, text, detail_text)
            if company_item is not None:
                units.append(replace(
                    company_item,
                    evaluation=apply_company_size(
                        company_item.evaluation, company_name_for_size(text), facts,
                    ),
                ))

    if matched_details != set(descriptions):
        raise ValueError("Digest description does not match an exact current block")
    ordered = sorted(units, key=lambda item: item.item_type == "digest_item")
    accepted_texts: list[str] = []
    accepted_companies: set[str] = set()
    deduplicated: list[CandidateItem] = []
    for item in ordered:
        evaluation = item.evaluation
        if evaluation.decision in {"include", "review"}:
            company_key = company_dedup_key(item.text) if item.item_type == "company" else ""
            is_duplicate_company = bool(company_key and company_key in accepted_companies)
            is_duplicate_text = (
                item.item_type != "company"
                and any(near_duplicate(item.text, earlier) for earlier in accepted_texts)
            )
            if is_duplicate_company or is_duplicate_text:
                evaluation = replace(
                    evaluation,
                    decision="duplicate",
                    reasons=evaluation.reasons + ("Повтор уже найденной отдельной публикации",),
                )
            else:
                if company_key:
                    accepted_companies.add(company_key)
                else:
                    accepted_texts.append(item.text)
        deduplicated.append(replace(item, evaluation=evaluation))
    return deduplicated

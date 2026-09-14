from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

from telegram_project_radar.cli import build_parser
from telegram_project_radar.company_size import (
    CompanySize, apply_company_size, load_company_sizes,
)
from telegram_project_radar.models import Evaluation
from telegram_project_radar.selection_v2 import build_candidate_items


def fact(minimum: int, maximum: int | None) -> CompanySize:
    return CompanySize(
        "Example Co", minimum, maximum, "https://example.com/about",
        "Company-wide employee count (synthetic test)", date.today(),
    )


def vacancy(company: str = "Example Co", message_id: int = 1) -> dict:
    return {
        "chat_id": 1, "message_id": message_id,
        "text": f"DevOps Engineer\n{company} | Software development\nSpain\nRemote contract\nSkills: linux",
    }


class CompanySizeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.baseline = Evaluation(80, "include", ("Profile fits",), (), (), "stable-hash")

    def test_exact_boundary_and_ranges(self) -> None:
        for low, high, expected in (
            (100, 100, "within_limit"), (200, 200, "within_limit"),
            (201, 201, "too_large"), (5001, None, "too_large"),
            (51, 200, "within_limit"), (101, 500, "uncertain"),
            (200, None, "uncertain"),
        ):
            with self.subTest(low=low, high=high):
                evidence = fact(low, high)
                self.assertEqual(evidence.status(date.today()), expected)
                result = apply_company_size(self.baseline, "Example Co", {"example co": evidence})
                self.assertEqual(result.decision, "exclude" if expected == "too_large" else "include")
                self.assertEqual(result.fingerprint, self.baseline.fingerprint)
                self.assertEqual(bool(result.unknowns), expected == "uncertain")

    def test_unknown_stale_and_future_facts_never_reject(self) -> None:
        for offset in (-181, 1):
            evidence = replace(fact(1000, 1000), checked_on=date.today() + timedelta(days=offset))
            result = apply_company_size(self.baseline, "Example Co", {"example co": evidence})
            self.assertEqual(result.decision, "include")
            self.assertTrue(result.unknowns)
        result = apply_company_size(self.baseline, "Unknown Co", {})
        self.assertEqual(result.score, 80)
        self.assertIn("Размер компании неизвестен", result.unknowns[0])

    def test_size_filter_applies_to_opportunity_and_company(self) -> None:
        items = build_candidate_items([vacancy()], company_sizes={"example co": fact(201, 201)})
        self.assertEqual({item.item_type for item in items}, {"opportunity", "company"})
        for item in items:
            self.assertEqual(item.evaluation.decision, "exclude")
            self.assertIn("https://example.com/about", item.evaluation.reasons[-1])

    def test_size_does_not_override_other_exclusions(self) -> None:
        row = vacancy()
        row["detail_text"] = "Full-time employment"
        items = build_candidate_items([row], company_sizes={"example co": fact(200, 200)})
        opportunity = next(item for item in items if item.item_type == "opportunity")
        self.assertEqual(opportunity.evaluation.decision, "exclude")
        self.assertIn("full-time", opportunity.evaluation.reasons[0])

    def test_team_count_and_vendor_mentions_are_not_company_identity(self) -> None:
        row = vacancy("Small Studio")
        row["detail_text"] = "Team of 5. We use Kaspersky products."
        items = build_candidate_items([row], company_sizes={"kaspersky": fact(5001, None)})
        for item in items:
            self.assertEqual(item.evaluation.decision, "include")
            self.assertTrue(item.evaluation.unknowns)

    def test_person_is_not_rejected_for_past_employer(self) -> None:
        row = {"chat_id": 1, "message_id": 1, "text": "Я DevOps, работал в Kaspersky и у интернет-провайдеров, открыт к IT-проектам."}
        item = build_candidate_items([row], company_sizes={"kaspersky": fact(5001, None)})[0]
        self.assertEqual(item.item_type, "person")
        self.assertIn(item.evaluation.decision, {"include", "review"})

    def test_digest_only_uses_company_label_in_its_own_block(self) -> None:
        details = "\nПодробности проектных задач, условий сотрудничества и ожидаемого результата." * 18
        row = {
            "chat_id": 1, "message_id": 1,
            "text": "Подборка вакансий недели\n\nCompany: Example Co; Ищем freelance DevOps для Linux серверов" + details + "\n" * 10 + "Компания: Small Studio; Требуется contract Network Engineer, routing и telecom" + details,
        }
        items = [item for item in build_candidate_items([row], company_sizes={"example co": fact(201, 201)}) if item.item_type == "digest_item"]
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].evaluation.decision, "exclude")
        self.assertEqual(items[1].evaluation.decision, "include")

    def test_large_company_does_not_suppress_small_company_as_duplicate(self) -> None:
        items = build_candidate_items(
            [vacancy(), vacancy("Small Studio", 2)],
            company_sizes={"example co": fact(201, 201), "small studio": fact(200, 200)},
        )
        for item in items:
            self.assertEqual(item.evaluation.decision, "exclude" if item.message_id == 1 else "include")

    def test_registry_rejects_team_scope_bad_bounds_and_alias_collisions(self) -> None:
        row = {
            "company": "Example Co", "aliases": [], "scope": "company",
            "employees_min": 200, "employees_max": 200,
            "source_url": "https://example.com/about", "source_excerpt": "200 employees",
            "checked_on": date.today().isoformat(),
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "facts.json"
            for override in ({"scope": "team"}, {"employees_max": 199}, {"employees_min": True}, {"aliases": ["EXAMPLE CO"]}, {"source_url": ""}, {"source_excerpt": ""}):
                with self.subTest(override=override):
                    path.write_text(json.dumps({"schema_version": 1, "companies": [{**row, **override}]}))
                    with self.assertRaises(ValueError):
                        load_company_sizes(path)

    def test_bundled_fact_and_explicit_registry_route(self) -> None:
        facts = load_company_sizes()
        self.assertEqual(facts["касперский"], facts["kaspersky"])
        self.assertEqual(facts["kaspersky"].minimum, 5001)
        self.assertEqual(facts["kaspersky"].status(date(2026, 9, 10)), "too_large")
        args = build_parser().parse_args(["tune", "--run-id", "test", "--company-sizes", "/tmp/verified.json"])
        self.assertEqual(args.company_sizes, "/tmp/verified.json")


if __name__ == "__main__":
    unittest.main()

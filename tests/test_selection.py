from __future__ import annotations

import unittest

from telegram_project_radar.selection import evaluate, evaluate_rows


class SelectionTests(unittest.TestCase):
    def test_explicit_full_time_is_excluded(self) -> None:
        result = evaluate("Full-time DevOps engineer for a hosting company")
        self.assertEqual(result.decision, "exclude")

    def test_flexible_infrastructure_work_is_included(self) -> None:
        result = evaluate("Ищем DevOps на проект: настроить VPS и monitoring, contract remote")
        self.assertEqual(result.decision, "include")
        self.assertGreaterEqual(result.score, 70)

    def test_infrastructure_vacancy_with_unknown_format_is_included(self) -> None:
        result = evaluate("Вакансия: network engineer в дата-центре")
        self.assertEqual(result.decision, "include")
        self.assertTrue(any("part-time" in item for item in result.unknowns))

    def test_gtm_work_outside_infrastructure_is_included(self) -> None:
        result = evaluate("Looking for a freelance technical GTM consultant for lead enrichment")
        self.assertEqual(result.decision, "include")

    def test_duplicate_is_not_shown_twice(self) -> None:
        rows = [
            {"chat_id": 1, "message_id": 1, "text": "Need freelance DevOps for VPS"},
            {"chat_id": 2, "message_id": 2, "text": "Need freelance DevOps for VPS"},
        ]
        results = evaluate_rows(rows)
        self.assertEqual(results[0][2].decision, "include")
        self.assertEqual(results[1][2].decision, "duplicate")


if __name__ == "__main__":
    unittest.main()

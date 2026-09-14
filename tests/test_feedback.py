from __future__ import annotations

import unittest

from telegram_project_radar.feedback import evaluate_feedback
from telegram_project_radar.models import CandidateItem, Evaluation


def item(item_type: str, decision: str = "include") -> CandidateItem:
    return CandidateItem(
        1,
        2,
        0 if item_type == "opportunity" else 1,
        item_type,
        "example",
        Evaluation(80, decision, (), (), (), "fingerprint"),
    )


class FeedbackTests(unittest.TestCase):
    def test_types_verdict_can_keep_company_and_reject_opportunity(self) -> None:
        feedback = {
            "labels": [
                {
                    "report_no": 1,
                    "chat_id": 1,
                    "message_id": 2,
                    "verdict": "types",
                    "expected_types": ["company"],
                    "rejected_types": ["opportunity"],
                }
            ]
        }
        result = evaluate_feedback(feedback, [item("company")])
        self.assertEqual(result["matched"], 1)
        self.assertEqual(result["mismatched"], 0)

    def test_types_verdict_detects_unwanted_surface_type(self) -> None:
        feedback = {
            "labels": [
                {
                    "report_no": 1,
                    "chat_id": 1,
                    "message_id": 2,
                    "verdict": "types",
                    "expected_types": ["company"],
                    "rejected_types": ["opportunity"],
                }
            ]
        }
        result = evaluate_feedback(feedback, [item("company"), item("opportunity")])
        self.assertEqual(result["matched"], 0)
        self.assertEqual(result["mismatched"], 1)

    def test_split_digest_accepts_relevant_item_hidden_as_duplicate(self) -> None:
        feedback = {
            "labels": [
                {
                    "report_no": 1,
                    "chat_id": 1,
                    "message_id": 2,
                    "verdict": "split_digest",
                    "expected_type": "digest",
                }
            ]
        }
        result = evaluate_feedback(
            feedback,
            [item("digest", "exclude"), item("digest_item", "duplicate")],
        )
        self.assertEqual(result["matched"], 1)


if __name__ == "__main__":
    unittest.main()

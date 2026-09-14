from __future__ import annotations

import unittest

from telegram_project_radar.cli import build_parser, selected_source_names
from telegram_project_radar.config import APPROVED_SOURCE_NAMES


class SelectedSourceTests(unittest.TestCase):
    def test_no_selection_uses_full_allowlist(self) -> None:
        self.assertEqual(selected_source_names(None), APPROVED_SOURCE_NAMES)

    def test_one_approved_source_can_be_isolated(self) -> None:
        selected = selected_source_names([APPROVED_SOURCE_NAMES[-1]])
        self.assertEqual(selected, (APPROVED_SOURCE_NAMES[-1],))

    def test_unknown_source_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            selected_source_names(["unknown source"])

    def test_inbox_command_accepts_type_and_limit(self) -> None:
        args = build_parser().parse_args(
            [
                "inbox",
                "--run-id",
                "run-1",
                "--type",
                "opportunity",
                "--limit",
                "3",
            ]
        )
        self.assertEqual(args.run_id, "run-1")
        self.assertEqual(args.types, ["opportunity"])
        self.assertEqual(args.limit, 3)


if __name__ == "__main__":
    unittest.main()

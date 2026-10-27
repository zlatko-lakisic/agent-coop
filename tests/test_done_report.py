"""The done report refuses an incomplete account of the work."""

from __future__ import annotations

import unittest

import support

from agentcoop.donereport import check_done_report, done_template
from agentcoop.errors import CoopError
from agentcoop.progress import write_progress


def _body(**sections: str) -> str:
    order = ("Asked", "Claims", "Not verified", "Surprises", "Risks")
    lines = ["Status: done", "Now: Finish the report.", ""]
    for name in order:
        if name not in sections:
            continue
        lines.append("## " + name)
        lines.append(sections[name])
        lines.append("")
    return "\n".join(lines)


COMPLETE = _body(
    **{
        "Asked": "- done: Add the done report.",
        "Claims": "- The check accepts a complete report. Evidence: test_done_report.py passed.",
        "Not verified": "None",
        "Surprises": "None",
        "Risks": "None",
    }
)


class DoneReportTests(unittest.TestCase):
    def test_missing_section_is_refused(self) -> None:
        body = _body(
            **{
                "Asked": "- done: Add the done report.",
                "Claims": "- It works. Evidence: the test output.",
                "Not verified": "None",
                "Surprises": "None",
            }
        )
        with self.assertRaises(CoopError) as caught:
            check_done_report(body)
        self.assertIn("missing the Risks section", str(caught.exception))

    def test_unmarked_item_is_refused(self) -> None:
        body = COMPLETE.replace("- done: Add the done report.", "- Add the done report.")
        with self.assertRaises(CoopError) as caught:
            check_done_report(body)
        self.assertIn("no mark", str(caught.exception))

    def test_claim_without_evidence_is_refused(self) -> None:
        body = COMPLETE.replace(
            "- The check accepts a complete report. Evidence: test_done_report.py passed.",
            "- The check accepts a complete report.",
        )
        with self.assertRaises(CoopError) as caught:
            check_done_report(body)
        self.assertIn("no evidence", str(caught.exception))

    def test_none_is_refused_when_an_item_is_only_partly_done(self) -> None:
        body = COMPLETE.replace("- done: Add the done report.", "- partly: Add the done report. Missing: the tests.")
        with self.assertRaises(CoopError) as caught:
            check_done_report(body)
        self.assertIn("cannot be None", str(caught.exception))

    def test_a_complete_report_is_accepted(self) -> None:
        check_done_report(COMPLETE)
        self.assertIn("## Asked", done_template())

    def test_progress_write_uses_the_check(self) -> None:
        with support.TempEnv() as env:
            from agentcoop.cli import main

            self.assertEqual(main(["setup", "--connect", "cursor", "--schedule", "no", "--yes"]), 0)
            folder = env.workspace / "demo"
            folder.mkdir()
            self.assertEqual(
                main(
                    [
                        "project",
                        "add",
                        str(folder),
                        "--name",
                        "demo",
                        "--driver",
                        "cursor",
                        "--gitignore",
                        "no",
                        "--yes",
                    ]
                ),
                0,
            )
            name = write_progress(env.board, "demo", "cursor", COMPLETE)
            self.assertTrue(name.endswith("_cursor.md"))
            with self.assertRaises(CoopError):
                write_progress(env.board, "demo", "cursor", "Status: done\nNow: No sections.\n")


if __name__ == "__main__":
    unittest.main()

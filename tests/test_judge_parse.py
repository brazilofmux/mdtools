"""Judge response parsing must fail closed (issue #3)."""

from __future__ import annotations

import unittest

from prosevary.llm import JudgeResult, _parse_judge


class JudgeParseFailClosedTests(unittest.TestCase):
    def test_literal_true_accepts(self) -> None:
        r = _parse_judge('{"accept": true, "reason": "ok"}')
        self.assertEqual(r, JudgeResult(accept=True, reason="ok"))

    def test_literal_false_rejects(self) -> None:
        r = _parse_judge('{"accept": false, "reason": "changed meaning"}')
        self.assertEqual(r, JudgeResult(accept=False, reason="changed meaning"))

    def test_string_false_does_not_accept(self) -> None:
        # The original footgun: bool("false") is True in Python.
        r = _parse_judge('{"accept": "false", "reason": "nope"}')
        self.assertFalse(r.accept)
        self.assertIn("non-boolean", r.reason)

    def test_string_true_does_not_accept(self) -> None:
        r = _parse_judge('{"accept": "true"}')
        self.assertFalse(r.accept)

    def test_numeric_accept_rejects(self) -> None:
        for payload in ('{"accept": 1}', '{"accept": 0}', '{"accept": 1.0}'):
            with self.subTest(payload=payload):
                r = _parse_judge(payload)
                self.assertFalse(r.accept)

    def test_null_accept_rejects(self) -> None:
        r = _parse_judge('{"accept": null}')
        self.assertFalse(r.accept)

    def test_missing_accept_rejects(self) -> None:
        r = _parse_judge('{"reason": "forgot the field"}')
        self.assertFalse(r.accept)
        self.assertIn("missing accept", r.reason)

    def test_top_level_non_objects_reject_without_crash(self) -> None:
        for payload in ("false", "null", "[]", '"accept"', "42", "true"):
            with self.subTest(payload=payload):
                r = _parse_judge(payload)
                self.assertFalse(r.accept)
                self.assertIn("not an object", r.reason)

    def test_malformed_json_rejects(self) -> None:
        r = _parse_judge("{accept: true")
        self.assertFalse(r.accept)
        self.assertIn("unparseable", r.reason)

    def test_ambiguous_prose_rejects(self) -> None:
        # Old heuristic accepted free-form "accept … true" text. Fail closed.
        r = _parse_judge("I accept this as true, ship it.")
        self.assertFalse(r.accept)
        self.assertIn("unparseable", r.reason)

    def test_reasoning_block_stripped(self) -> None:
        raw = '<think>pondering</think>{"accept": true, "reason": "same"}'
        r = _parse_judge(raw)
        self.assertTrue(r.accept)
        self.assertEqual(r.reason, "same")

    def test_unclosed_think_stripped(self) -> None:
        raw = '<think>still going{"accept": false, "reason": "no"}'
        # Unclosed think eats the rest; nothing left to parse → reject.
        r = _parse_judge(raw)
        self.assertFalse(r.accept)

    def test_fenced_json(self) -> None:
        raw = '```json\n{"accept": false, "reason": "nope"}\n```'
        r = _parse_judge(raw)
        self.assertFalse(r.accept)
        self.assertEqual(r.reason, "nope")

    def test_last_object_wins_in_prose_wrap(self) -> None:
        raw = (
            'First thought {"accept": true, "reason": "too early"} then '
            'final {"accept": false, "reason": "changed claim"}.'
        )
        r = _parse_judge(raw)
        self.assertFalse(r.accept)
        self.assertEqual(r.reason, "changed claim")

    def test_prose_wrap_literal_true_accepts(self) -> None:
        raw = 'Sure. {"accept": true, "reason": "ok"} hope that helps'
        r = _parse_judge(raw)
        self.assertTrue(r.accept)

    def test_prose_wrap_string_false_still_rejects(self) -> None:
        raw = 'Verdict: {"accept": "false", "reason": "stringy"}'
        r = _parse_judge(raw)
        self.assertFalse(r.accept)
        self.assertIn("non-boolean", r.reason)

    def test_fallback_skips_objects_without_accept(self) -> None:
        raw = 'meta {"score": 1} then {"accept": true, "reason": "yes"}'
        r = _parse_judge(raw)
        self.assertTrue(r.accept)
        self.assertEqual(r.reason, "yes")

    def test_empty_and_whitespace_reject(self) -> None:
        for payload in ("", "   ", "\n"):
            with self.subTest(payload=repr(payload)):
                r = _parse_judge(payload)
                self.assertFalse(r.accept)


if __name__ == "__main__":
    unittest.main()

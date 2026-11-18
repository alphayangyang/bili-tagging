"""Boundary probes, decision order, and prompt v3 wiring."""
from __future__ import annotations

import os
import unittest
from unittest import mock

from danmaku_filter import boundary, taxonomy
from danmaku_filter.classify.llm import LLMClassifier
from danmaku_filter.taxonomy import CATEGORY_IDS
from danmaku_filter.types import Danmaku


class BoundaryPairsTest(unittest.TestCase):
    def test_probe_items_are_well_formed(self):
        rows = boundary.probe_items()
        self.assertEqual(len(rows), len(boundary.PAIRS) * 2)
        known = set(CATEGORY_IDS) | {taxonomy.FALLBACK_ID}
        texts = set()
        for row in rows:
            self.assertIn(row["expect_dominant"], known)
            self.assertTrue(row["must"], row)
            for tag, floor in row["must"].items():
                self.assertIn(tag, known)
                self.assertGreater(floor, 0.0)
                self.assertLessEqual(floor, 1.0)
            self.assertEqual(row["text"], row["text"].strip())
            self.assertNotIn(row["text"], texts, "duplicate probe text")
            texts.add(row["text"])

    def test_every_pair_has_two_sides_on_the_named_boundary(self):
        rows = boundary.probe_items()
        by_pair = {}
        for row in rows:
            by_pair.setdefault(row["pair"], []).append(row)
        for pair, sides in by_pair.items():
            self.assertEqual(len(sides), 2, pair)
            self.assertNotEqual(sides[0]["text"], sides[1]["text"])
            # the focus names the two labels of the boundary; both sides must use them
            names = [part.strip() for part in sides[0]["focus"].split(" vs ")]
            self.assertEqual(len(names), 2, sides[0]["focus"])
            for side in sides:
                self.assertIn(side["expect_dominant"], names, (pair, side))

    def test_fewshot_one_pair_per_focus(self):
        anchors = boundary.fewshot_pairs()
        self.assertGreaterEqual(len(anchors), 4)
        focuses = [focus for focus, _l, _r, _w in boundary.PAIRS]
        picked_focus = []
        seen = set()
        for focus in focuses:
            if focus in seen:
                continue
            seen.add(focus)
            picked_focus.append(focus)
        self.assertEqual(len(anchors), min(len(picked_focus), 6))


class DecisionOrderTest(unittest.TestCase):
    def test_decision_order_has_five_ordered_steps(self):
        text = taxonomy.decision_order()
        steps = [line for line in text.splitlines() if line.strip()]
        self.assertEqual(len(steps), len(taxonomy.DECISION_ORDER))
        self.assertTrue(steps[0].startswith("1."))
        self.assertIn("irrelevant_repetition", steps[0])
        self.assertIn("information", steps[2])
        self.assertIn("other", steps[-1])

    def test_prompt_guide_v3_appends_the_order(self):
        plain = taxonomy.prompt_guide()
        v3 = taxonomy.prompt_guide_v3()
        self.assertNotIn("Decision order", plain)
        self.assertTrue(v3.startswith(plain))
        self.assertIn("Decision order", v3)
        self.assertIn(taxonomy.decision_order(), v3)


class PromptStyleTest(unittest.TestCase):
    def _prompt(self, style: str) -> str:
        with mock.patch.dict(os.environ, {"DM_LLM_PROMPT": style}):
            clf = LLMClassifier(batch_size=2, video_context="music")
            return clf._prompt([Danmaku("哈哈哈", 1000), Danmaku("这是第3集", 2000)])

    def test_v3_prompt_carries_order_and_anchors(self):
        text = self._prompt("v3")
        self.assertIn("Decision order", text)
        self.assertIn("Boundary anchors", text)
        self.assertIn("exactly 2 objects", text)
        self.assertIn("information stays 0.6 or higher", text)
        self.assertIn("Video context: music", text)
        # the first anchor pair must appear together with the pipe separator
        left, right = boundary.fewshot_pairs()[0]
        self.assertIn(left + "  |  " + right, text)

    def test_v2_prompt_stays_flat(self):
        text = self._prompt("v2")
        self.assertNotIn("Decision order", text)
        self.assertNotIn("Boundary anchors", text)
        self.assertIn("exactly 2 objects", text)

    def test_default_style_is_v4(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DM_LLM_PROMPT", None)
            from danmaku_filter.classify.llm import prompt_style

            self.assertEqual(prompt_style(), "v4")


class DecisionOrderV4Test(unittest.TestCase):
    def test_v4_order_keeps_five_steps(self):
        text = taxonomy.decision_order_v4()
        steps = [line for line in text.splitlines() if line.strip()]
        self.assertEqual(len(steps), len(taxonomy.DECISION_ORDER_V4))
        self.assertIn("irrelevant_repetition", steps[0])
        self.assertIn("attack", steps[1])
        self.assertIn("information", steps[2])
        self.assertTrue(steps[-1].endswith("reaches 0.2."))

    def test_prompt_guide_v4_appends_the_tight_order(self):
        plain = taxonomy.prompt_guide()
        v4 = taxonomy.prompt_guide_v4()
        self.assertTrue(v4.startswith(plain))
        self.assertIn("Decision order", v4)
        self.assertIn(taxonomy.decision_order_v4(), v4)
        self.assertNotEqual(taxonomy.decision_order(), taxonomy.decision_order_v4())

    def test_v4_prompt_softens_the_anchor_requirement(self):
        with mock.patch.dict(os.environ, {"DM_LLM_PROMPT": "v4"}):
            clf = LLMClassifier(batch_size=1, video_context="music")
            text = clf._prompt([Danmaku("哈哈哈哈这翻译", 1000)])
        self.assertIn("Boundary anchors", text)
        self.assertIn("not a rule that two comments must have different dominant functions", text)
        self.assertNotIn("must not receive the same dominant function", text)
        self.assertIn("nothing but symbols", text)
        self.assertIn("哈哈哈哈这翻译 is humour", text)

if __name__ == "__main__":
    unittest.main()

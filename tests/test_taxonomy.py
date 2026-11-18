"""Taxonomy v0.3 contract: provisional tags plus a formalised other class."""
import unittest

from danmaku_filter import taxonomy
from danmaku_filter.policy import DEFAULT_PREFS, normalize_prefs


class TaxonomySpecTest(unittest.TestCase):
    def test_new_tags_are_registered_but_other_is_not_scored(self) -> None:
        self.assertIn("political", taxonomy.CATEGORY_IDS)
        self.assertIn("inflammatory", taxonomy.CATEGORY_IDS)
        self.assertNotIn("other", taxonomy.CATEGORY_IDS)

    def test_spec_exports_other_with_its_usage_rule(self) -> None:
        spec = taxonomy.spec()
        self.assertEqual(spec["version"], 0.3)
        ids = [category["id"] for category in spec["categories"]]
        self.assertIn("other", ids)
        other = next(category for category in spec["categories"] if category["id"] == "other")
        self.assertTrue(other["fallback"])
        self.assertIn("not a safe default", other["usage"])

    def test_prompt_guide_marks_other_as_a_last_resort(self) -> None:
        guide = taxonomy.prompt_guide()
        self.assertIn("other (Other / unclassifiable)", guide)
        self.assertIn("last resort, not a safe default", guide)

    def test_default_policy_covers_every_tag(self) -> None:
        self.assertEqual(DEFAULT_PREFS["per_type"]["political"], "show")
        self.assertEqual(DEFAULT_PREFS["per_type"]["inflammatory"], "hide")
        prefs = normalize_prefs({"per_type": {"inflammatory": "collapse"}})
        self.assertEqual(prefs["per_type"]["inflammatory"], "collapse")


if __name__ == "__main__":
    unittest.main()

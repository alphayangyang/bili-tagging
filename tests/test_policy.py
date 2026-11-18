import unittest

from danmaku_filter.policy import DEFAULT_PREFS, decide, normalize_prefs, resolve_item


class DecideTest(unittest.TestCase):
    def test_untagged_item_is_shown(self) -> None:
        self.assertEqual(decide(None)["action"], "show")
        self.assertEqual(decide(None)["reason"], "not_tagged")

    def test_attack_is_hidden_by_default(self) -> None:
        result = decide({"dominant": "attack", "tags": {"attack": 0.95}, "score": 0.95})
        self.assertEqual(result["action"], "hide")
        self.assertEqual(result["reason"], "pref_hide_attack")

    def test_spoiler_and_conflict_are_hidden(self) -> None:
        for label in ("spoiler", "conflict"):
            self.assertEqual(decide({"dominant": label, "score": 0.8})["action"], "hide")

    def test_repetition_is_collapsed(self) -> None:
        result = decide({"dominant": "irrelevant_repetition", "score": 0.8})
        self.assertEqual(result["action"], "collapse")

    def test_humor_and_information_are_shown(self) -> None:
        for label in ("humor", "information", "emotional_resonance"):
            result = decide({"dominant": label, "score": 0.9})
            self.assertEqual(result["action"], "show")
            self.assertEqual(result["reason"], "default_show")

    def test_low_confidence_is_always_shown(self) -> None:
        result = decide({"dominant": "attack", "score": 0.2})
        self.assertEqual(result["action"], "show")
        self.assertEqual(result["reason"], "low_confidence")

    def test_disabled_prefs_show_everything(self) -> None:
        result = decide({"dominant": "attack", "score": 0.9}, {"enabled": False})
        self.assertEqual(result["action"], "show")
        self.assertEqual(result["reason"], "disabled")

    def test_user_override_wins(self) -> None:
        override = {"label": "humor"}
        item = resolve_item({"dominant": "attack", "score": 0.9, "source": "rules"}, override)
        self.assertEqual(item["source"], "user")
        self.assertEqual(decide(item)["action"], "show")

    def test_user_override_to_attack_is_hidden(self) -> None:
        item = resolve_item({"dominant": "humor", "score": 0.9}, {"label": "attack"})
        self.assertEqual(decide(item)["action"], "hide")

    def test_per_type_preference_can_unhide_attack(self) -> None:
        prefs = {"per_type": {"attack": "show"}}
        self.assertEqual(decide({"dominant": "attack", "score": 0.9}, prefs)["action"], "show")

    def test_custom_min_confidence(self) -> None:
        prefs = {"min_confidence": 0.95}
        result = decide({"dominant": "attack", "score": 0.9}, prefs)
        self.assertEqual(result["reason"], "low_confidence")

    def test_label_falls_back_to_best_tag(self) -> None:
        result = decide({"tags": {"attack": 0.7, "humor": 0.3}})
        self.assertEqual(result["label"], "attack")
        self.assertEqual(result["action"], "hide")

    def test_unknown_label_is_shown(self) -> None:
        self.assertEqual(decide({"dominant": "vibes", "score": 0.9})["action"], "show")


class NormalizePrefsTest(unittest.TestCase):
    def test_defaults_are_not_mutated(self) -> None:
        snapshot = normalize_prefs(None)
        snapshot["per_type"]["attack"] = "show"
        self.assertEqual(DEFAULT_PREFS["per_type"]["attack"], "hide")

    def test_invalid_values_are_ignored(self) -> None:
        prefs = normalize_prefs(
            {"enabled": "no", "min_confidence": 5, "per_type": {"attack": "explode", "nope": "hide"}}
        )
        self.assertTrue(prefs["enabled"])
        self.assertEqual(prefs["min_confidence"], 1.0)
        self.assertEqual(prefs["per_type"]["attack"], "hide")
        self.assertNotIn("nope", prefs["per_type"])

    def test_partial_prefs_merge(self) -> None:
        prefs = normalize_prefs({"per_type": {"spoiler": "collapse"}, "density": {"max_visible": 30}})
        self.assertEqual(prefs["per_type"]["spoil" + "er"], "collapse")
        self.assertEqual(prefs["per_type"]["attack"], "hide")
        self.assertEqual(prefs["density"]["max_visible"], 30)
        self.assertEqual(prefs["density"]["window_seconds"], 10)

    def test_garbage_input_returns_defaults(self) -> None:
        self.assertEqual(normalize_prefs("nope"), DEFAULT_PREFS)


if __name__ == "__main__":
    unittest.main()

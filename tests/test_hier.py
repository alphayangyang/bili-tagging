import json
import tempfile
import unittest
from pathlib import Path

from danmaku_filter.classify.hier import FAMILY_OF, HierTagger


def synthetic():
    texts, labels = [], []
    for i in range(30):
        texts.append("科普星球知识点位置%d" % i); labels.append("information")
        texts.append("哈哈笑死梗反转%d" % i); labels.append("humor")
        texts.append("泪目感动共鸣%d" % i); labels.append("emotional_resonance")
        texts.append("？？？刷屏符号%d" % i); labels.append("irrelevant_repetition")
        texts.append("傻逼辱骂攻击%d" % i); labels.append("attack")
        texts.append("对线争论吵架%d" % i); labels.append("conflict")
        texts.append("剧透结局凶手%d" % i); labels.append("spoiler")
    return texts, labels


class HierTaggerTest(unittest.TestCase):
    def setUp(self):
        texts, labels = synthetic()
        self.tagger = HierTagger().train(texts, labels)

    def test_predict_scores_returns_normalised_vectors(self):
        probe = ["哈哈笑死梗", "傻逼辱骂", "剧透结局"]
        rows = self.tagger.predict_scores(probe)
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertTrue(row)
            self.assertAlmostEqual(sum(row.values()), 1.0, places=6)
            for label in row:
                self.assertIn(label, FAMILY_OF)

    def test_predict_stays_inside_the_detected_family(self):
        self.assertEqual(self.tagger.predict(["哈哈笑死梗"])[0], "humor")
        self.assertEqual(self.tagger.predict(["傻逼辱骂"])[0], "attack")
        self.assertEqual(self.tagger.predict(["剧透结局"])[0], "spoiler")
        family = self.tagger.family_model.predict(["泪目感动"])[0]
        label = self.tagger.predict(["泪目感动"])[0]
        self.assertEqual(FAMILY_OF[label], family)

    def test_save_and_load_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hier.json"
            self.tagger.save(path)
            loaded = HierTagger.load(path)
            self.assertEqual(loaded.to_dict()["families"], self.tagger.to_dict()["families"])
            self.assertEqual(loaded.predict(["科普星球知识点"]), self.tagger.predict(["科普星球知识点"]))

    def test_unknown_label_is_rejected(self):
        with self.assertRaises(ValueError):
            HierTagger().train(["x"], ["not-a-label"])

    def test_empty_inputs_are_rejected(self):
        with self.assertRaises(ValueError):
            HierTagger().train([], [])
        self.assertEqual(self.tagger.predict_scores([]), [])

    def test_length_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            HierTagger().train(["a", "b"], ["humor"])


if __name__ == "__main__":
    unittest.main()


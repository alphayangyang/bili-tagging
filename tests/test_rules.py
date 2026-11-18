import unittest

from danmaku_filter.classify.rules import RuleClassifier
from danmaku_filter.types import Danmaku


class RuleClassifierTest(unittest.TestCase):
    def setUp(self) -> None:
        self.classifier = RuleClassifier()

    def label(self, text: str) -> str:
        return self.classifier.classify(Danmaku(text=text)).label

    def test_attack(self) -> None:
        self.assertEqual(self.label("up主就是个傻逼"), "attack")

    def test_spoiler(self) -> None:
        self.assertEqual(self.label("剧透预警：最后主角死了"), "spoiler")

    def test_information(self) -> None:
        self.assertEqual(self.label("这个BGM出自电影星际穿越"), "information")

    def test_repetition(self) -> None:
        self.assertEqual(self.label("哈哈哈哈哈哈哈"), "irrelevant_repetition")
        self.assertEqual(self.label("1111"), "irrelevant_repetition")

    def test_political(self) -> None:
        self.assertEqual(self.label("美国这次又要制裁谁"), "political")
        self.assertEqual(self.label("这个政策对房价影响挺大"), "political")

    def test_conflict(self) -> None:
        self.assertEqual(self.label("别扣帽子了，先讲逻辑"), "conflict")

    def test_inflammatory(self) -> None:
        self.assertEqual(self.label("小学生也来评论国际局势了"), "inflammatory")

    def test_fallback_is_other(self) -> None:
        result = self.classifier.classify(Danmaku(text="这个镜头拍得真好"))
        self.assertEqual(result.label, "other")
        self.assertEqual(result.source, "rules")


if __name__ == "__main__":
    unittest.main()

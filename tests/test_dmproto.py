import json
import unittest
from pathlib import Path

from danmaku_filter import dmproto

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE = FIXTURES / "seg_sample.bin"
EXPECTED = FIXTURES / "seg_sample.expected.json"


def load_expected() -> dict:
    return json.loads(EXPECTED.read_text(encoding="utf-8"))


class ParseSegmentTest(unittest.TestCase):
    def test_element_count_matches_fixture(self) -> None:
        data = SAMPLE.read_bytes()
        self.assertEqual(len(list(dmproto.iter_elements(data))), 150)

    def test_decoded_fields_match_expected(self) -> None:
        expected = load_expected()["elements"]
        raw_elements = list(dmproto.iter_elements(SAMPLE.read_bytes()))
        for raw, want in zip(raw_elements, expected):
            info = dmproto.decode_element(raw)
            self.assertEqual(info["dmid"], want["id_str"])
            self.assertEqual(info["text"], want["content"])
            self.assertEqual(info["time_ms"], want["progress"])
            self.assertEqual(info["mode"], want["mode"])
            self.assertEqual(info["uid"], want["mid_hash"])

    def test_parse_segment_drops_empty_text(self) -> None:
        expected = load_expected()["elements"]
        items = dmproto.parse_segment(SAMPLE.read_bytes())
        non_empty = [entry for entry in expected if entry["content"].strip()]
        self.assertEqual(len(items), len(non_empty))
        self.assertEqual(items[0].text, non_empty[0]["content"])
        self.assertEqual(items[0].dmid, non_empty[0]["id_str"])

    def test_parse_segment_on_empty_payload(self) -> None:
        self.assertEqual(dmproto.parse_segment(b""), [])


class FilterSegmentTest(unittest.TestCase):
    def test_identity_roundtrip_is_byte_identical(self) -> None:
        data = SAMPLE.read_bytes()
        filtered, hidden, collapsed = dmproto.filter_segment(data, set(), {})
        self.assertEqual(filtered, data)
        self.assertEqual((hidden, collapsed), (0, 0))

    def test_drop_first_three_preserves_remaining_bytes(self) -> None:
        data = SAMPLE.read_bytes()
        raw_elements = list(dmproto.iter_elements(data))
        dmids = [dmproto.decode_element(raw)["dmid"] for raw in raw_elements]
        filtered, hidden, collapsed = dmproto.filter_segment(data, set(dmids[:3]), {})
        self.assertEqual((hidden, collapsed), (3, 0))
        kept = list(dmproto.iter_elements(filtered))
        self.assertEqual(len(kept), len(raw_elements) - 3)
        self.assertEqual(kept[0], raw_elements[3])
        texts = [dmproto.decode_element(raw)["text"] for raw in kept]
        self.assertNotIn(dmproto.decode_element(raw_elements[0])["text"], texts)

    def test_collapse_replaces_content_only(self) -> None:
        data = SAMPLE.read_bytes()
        raw_elements = list(dmproto.iter_elements(data))
        target = dmproto.decode_element(raw_elements[1])["dmid"]
        placeholder = "【已折叠·攻击】"
        filtered, hidden, collapsed = dmproto.filter_segment(data, set(), {target: placeholder})
        self.assertEqual((hidden, collapsed), (0, 1))
        kept = list(dmproto.iter_elements(filtered))
        self.assertEqual(kept[0], raw_elements[0])
        self.assertEqual(kept[2:], raw_elements[2:])
        info = dmproto.decode_element(kept[1])
        self.assertEqual(info["text"], placeholder)
        self.assertEqual(info["time_ms"], dmproto.decode_element(raw_elements[1])["time_ms"])

    def test_replace_missing_field_raises(self) -> None:
        raw = dmproto.iter_elements(SAMPLE.read_bytes()).__next__()
        with self.assertRaises(KeyError):
            dmproto.replace_field(raw, 99, b"x")

    def test_filter_on_empty_payload(self) -> None:
        self.assertEqual(dmproto.filter_segment(b"", set(), {}), (b"", 0, 0))

    def test_truncated_varint_raises(self) -> None:
        with self.assertRaises(ValueError):
            list(dmproto.parse_fields(b"\x80"))


if __name__ == "__main__":
    unittest.main()

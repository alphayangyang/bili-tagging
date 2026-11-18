import json
import unittest
import urllib.error

from danmaku_filter.fetch import parse_xml

XML = """<?xml version="1.0" encoding="UTF-8"?>
<i>
  <chatserver>chat.bilibili.com</chatserver>
  <d p="12.5,1,25,16777215,1700000000,0,abc123,123456,10">这个BGM出自电影星际穿越</d>
  <d p="30,5,25,16777215,1700000001,0,def456,123457,10">哈哈哈哈哈哈哈</d>
</i>"""


class ParseXmlTest(unittest.TestCase):
    def test_parse(self) -> None:
        items = parse_xml(XML.encode("utf-8"))
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].text, "这个BGM出自电影星际穿越")
        self.assertEqual(items[0].time_ms, 12500)
        self.assertEqual(items[0].dmid, "123456")
        self.assertEqual(items[1].mode, 5)

    def test_entities_are_unescaped(self) -> None:
        raw = '<i><d p="1,1,25,0,0,0,x,1,0">a &amp; b &lt; c</d></i>'
        items = parse_xml(raw.encode("utf-8"))
        self.assertEqual(items[0].text, "a & b < c")



from unittest import mock

from danmaku_filter import fetch


class VideoInfoTest(unittest.TestCase):
    def test_get_video_info_uses_first_page_cid(self) -> None:
        payload = {
            "code": 0,
            "data": {
                "aid": 80433022,
                "cid": 1,
                "duration": 213,
                "title": "Never Gonna Give You Up",
                "pages": [{"cid": 137649199}],
            },
        }
        with mock.patch.object(fetch, "http_get", return_value=json.dumps(payload).encode()):
            info = fetch.get_video_info("BV1GJ411x7h7")
        self.assertEqual(info["aid"], 80433022)
        self.assertEqual(info["cid"], 137649199)
        self.assertEqual(info["duration"], 213)
        self.assertEqual(info["title"], "Never Gonna Give You Up")


class SegmentFetchTest(unittest.TestCase):
    def test_fetch_segment_returns_bytes(self) -> None:
        with mock.patch.object(fetch, "_http_get_raw", return_value=b"\x0a\x00"):
            self.assertEqual(fetch.fetch_segment(1, 2, 1), b"\x0a\x00")

    def test_fetch_segment_304_is_empty(self) -> None:
        error = urllib.error.HTTPError("u", 304, "Not Modified", {}, None)
        with mock.patch.object(fetch, "_http_get_raw", side_effect=error):
            self.assertEqual(fetch.fetch_segment(1, 2, 3), b"")
        error.close()

    def test_fetch_segment_retries_then_succeeds(self) -> None:
        error = urllib.error.URLError("boom")
        with mock.patch.object(fetch, "_http_get_raw", side_effect=[error, b"data"]):
            with mock.patch.object(fetch.time, "sleep"):
                self.assertEqual(fetch.fetch_segment(1, 2, 1), b"data")

    def test_fetch_segment_gives_up_after_retries(self) -> None:
        with mock.patch.object(fetch, "_http_get_raw", side_effect=urllib.error.URLError("boom")):
            with mock.patch.object(fetch.time, "sleep"):
                with self.assertRaises(RuntimeError):
                    fetch.fetch_segment(1, 2, 1, retries=2)

    def test_fetch_all_segments_stops_at_empty_segment(self) -> None:
        pages = [b"a" * 128, b"b" * 128, b""]
        with mock.patch.object(fetch, "fetch_segment", side_effect=pages) as patched:
            segments = fetch.fetch_all_segments(1, 2, 1200)
        self.assertEqual(len(segments), 2)
        self.assertEqual(patched.call_count, 3)

    def test_fetch_all_segments_counts_from_duration(self) -> None:
        with mock.patch.object(fetch, "fetch_segment", return_value=b"x" * 200) as patched:
            segments = fetch.fetch_all_segments(1, 2, 213)
        self.assertEqual(len(segments), 1)
        patched.assert_called_once_with(1, 2, 1)

    def test_fetch_all_segments_respects_max_segments(self) -> None:
        with mock.patch.object(fetch, "fetch_segment", return_value=b"x" * 200):
            self.assertEqual(len(fetch.fetch_all_segments(1, 2, 10_000, max_segments=2)), 2)


if __name__ == "__main__":
    unittest.main()

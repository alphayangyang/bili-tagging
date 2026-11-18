"""Integration tests for danmaku_filter.server over real HTTP on an ephemeral port."""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from danmaku_filter import cache
from danmaku_filter.server import make_server

BVID = "BV1TEST00001"
ITEMS = {
    "1001": {
        "text": "up主就是个傻逼",
        "time_ms": 12000,
        "mode": 1,
        "tags": {"attack": 0.9},
        "dominant": "attack",
        "score": 0.9,
        "evidence": "matched cue: 傻逼",
        "source": "rules",
    },
    "1002": {
        "text": "哈哈哈哈笑死我了",
        "time_ms": 13000,
        "mode": 1,
        "tags": {"humor": 0.9},
        "dominant": "humor",
        "score": 0.9,
        "evidence": "matched cue: 哈哈",
        "source": "rules",
    },
    "1003": {
        "text": "1",
        "time_ms": 14000,
        "mode": 1,
        "tags": {"irrelevant_repetition": 0.8},
        "dominant": "irrelevant_repetition",
        "score": 0.8,
        "evidence": "matched cue: 1",
        "source": "rules",
    },
    "1004": {
        "text": "这句话疑似攻击但不确定",
        "time_ms": 15000,
        "mode": 1,
        "tags": {"attack": 0.3},
        "dominant": "attack",
        "score": 0.3,
        "evidence": "matched cue: 疑似",
        "source": "rules",
    },
}


class ServerTestBase(unittest.TestCase):
    """Starts one server per test class on port 0 and seeds a fake tag cache."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.data_root = Path(cls._tmp.name) / "data"
        cls.tags_dir = cls.data_root / "tags"
        cls.feedback_dir = cls.data_root / "feedback"
        cls.seed(BVID, ITEMS)
        cls.server = make_server("127.0.0.1", 0, cls.tags_dir)
        cls.server.quiet = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        host, port = cls.server.server_address[:2]
        cls.base = f"http://{host}:{port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls._tmp.cleanup()

    @staticmethod
    def payload(bvid: str, items: dict) -> dict:
        return {
            "schema": 2,
            "bvid": bvid,
            "aid": 80433022,
            "cid": 137649199,
            "duration": 213,
            "segments": 1,
            "backend": "rules",
            "model": None,
            "created": "2026-10-21T12:00:00Z",
            "items": items,
        }

    @classmethod
    def seed(cls, bvid: str, items: dict | None = None) -> None:
        cache.save_tags(bvid, cls.payload(bvid, ITEMS if items is None else items), cls.tags_dir)

    def call(self, method: str, path: str, payload: dict | None = None, raw: str | None = None):
        data = None
        headers: dict[str, str] = {}
        if raw is not None:
            data = raw.encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                body = response.read()
                return response.status, dict(response.headers), json.loads(body) if body else None
        except urllib.error.HTTPError as error:
            try:
                body = error.read()
            finally:
                error.close()
            return error.code, dict(error.headers), json.loads(body) if body else None

    def decision(self, bvid: str, dmids: list, prefs: dict | None = None) -> dict:
        payload: dict = {"bvid": bvid, "dmids": dmids}
        if prefs is not None:
            payload["prefs"] = prefs
        status, _, body = self.call("POST", "/api/v1/decisions", payload)
        self.assertEqual(status, 200, body)
        return body


class HealthTest(ServerTestBase):
    def test_health(self) -> None:
        status, headers, body = self.call("GET", "/api/v1/health")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["version"], "0.1.0")
        self.assertEqual(body["tag_files"], 1)
        self.assertEqual(body["tags_dir"], str(self.tags_dir))
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), "*")
        self.assertIn("application/json", headers.get("Content-Type", ""))

    def test_options_preflight(self) -> None:
        status, headers, body = self.call("OPTIONS", "/api/v1/decisions")
        self.assertEqual(status, 204)
        self.assertIsNone(body)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), "*")
        self.assertIn("POST", headers.get("Access-Control-Allow-Methods", ""))


class DecisionsTest(ServerTestBase):
    def test_decisions_include_only_non_show_actions(self) -> None:
        # min_confidence 0.6 is stricter than the 0.3 default, so the 0.3 item
        # (1004) stays visible and the test still exercises the gating floor.
        body = self.decision(
            BVID, ["1001", "1002", "1003", "1004", "9999", "1001"], {"min_confidence": 0.6}
        )
        self.assertTrue(body["ok"])
        self.assertEqual(body["bvid"], BVID)
        self.assertTrue(body["found"])
        self.assertEqual(body["n"], 5)
        self.assertEqual(set(body["decisions"]), {"1001", "1003"})

        attack = body["decisions"]["1001"]
        self.assertEqual(attack["action"], "hide")
        self.assertEqual(attack["label"], "attack")
        self.assertEqual(attack["text"], "up主就是个傻逼")
        self.assertIn("傻逼", attack["evidence"])
        self.assertTrue(attack["reason"].startswith("pref_hide_"))
        self.assertGreaterEqual(attack["score"], 0.6)

        collapsed = body["decisions"]["1003"]
        self.assertEqual(collapsed["action"], "collapse")
        self.assertEqual(collapsed["label"], "irrelevant_repetition")
        self.assertEqual(body["miss"], ["9999"])

    def test_low_confidence_item_is_shown(self) -> None:
        body = self.decision(BVID, ["1004"], {"min_confidence": 0.6})
        self.assertEqual(body["decisions"], {})
        self.assertEqual(body["miss"], [])

    def test_default_threshold_applies_a_borderline_label(self) -> None:
        # Default min_confidence is 0.3, so a 0.3 item follows the user's
        # per-label strategy instead of being dropped as low confidence.
        body = self.decision(BVID, ["1004"])
        self.assertEqual(body["decisions"]["1004"]["action"], "hide")
        self.assertEqual(body["decisions"]["1004"]["label"], "attack")

    def test_missing_cache_marks_everything_as_miss(self) -> None:
        body = self.decision("BV1NOCACHE", ["1", "2"])
        self.assertFalse(body["found"])
        self.assertEqual(body["decisions"], {})
        self.assertEqual(body["miss"], ["1", "2"])
        self.assertEqual(body["n"], 2)

    def test_custom_prefs_are_applied(self) -> None:
        body = self.decision(BVID, ["1002"], {"per_type": {"humor": "hide"}})
        self.assertEqual(body["decisions"]["1002"]["action"], "hide")
        self.assertEqual(body["decisions"]["1002"]["label"], "humor")

    def test_disabled_prefs_show_everything(self) -> None:
        body = self.decision(BVID, ["1001", "1003"], {"enabled": False})
        self.assertEqual(body["decisions"], {})


class OverrideDecisionsTest(ServerTestBase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cache.save_overrides(
            BVID,
            {
                "1001": {"label": "humor", "at": "2026-10-21T12:00:00Z"},
                "1002": {"label": "attack", "at": "2026-10-21T12:00:00Z"},
            },
            cls.data_root / "overrides",
        )

    def test_overrides_replace_cached_labels(self) -> None:
        body = self.decision(BVID, ["1001", "1002"])
        self.assertNotIn("1001", body["decisions"])
        entry = body["decisions"]["1002"]
        self.assertEqual(entry["action"], "hide")
        self.assertEqual(entry["label"], "attack")
        self.assertEqual(entry["score"], 1.0)
        self.assertEqual(entry["reason"], "pref_hide_attack")
        self.assertEqual(entry["evidence"], "user correction")


class FeedbackTest(ServerTestBase):
    def reply(self, payload: dict):
        return self.call("POST", "/api/v1/feedback", payload)

    def rows(self, filename: str, bvid: str) -> list[dict]:
        path = self.feedback_dir / filename
        if not path.exists():
            return []
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return [row for row in map(json.loads, lines) if row["bvid"] == bvid]

    def test_label_correction_writes_override_and_jsonl(self) -> None:
        bvid = "BV1FB00001"
        self.seed(bvid)
        before = self.decision(bvid, ["1002"])
        self.assertNotIn("1002", before["decisions"])
        status, _, body = self.reply(
            {
                "bvid": bvid,
                "dmid": "1002",
                "kind": "label_correction",
                "payload": {"text": "哈哈哈哈", "old_label": "humor", "new_label": "attack"},
            }
        )
        self.assertEqual(status, 200, body)
        self.assertTrue(body["ok"])

        overrides = cache.load_overrides(bvid, self.data_root / "overrides")
        self.assertEqual(overrides["1002"]["label"], "attack")
        self.assertTrue(overrides["1002"]["at"].endswith("Z"))
        on_disk = json.loads(
            (self.data_root / "overrides" / f"{bvid}.json").read_text(encoding="utf-8")
        )
        self.assertEqual(on_disk, {"items": overrides})

        after = self.decision(bvid, ["1002"])
        entry = after["decisions"]["1002"]
        self.assertEqual(entry["action"], "hide")
        self.assertEqual(entry["label"], "attack")
        self.assertEqual(entry["reason"], "pref_hide_attack")
        self.assertEqual(entry["evidence"], "user correction")

        rows = self.rows("label_corrections.jsonl", bvid)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["dmid"], "1002")
        self.assertEqual(rows[0]["kind"], "label_correction")
        self.assertEqual(rows[0]["payload"]["new_label"], "attack")
        self.assertTrue(rows[0]["at"].endswith("Z"))

    def test_label_correction_can_show_an_attack_item(self) -> None:
        bvid = "BV1FB00002"
        self.seed(bvid)
        self.assertIn("1001", self.decision(bvid, ["1001"])["decisions"])
        status, _, _ = self.reply(
            {
                "bvid": bvid,
                "dmid": "1001",
                "kind": "label_correction",
                "payload": {"old_label": "attack", "new_label": "humor"},
            }
        )
        self.assertEqual(status, 200)
        self.assertNotIn("1001", self.decision(bvid, ["1001"])["decisions"])

    def test_label_correction_requires_a_taxonomy_label(self) -> None:
        bvid = "BV1FB00003"
        self.seed(bvid)
        status, _, body = self.reply(
            {"bvid": bvid, "dmid": "1001", "kind": "label_correction", "payload": {}}
        )
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        status, _, _ = self.reply(
            {
                "bvid": bvid,
                "dmid": "1001",
                "kind": "label_correction",
                "payload": {"new_label": "not_a_label"},
            }
        )
        self.assertEqual(status, 400)
        self.assertEqual(cache.load_overrides(bvid, self.data_root / "overrides"), {})

    def test_policy_event_goes_to_its_own_jsonl(self) -> None:
        bvid = "BV1FB00004"
        self.seed(bvid)
        status, _, _ = self.reply(
            {
                "bvid": bvid,
                "dmid": "1001",
                "kind": "policy_event",
                "payload": {"action": "show_once", "label": "attack", "reason": "user_show"},
            }
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(self.rows("policy_events.jsonl", bvid)), 1)
        self.assertEqual(len(self.rows("label_corrections.jsonl", bvid)), 0)
        self.assertEqual(len(self.rows("prefs_events.jsonl", bvid)), 0)

    def test_prefs_event_goes_to_its_own_jsonl(self) -> None:
        bvid = "BV1FB00005"
        self.seed(bvid)
        status, _, _ = self.reply(
            {
                "bvid": bvid,
                "kind": "prefs",
                "payload": {"per_type": {"attack": "show"}, "min_confidence": 0.5},
            }
        )
        self.assertEqual(status, 200)
        rows = self.rows("prefs_events.jsonl", bvid)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["payload"]["per_type"]["attack"], "show")
        self.assertEqual(len(self.rows("policy_events.jsonl", bvid)), 0)

    def test_unknown_feedback_kind_is_rejected(self) -> None:
        status, _, body = self.reply({"bvid": BVID, "dmid": "1001", "kind": "nonsense"})
        self.assertEqual(status, 400)
        self.assertIn("unknown feedback kind", body["error"])

    def test_concurrent_corrections_are_not_lost(self) -> None:
        bvid = "BV1FB00006"
        self.seed(bvid)

        def correct(index: int) -> int:
            status, _, _ = self.reply(
                {
                    "bvid": bvid,
                    "dmid": f"20{index:02d}",
                    "kind": "label_correction",
                    "payload": {"new_label": "humor"},
                }
            )
            return status

        with ThreadPoolExecutor(max_workers=8) as pool:
            statuses = list(pool.map(correct, range(8)))
        self.assertEqual(statuses, [200] * 8)
        self.assertEqual(len(cache.load_overrides(bvid, self.data_root / "overrides")), 8)
        self.assertEqual(len(self.rows("label_corrections.jsonl", bvid)), 8)


class MissTest(ServerTestBase):
    def test_miss_is_idempotent_and_deduped(self) -> None:
        bvid = "BV1MISS0001"
        status, _, body = self.call(
            "POST", "/api/v1/miss", {"bvid": bvid, "dmids": ["111", "222", "111"]}
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["added"], 2)
        self.assertEqual(body["pending"], 2)
        status, _, body = self.call(
            "POST", "/api/v1/miss", {"bvid": bvid, "dmids": ["111", "222"]}
        )
        self.assertEqual(body["added"], 0)
        self.assertEqual(body["pending"], 2)
        self.assertEqual(cache.load_pending(bvid, self.data_root / "pending"), ["111", "222"])


class TagsTest(ServerTestBase):
    def test_tags_truncates_items(self) -> None:
        status, _, body = self.call("GET", f"/api/v1/tags?bvid={BVID}&limit=2")
        self.assertEqual(status, 200)
        self.assertEqual(body["payload"]["schema"], 2)
        self.assertEqual(len(body["payload"]["items"]), 2)
        self.assertEqual(body["payload"]["bvid"], BVID)

    def test_tags_default_limit_returns_all(self) -> None:
        body = self.call("GET", f"/api/v1/tags?bvid={BVID}")[2]
        self.assertEqual(len(body["payload"]["items"]), len(ITEMS))

    def test_tags_missing_cache_returns_null_payload(self) -> None:
        body = self.call("GET", "/api/v1/tags?bvid=BV1NOPE")[2]
        self.assertTrue(body["ok"])
        self.assertIsNone(body["payload"])

    def test_tags_rejects_bad_query(self) -> None:
        self.assertEqual(self.call("GET", "/api/v1/tags")[0], 400)
        self.assertEqual(self.call("GET", "/api/v1/tags?bvid=../evil")[0], 400)
        self.assertEqual(self.call("GET", f"/api/v1/tags?bvid={BVID}&limit=abc")[0], 400)
        self.assertEqual(self.call("GET", f"/api/v1/tags?bvid={BVID}&limit=-1")[0], 400)


class OnlineFallbackTest(ServerTestBase):
    """Videos without an offline cache are tagged on the fly from posted texts."""

    BVID = "BV1ONLINE001"

    def post_decisions(self, dmids, texts, prefs=None, bvid=None):
        payload = {"bvid": bvid or self.BVID, "dmids": dmids, "texts": texts}
        if prefs is not None:
            payload["prefs"] = prefs
        return self.call("POST", "/api/v1/decisions", payload)

    def test_uncached_danmaku_is_classified_online(self):
        status, _, body = self.post_decisions(["9001"], {"9001": "up主就是个傻逼"})
        self.assertEqual(status, 200, body)
        self.assertFalse(body["found"])
        self.assertEqual(body["miss"], [])
        self.assertEqual(body["online"], 1)
        self.assertEqual(body["decisions"]["9001"]["action"], "hide")
        self.assertEqual(body["decisions"]["9001"]["label"], "attack")
        self.assertEqual(body["decisions"]["9001"]["text"], "up主就是个傻逼")

    def test_online_items_are_persisted_and_reused(self):
        bvid = "BV1ONLINE002"
        self.post_decisions(["9101"], {"9101": "后面会有剧透，凶手是他"}, bvid=bvid)
        cached = cache.load_tags(bvid, self.tags_dir)
        self.assertIsNotNone(cached)
        self.assertEqual(cached["backend"], "rules-online")
        self.assertEqual(cached["items"]["9101"]["source"], "rules-online")
        self.assertEqual(cached["items"]["9101"]["dominant"], "spoiler")

        status, _, body = self.post_decisions(["9101"], {"9101": "后面会有剧透"}, bvid=bvid)
        self.assertEqual(status, 200, body)
        self.assertTrue(body["found"])
        self.assertEqual(body["online"], 0)
        self.assertEqual(body["decisions"]["9101"]["action"], "hide")

    def test_online_items_respect_user_prefs(self):
        prefs = {"per_type": {"attack": "show"}}
        status, _, body = self.post_decisions(["9201"], {"9201": "脑残一个"}, prefs, bvid="BV1ONLINE003")
        self.assertEqual(status, 200, body)
        self.assertNotIn("9201", body["decisions"])

    def test_unmatched_text_stays_other_and_visible(self):
        status, _, body = self.post_decisions(["9301"], {"9301": "今天天气不错"}, bvid="BV1ONLINE004")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["online"], 1)
        self.assertNotIn("9301", body["decisions"])
        # low-confidence other is still stored for the next visit
        self.assertIn("9301", cache.load_tags("BV1ONLINE004", self.tags_dir)["items"])

    def test_ids_without_text_are_still_misses(self):
        status, _, body = self.post_decisions(["9401"], {}, bvid="BV1ONLINE005")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["miss"], ["9401"])
        self.assertEqual(body["online"], 0)

    def test_texts_must_be_an_object(self):
        status, _, body = self.call(
            "POST", "/api/v1/decisions", {"bvid": self.BVID, "dmids": ["1"], "texts": [1]}
        )
        self.assertEqual(status, 400)
        self.assertIn("texts", body["error"])


class ErrorsTest(ServerTestBase):
    def test_unknown_path_returns_404(self) -> None:
        status, _, body = self.call("GET", "/api/v1/nope")
        self.assertEqual(status, 404)
        self.assertFalse(body["ok"])

    def test_wrong_method_returns_405(self) -> None:
        self.assertEqual(self.call("POST", "/api/v1/health", {})[0], 405)
        self.assertEqual(self.call("GET", "/api/v1/decisions")[0], 405)

    def test_invalid_json_returns_400(self) -> None:
        status, _, body = self.call("POST", "/api/v1/decisions", raw="not json")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])

    def test_invalid_requests_return_400(self) -> None:
        url = "/api/v1/decisions"
        self.assertEqual(self.call("POST", url, {})[0], 400)
        self.assertEqual(self.call("POST", url, {"bvid": BVID, "dmids": "nope"})[0], 400)
        self.assertEqual(self.call("POST", url, {"bvid": BVID, "dmids": [1, 2]})[0], 400)
        self.assertEqual(self.call("POST", url, {"bvid": "../evil", "dmids": []})[0], 400)
        self.assertEqual(
            self.call("POST", url, {"bvid": BVID, "dmids": ["1"], "prefs": [1]})[0], 400
        )

    def test_no_body_returns_400(self) -> None:
        status, _, _ = self.call("POST", "/api/v1/decisions")
        self.assertEqual(status, 400)

    def test_too_many_dmids_returns_400(self) -> None:
        dmids = [str(index) for index in range(20_001)]
        status, _, body = self.call("POST", "/api/v1/decisions", {"bvid": BVID, "dmids": dmids})
        self.assertEqual(status, 400)
        self.assertIn("too many dmids", body["error"])


if __name__ == "__main__":
    unittest.main()

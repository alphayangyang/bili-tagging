"""Tests for scripts/study/analyze.py (pure stdlib, synthetic data only)."""
from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

STUDY_DIR = Path(__file__).resolve().parents[1] / "scripts" / "study"
if str(STUDY_DIR) not in sys.path:
    sys.path.insert(0, str(STUDY_DIR))

import analyze  # noqa: E402

from danmaku_filter.classify.linear import LinearTagger  # noqa: E402

QUESTIONNAIRE_COLUMNS = [
    "participant", "condition", "video", "genre", "density", "block_order", "familiar",
    "discomfort_1", "discomfort_2", "discomfort_3", "discomfort_4", "discomfort_5",
    "coview_1", "coview_2", "coview_3",
    "control_1", "control_2", "control_3",
    "fomo", "nasa_1", "nasa_2", "nasa_3", "nasa_4", "nasa_5", "nasa_6",
    "comp_1", "comp_2", "comp_3", "comp_4", "comp_5",
    "recall", "explain_1", "explain_2", "overall_1", "overall_2",
    "expectancy_1", "expectancy_2", "notes",
]


def _write_csv(path: Path, rows: list[dict], columns: list[str] | None = None) -> None:
    columns = columns or QUESTIONNAIRE_COLUMNS
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in columns})


def _row(**overrides) -> dict:
    row = {
        "participant": "P01", "condition": "A", "video": "BVtestA",
        "genre": "information", "density": "high", "block_order": 1, "familiar": "0",
        "discomfort_1": 3, "discomfort_2": 3, "discomfort_3": 3, "discomfort_4": 3, "discomfort_5": 3,
        "coview_1": 2, "coview_2": 2, "coview_3": 2,
        "control_1": 4, "control_2": 4, "control_3": 4,
        "fomo": 2,
        "nasa_1": 2, "nasa_2": 2, "nasa_3": 2, "nasa_4": 2, "nasa_5": 2, "nasa_6": 2,
        "comp_1": 2, "comp_2": 2, "comp_3": 2, "comp_4": 2, "comp_5": 2,
        "recall": "记得一点", "explain_1": 1, "explain_2": 1, "overall_1": 2, "overall_2": 2,
        "expectancy_1": 2, "expectancy_2": 2,
        "notes": "",
    }
    row.update(overrides)
    return row


def _session(participant: str = "P01", condition: str = "A", **overrides) -> dict:
    session = {
        "participant": participant,
        "condition": condition,
        "started_at": "2026-11-10T10:00:00Z",
        "ended_at": "2026-11-10T10:05:00Z",
        "wall_seconds": 300.0,
        "counts": {"shown": 3, "hidden": 1, "collapsed": 1, "by_label": {"attack": 1}},
        "events": 4,
        "type_counts": {"segment": 2, "correction": 1, "restore": 0, "expand": 0, "pref": 1},
        "latency_ms": [10.0, 20.0],
        "bvids": ["BVtestA"],
        "online_segments": 0,
        "truncated": False,
    }
    session.update(overrides)
    return session


def _manifest(path: Path) -> None:
    stimuli = [
        {"bvid": "BVtestA", "duration_s": 200.0},
        {"bvid": "BVtestB", "duration_s": 200.0},
        {"bvid": "BVtestC", "duration_s": 200.0},
        {"bvid": "BVtestD", "duration_s": 200.0},
    ]
    path.write_text(json.dumps({"schema": 1, "stimuli": stimuli}), encoding="utf-8")


class ParsingTest(unittest.TestCase):
    def test_number_and_truthy(self) -> None:
        self.assertEqual(analyze._number("3.5"), 3.5)
        self.assertEqual(analyze._number(4), 4.0)
        for blank in ("", "NA", "n/a", "-", None, "abc"):
            self.assertIsNone(analyze._number(blank))
        self.assertIs(analyze._truthy("yes"), True)
        self.assertIs(analyze._truthy("看过"), True)
        self.assertIs(analyze._truthy("0"), False)
        self.assertIs(analyze._truthy(2), True)
        self.assertIsNone(analyze._truthy(""))
        self.assertIsNone(analyze._truthy("maybe"))

    def test_parse_time_handles_z_and_bad_values(self) -> None:
        parsed = analyze._parse_time("2026-11-10T10:00:00Z")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.hour, 10)
        self.assertIsNone(analyze._parse_time("not-a-time"))
        self.assertIsNone(analyze._parse_time(""))


class QuestionnaireTest(unittest.TestCase):
    def test_reads_scales_and_tolerates_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "questionnaire.csv"
            columns = ["participant_id", "cond", "bvid"] + QUESTIONNAIRE_COLUMNS[3:]
            _write_csv(path, [
                _row(participant_id="P07", cond="B", bvid="BVx", discomfort_1=4, discomfort_2=4,
                     discomfort_3=4, discomfort_4=4, discomfort_5=4),
                _row(participant_id="", cond="A"),
                _row(participant="P08", condition="Z"),
            ], columns=columns)
            rows, problems = analyze.load_questionnaire(path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["participant"], "P07")
        self.assertEqual(rows[0]["condition"], "B")
        self.assertEqual(rows[0]["video"], "BVx")
        self.assertAlmostEqual(rows[0]["outcomes"]["discomfort"], 4.0)
        self.assertAlmostEqual(rows[0]["outcomes"]["coview"], 2.0)
        self.assertEqual(rows[0]["outcomes"]["fomo"], 2.0)
        self.assertAlmostEqual(rows[0]["outcomes"]["expectancy"], 2.0)
        self.assertEqual(len(problems), 2)

    def test_reads_four_conditions_and_expectancy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "questionnaire.csv"
            alias_row = _row(participant="P11", condition="D")
            alias_row.pop("expectancy_1")
            alias_row.pop("expectancy_2")
            alias_row.update({"expect_1": 3, "expect_2": 5})
            _write_csv(
                path,
                [
                    _row(participant="P09", condition="D", expectancy_1=4, expectancy_2=5),
                    _row(participant="P10", condition="sham", expectancy_1="", expectancy_2=""),
                    alias_row,
                ],
                columns=QUESTIONNAIRE_COLUMNS + ["expect_1", "expect_2"],
            )
            rows, problems = analyze.load_questionnaire(path)
        self.assertEqual([row["condition"] for row in rows], ["D", "D", "D"])
        self.assertAlmostEqual(rows[0]["outcomes"]["expectancy"], 4.5)
        self.assertIsNone(rows[1]["outcomes"]["expectancy"])
        # Item-column aliases (expect_1 / expect_2) are accepted too.
        self.assertAlmostEqual(rows[2]["outcomes"]["expectancy"], 4.0)
        self.assertEqual(problems, [])

    def test_missing_file_is_reported(self) -> None:
        rows, problems = analyze.load_questionnaire("/nonexistent/questionnaire.csv")
        self.assertEqual(rows, [])
        self.assertTrue(problems)

    def test_outcomes_need_half_the_items(self) -> None:
        row = _row(discomfort_1=3, discomfort_2="", discomfort_3="", discomfort_4="", discomfort_5="")
        canonical = analyze._canonicalise(row)
        outcomes = analyze._outcomes_from_row(canonical)
        self.assertIsNone(outcomes["discomfort"])
        self.assertEqual(outcomes["fomo"], 2.0)


class SessionsTest(unittest.TestCase):
    def _write_session(self, root: Path, name: str, payload: dict) -> None:
        target = root / "raw" / "P01" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def test_loads_and_summarises_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = {
                "schema": 1,
                "study_id": "P01-A",
                "participant": "P01",
                "condition": "A",
                "started_at": "2026-11-10T10:00:00Z",
                "ended_at": "2026-11-10T10:05:00Z",
                "prefs": {"min_confidence": 0.6},
                "counts": {"shown": 2, "hidden": 1, "collapsed": 0, "by_label": {"attack": 1}},
                "events": [
                    {"t": 1, "type": "segment", "bvid": "BV1", "latency_ms": 12.5, "online": 1},
                    {"t": 2, "type": "segment", "bvid": "BV1", "latency_ms": 7.5, "online": 0},
                    {"t": 3, "type": "correction", "dmid": "1", "from": "humor", "to": "spoiler"},
                    {"t": 4, "type": "pref", "key": "min_confidence", "from": 0.6, "to": 0.7},
                ],
            }
            self._write_session(root, "dmf-study-P01-A.json", payload)
            (root / "raw" / "P01" / "dmf-study-P02-B.json").write_text("{bad", encoding="utf-8")
            sessions, problems = analyze.load_sessions(root / "raw")
        self.assertEqual(len(sessions), 1)
        session = sessions[0]
        self.assertEqual(session["participant"], "P01")
        self.assertEqual(session["condition"], "A")
        self.assertAlmostEqual(session["wall_seconds"], 300.0)
        self.assertEqual(session["latency_ms"], [12.5, 7.5])
        self.assertEqual(session["bvids"], ["BV1"])
        self.assertEqual(session["online_segments"], 1)
        self.assertEqual(session["type_counts"]["correction"], 1)
        self.assertEqual(session["events"], 4)
        self.assertTrue(problems)

    def test_derives_identity_from_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_session(root, "dmf-study-P03-C.json", {"schema": 1, "events": []})
            sessions, problems = analyze.load_sessions(root / "raw")
        self.assertEqual(sessions[0]["participant"], "P03")
        self.assertEqual(sessions[0]["condition"], "C")
        self.assertEqual(problems, [])

    def test_canonical_condition_ids_map_to_letters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_session(root, "dmf-study-P04-all_on.json",
                                {"schema": 1, "condition": "all_on", "events": []})
            self._write_session(root, "dmf-study-P05-keyword.json",
                                {"schema": 1, "condition": "keyword", "events": []})
            self._write_session(root, "dmf-study-P06-prototype.json",
                                {"schema": 1, "condition": "prototype", "events": []})
            self._write_session(root, "dmf-study-P07-.json",
                                {"schema": 1, "condition_label": "C", "events": []})
            self._write_session(root, "dmf-study-P08-sham.json",
                                {"schema": 1, "condition": "sham", "events": []})
            sessions, problems = analyze.load_sessions(root / "raw")
        by_participant = {session["participant"]: session["condition"] for session in sessions}
        self.assertEqual(by_participant, {"P04": "A", "P05": "B", "P06": "C", "P07": "C", "P08": "D"})
        self.assertEqual(problems, [])

    def test_questionnaire_accepts_canonical_condition_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "q.csv"
            _write_csv(path, [_row(condition="all_on"), _row(condition="prototype", block_order=2),
                               _row(condition="sham", block_order=3)])
            rows, problems = analyze.load_questionnaire(path)
        self.assertEqual([row["condition"] for row in rows], ["A", "C", "D"])
        self.assertEqual(problems, [])

    def test_missing_raw_dir(self) -> None:
        sessions, problems = analyze.load_sessions("/nonexistent/raw")
        self.assertEqual(sessions, [])
        self.assertTrue(problems)


class CleanDataTest(unittest.TestCase):
    def _rows(self) -> list[dict]:
        rows = []
        for participant in ("P01", "P02"):
            for condition in ("A", "B", "C", "D"):
                for suffix in ("1", "2"):
                    rows.append({
                        "participant": participant,
                        "condition": condition,
                        "video": "BVtest%s%s" % (condition, suffix),
                        "genre": "information",
                        "density": "high",
                        "familiar": "yes" if participant == "P02" else "0",
                        "outcomes": {"discomfort": 3.0, "control": 3.0},
                    })
        return rows

    def test_excludes_familiar_participants_and_short_dwell(self) -> None:
        rows = self._rows()
        sessions = [
            _session(
                participant="P01",
                condition=condition,
                wall_seconds=60.0 if condition == "A" else 300.0,
                bvids=["BVtest%s1" % condition, "BVtest%s2" % condition],
            )
            for condition in ("A", "B", "C", "D")
        ]
        durations = {row["video"]: 200.0 for row in rows}
        included, cleaning = analyze.clean_data(rows, sessions, durations)
        self.assertEqual(cleaning["excluded_participants"], ["P02"])
        p01 = [row for row in included if row["participant"] == "P01"]
        self.assertEqual({row["condition"] for row in p01}, {"B", "C", "D"})
        self.assertEqual(len(p01), 6)
        short = [entry for entry in cleaning["excluded"] if entry["participant"] == "P01"]
        self.assertEqual(len(short), 2)
        self.assertIn("dwell", short[0]["reasons"][0])
        self.assertEqual(cleaning["included_participants"], 1)
        self.assertEqual(cleaning["session_logs"], 4)
        self.assertEqual(list(cleaning["rows_by_condition"]), ["B", "C", "D"])

    def test_missing_session_is_a_note_not_an_exclusion(self) -> None:
        rows = self._rows()[:1]
        rows[0]["familiar"] = "0"
        included, cleaning = analyze.clean_data(rows, [], {})
        self.assertEqual(len(included), 1)
        self.assertTrue(any("no session log" in note for note in cleaning["notes"]))

    def test_row_without_scale_answers_is_excluded(self) -> None:
        row = {"participant": "P09", "condition": "A", "video": "BVx", "familiar": "0",
               "outcomes": {name: None for name in analyze.OUTCOMES}}
        included, cleaning = analyze.clean_data([row], [], {})
        self.assertEqual(included, [])
        self.assertIn("no scale responses", cleaning["excluded"][0]["reasons"][0])


class StatisticsTest(unittest.TestCase):
    def test_wilcoxon_all_positive(self) -> None:
        result = analyze.wilcoxon_signed_rank([1, 2, 3, 4, 5])
        self.assertEqual(result["n"], 5)
        self.assertEqual(result["statistic"], 0.0)
        self.assertAlmostEqual(result["z"], 2.023, delta=0.01)
        self.assertAlmostEqual(result["p"], 0.0431, delta=0.002)

    def test_wilcoxon_handles_zeros_and_ties(self) -> None:
        zeros = analyze.wilcoxon_signed_rank([0.0, 0.0])
        self.assertEqual(zeros["n"], 0)
        self.assertEqual(zeros["p"], 1.0)
        ties = analyze.wilcoxon_signed_rank([1.0, -1.0, 2.0, -2.0])
        self.assertEqual(ties["n"], 4)
        self.assertEqual(ties["statistic"], 5.0)
        self.assertAlmostEqual(ties["p"], 1.0)

    def test_wilcoxon_matches_scipy_reference(self) -> None:
        # Reference values from scipy.stats.wilcoxon(..., correction=False, mode="approx")
        # on the same differences; hard-coded so production keeps no scipy dependency.
        ties = [1.0, 1.0, 2.0, 2.0, 3.0, -1.0, -1.0, 0.5, 0.5, 0.0]
        result = analyze.wilcoxon_signed_rank(ties)
        self.assertEqual(result["n"], 9)
        self.assertAlmostEqual(result["p"], 0.1060, delta=0.001)
        self.assertAlmostEqual(result["z"], 1.616, delta=0.01)
        small = analyze.wilcoxon_signed_rank([2.0, 1.5, -0.5, 3.0, -2.0, 0.5])
        self.assertAlmostEqual(small["p"], 0.3428, delta=0.001)
        self.assertEqual(small["statistic"], 6.0)

    def test_holm_reference_example(self) -> None:
        # Holm on 5 p-values: p_i * (5 - i) with a running maximum.
        adjusted = analyze.holm([0.001, 0.02, 0.04, 0.2, 0.5])
        self.assertEqual(adjusted, [0.005, 0.08, 0.12, 0.4, 0.5])

    def test_holm_adjusts_and_stays_monotone(self) -> None:
        adjusted = analyze.holm([0.01, 0.04, 0.03])
        self.assertEqual(adjusted, [0.03, 0.06, 0.06])
        self.assertEqual(analyze.holm([]), [])
        self.assertEqual(analyze.holm([0.5]), [0.5])

    def test_cohens_d_and_bootstrap(self) -> None:
        self.assertAlmostEqual(analyze.cohens_d([1.0, 2.0, 3.0]), 2.0)
        self.assertIsNone(analyze.cohens_d([1.0]))
        self.assertIsNone(analyze.cohens_d([1.0, 1.0]))
        self.assertEqual(analyze.bootstrap_ci([2.0, 2.0, 2.0], samples=50), [2.0, 2.0])
        self.assertIsNone(analyze.bootstrap_ci([1.0], samples=50))

    def test_cronbach_alpha(self) -> None:
        item_rows = [[1, 2, 3], [2, 3, 4], [3, 4, 5], [4, 5, 6]]
        self.assertAlmostEqual(analyze.cronbach_alpha(item_rows), 1.0)
        self.assertIsNone(analyze.cronbach_alpha(item_rows[:2]))
        self.assertIsNone(analyze.cronbach_alpha([[1, None], [2, 3], [3, 4]]))


class DescriptivesTest(unittest.TestCase):
    def _rows(self) -> list[dict]:
        def outcomes(**values):
            base = {name: None for name in analyze.OUTCOMES}
            base.update(values)
            return base

        return [
            {"participant": "P01", "condition": "A", "video": "BV1", "genre": "information",
             "density": "high", "outcomes": outcomes(discomfort=2.0, control=3.0, fomo=2.0),
             "discomfort_items": [2, 2, 2, 2, 2]},
            {"participant": "P02", "condition": "A", "video": "BV1", "genre": "information",
             "density": "high", "outcomes": outcomes(discomfort=4.0, control=3.0, fomo=2.0),
             "discomfort_items": [4, 4, 4, 4, 4]},
            {"participant": "P01", "condition": "B", "video": "BV2", "genre": "entertainment",
             "density": "low", "outcomes": outcomes(discomfort=3.0, control=5.0, fomo=3.0),
             "discomfort_items": [3, 3, 3, 3, 3]},
            {"participant": "P03", "condition": "D", "video": "BV3", "genre": "entertainment",
             "density": "low", "outcomes": outcomes(discomfort=1.0, expectancy=2.0),
             "discomfort_items": [1, 1, 1, 1, 1]},
        ]

    def test_by_condition_summary(self) -> None:
        result = analyze.descriptives(self._rows())
        condition_a = result["by_condition"]["A"]
        self.assertEqual(condition_a["n_blocks"], 2)
        self.assertEqual(condition_a["n_participants"], 2)
        self.assertAlmostEqual(condition_a["outcomes"]["discomfort"]["mean"], 3.0)
        self.assertIsNone(condition_a["outcomes"]["expectancy"]["mean"])
        # Conditions missing from the data are omitted, not zero-filled.
        self.assertNotIn("C", result["by_condition"])
        self.assertEqual(list(result["by_condition"]), ["A", "B", "D"])
        self.assertAlmostEqual(result["by_condition"]["D"]["outcomes"]["expectancy"]["mean"], 2.0)
        self.assertEqual(result["by_video"]["BV1"]["n_blocks"], 2)

    def test_condition_order_is_known_then_alphabetical(self) -> None:
        rows = [
            {"participant": "P01", "condition": "Z", "video": "BVz", "outcomes": {}},
            {"participant": "P02", "condition": "D", "video": "BVd", "outcomes": {}},
            {"participant": "P03", "condition": "A", "video": "BVa", "outcomes": {}},
            {"participant": "P04", "condition": "B", "video": "BVb", "outcomes": {}},
            {"participant": "P05", "condition": "E", "video": "BVe", "outcomes": {}},
        ]
        result = analyze.descriptives(rows)
        self.assertEqual(list(result["by_condition"]), ["A", "B", "D", "E", "Z"])

    def test_contrasts_cover_six_pairs(self) -> None:
        rows = []
        for index in range(8):
            participant = "P%02d" % index
            for condition, score in (("A", 4.0), ("B", 3.0), ("C", 2.0), ("D", 2.5)):
                rows.append({
                    "participant": participant, "condition": condition, "video": "BV" + condition,
                    "outcomes": {"discomfort": score}, "discomfort_items": [score] * 5,
                })
        result = analyze.planned_contrasts(rows, samples=50)
        entries = result["discomfort"]
        self.assertEqual(len(entries), 6)
        self.assertEqual(
            [entry["contrast"] for entry in entries],
            ["C vs A", "C vs B", "C vs D", "D vs A", "D vs B", "B vs A"],
        )
        c_vs_a = entries[0]
        self.assertEqual(c_vs_a["n"], 8)
        self.assertAlmostEqual(c_vs_a["mean_diff"], -2.0)
        self.assertEqual(c_vs_a["ci95"], [-2.0, -2.0])
        self.assertAlmostEqual(entries[2]["mean_diff"], -0.5)
        self.assertAlmostEqual(entries[3]["mean_diff"], -1.5)
        self.assertIsNone(c_vs_a["d"])
        # Holm correction covers all six planned contrasts, not just the primary pair.
        for entry in entries:
            self.assertIn("p_holm", entry)
        expected = analyze.holm([entry["wilcoxon"]["p"] for entry in entries])
        for entry, adjusted in zip(entries, expected):
            self.assertAlmostEqual(entry["p_holm"], adjusted)

    def test_reliability(self) -> None:
        rows = [
            {
                "participant": "P0%d" % (index + 1),
                "condition": "A",
                "video": "BV1",
                "outcomes": {name: 3.0 for name in analyze.OUTCOMES},
                "discomfort_items": [index + 1] * 5,
            }
            for index in range(3)
        ]
        result = analyze.reliability(rows)
        self.assertIn("discomfort", result["A"])
        self.assertIsNotNone(result["A"]["discomfort"])
        self.assertNotIn("fomo", result["A"])

    def test_rq1_groups_condition_a(self) -> None:
        result = analyze.rq1_video_level(self._rows())
        self.assertEqual(result["n_blocks"], 2)
        self.assertEqual(result["rows"][0]["genre"], "information")
        self.assertAlmostEqual(result["rows"][0]["means"]["discomfort"], 3.0)


class Rq2Test(unittest.TestCase):
    def test_latency_and_behaviour(self) -> None:
        sessions = [
            _session("P01", "A", latency_ms=[10.0, 20.0, 30.0], wall_seconds=600.0,
                     type_counts={"segment": 3, "correction": 2, "restore": 1, "expand": 3, "pref": 4},
                     online_segments=2),
            _session("P02", "B", latency_ms=[100.0], wall_seconds=300.0,
                     type_counts={"segment": 1, "correction": 1}),
        ]
        result = analyze.rq2_from_sessions(sessions)
        self.assertEqual(result["latency"]["A"]["n"], 3)
        self.assertAlmostEqual(result["latency"]["A"]["p50_ms"], 20.0)
        self.assertEqual(result["latency"]["C"]["n"], 0)
        self.assertEqual(result["latency_overall"]["n"], 4)
        self.assertEqual(result["behaviour"]["corrections"], 3)
        self.assertAlmostEqual(result["behaviour"]["corrections_per_10min"], 2.0)
        self.assertAlmostEqual(result["online_share"], 2 / 4)
        self.assertAlmostEqual(result["session_minutes"], 15.0)
    def test_applied_and_counterfactual_counts_stay_separate(self) -> None:
        # A/B sessions never rewrite the stream, so their applied hidden/collapsed
        # are zero and the prototype's policy result lives in would_*. RQ2 keeps
        # both so the behaviour table cannot mistake a counterfactual for reality.
        sessions = [
            _session("P01", "A", counts={"shown": 991, "hidden": 0, "collapsed": 0, "thinned": 0,
                                              "would_shown": 972, "would_hidden": 19, "would_collapsed": 0}),
            _session("P02", "C", counts={"shown": 303, "hidden": 42, "collapsed": 0, "thinned": 0},
                                 bvids=["BVtestC"]),
        ]
        result = analyze.rq2_from_sessions(sessions)
        self.assertEqual(result["decisions"]["A"]["shown"], 991)
        self.assertEqual(result["decisions"]["A"]["hidden"], 0)
        self.assertEqual(result["decisions"]["A"]["would_hidden"], 19)
        self.assertEqual(result["decisions"]["C"]["hidden"], 42)
        self.assertNotIn("would_hidden", result["decisions"]["C"])


class GoldTest(unittest.TestCase):
    def test_gold_evaluation_uses_per_video_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = root / "models"
            models.mkdir()
            texts = ["太好笑了 哈哈" if index % 2 else "这段信息很有用" for index in range(24)]
            labels = ["humor" if index % 2 else "information" for index in range(24)]
            LinearTagger(kind="centroid").train(texts, labels).save(models / "BVgold.json")
            gold = root / "gold.jsonl"
            rows = [
                {"bvid": "BVgold", "text": texts[1], "label": "humor"},
                {"bvid": "BVgold", "text": texts[0], "label": "information"},
            ]
            gold.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
            result = analyze.evaluate_gold(gold, models)
        self.assertTrue(result["available"])
        self.assertEqual(result["n"], 2)
        self.assertEqual(result["accuracy"], 1.0)
        self.assertEqual(result["skipped_rows"], 0)
        self.assertIn("humor", result["per_tag"])
        self.assertIsNotNone(result["macro_f1"])

    def test_missing_gold_and_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(analyze.evaluate_gold(Path(tmp) / "none.jsonl", Path(tmp) / "models")["available"])
            gold = Path(tmp) / "gold.jsonl"
            gold.write_text(json.dumps({"bvid": "BVx", "text": "a", "label": "humor"}) + "\n", encoding="utf-8")
            result = analyze.evaluate_gold(gold, Path(tmp) / "models")
        self.assertFalse(result["available"])

    def test_double_annotation_alpha(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "double.jsonl"
            rows = [{"label_a": "humor", "label_b": "humor"} for _ in range(3)]
            rows.append({"label_a": "humor", "label_b": "spoiler"})
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            result = analyze.evaluate_double_annotation(path)
        self.assertTrue(result["available"])
        self.assertEqual(result["agreement"], 0.75)
        # 3 agree + 1 disagree over binary-ish labels: reference alpha is 0.0
        # (krippendorff 0.9.0, nominal, same pairs).
        self.assertAlmostEqual(result["krippendorff_alpha"], 0.0, delta=0.01)

    def test_krippendorff_handles_empty_input(self) -> None:
        self.assertIsNone(analyze.krippendorff_alpha_nominal([]))
        self.assertIsNone(analyze.krippendorff_alpha_nominal([("humor", "humor")]))
        self.assertEqual(analyze.krippendorff_alpha_nominal([("a", "a"), ("b", "b")]), 1.0)

    def test_krippendorff_matches_reference_values(self) -> None:
        # Expected values computed with the krippendorff 0.9.0 package
        # (level_of_measurement="nominal") on the same pairs; hard-coded so the
        # production script keeps no third-party dependency.
        eight_of_ten = [("a", "a")] * 4 + [("b", "b")] * 4 + [("a", "b"), ("b", "a")]
        six_of_ten = [("a", "a")] * 3 + [("b", "b")] * 3 + [("a", "b")] * 2 + [("b", "a")] * 2
        six_units_mixed = [
            ("a", "a"),
            ("b", "a"),
            ("c", "c"),
            ("b", "b"),
            ("c", "a"),
            ("a", "b"),
        ]
        three_labels = [
            ("x", "x"),
            ("y", "z"),
            ("z", "z"),
            ("x", "y"),
            ("y", "y"),
            ("z", "x"),
            ("x", "x"),
            ("y", "z"),
        ]
        self.assertAlmostEqual(analyze.krippendorff_alpha_nominal(eight_of_ten), 0.620, delta=0.001)
        self.assertAlmostEqual(analyze.krippendorff_alpha_nominal(six_of_ten), 0.240, delta=0.001)
        self.assertAlmostEqual(analyze.krippendorff_alpha_nominal(six_units_mixed), 0.298, delta=0.001)
        self.assertAlmostEqual(analyze.krippendorff_alpha_nominal(three_labels), 0.294, delta=0.001)


class EndToEndTest(unittest.TestCase):
    def _build(self, root: Path) -> tuple[Path, Path, Path]:
        questionnaire = root / "questionnaire.csv"
        rows = []
        for participant in ("P01", "P02", "P03"):
            for condition in ("A", "B", "C", "D"):
                rows.append(_row(participant=participant, condition=condition,
                                 video="BVtest" + condition))
        _write_csv(questionnaire, rows)
        raw = root / "raw"
        for participant in ("P01", "P02", "P03"):
            for condition in ("A", "B", "C", "D"):
                payload = {
                    "schema": 1, "study_id": participant + "-" + condition,
                    "participant": participant, "condition": condition,
                    "started_at": "2026-11-10T10:00:00Z", "ended_at": "2026-11-10T10:05:00Z",
                    "counts": {"shown": 3, "hidden": 1, "collapsed": 0, "by_label": {"attack": 1}},
                    "events": [
                        {"t": 1, "type": "segment", "bvid": "BVtest" + condition, "latency_ms": 12.0, "online": 0},
                        {"t": 2, "type": "correction", "dmid": "1", "from": "humor", "to": "spoiler"},
                    ],
                }
                target = raw / participant / ("dmf-study-%s-%s.json" % (participant, condition))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(payload), encoding="utf-8")
        report = root / "report.json"
        _manifest(report)
        return questionnaire, raw, report

    def test_main_writes_json_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            questionnaire, raw, report = self._build(root)
            out = root / "analysis.json"
            markdown = root / "analysis.md"
            code = analyze.main([
                "--raw-dir", str(raw),
                "--questionnaire", str(questionnaire),
                "--study-report", str(report),
                "--gold", str(root / "missing_gold.jsonl"),
                "--gold-double", str(root / "missing_double.jsonl"),
                "--models-dir", str(root / "models"),
                "--out", str(out),
                "--markdown", str(markdown),
                "--bootstrap", "100",
                "--quiet",
            ])
            self.assertEqual(code, 0)
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema"], 1)
            self.assertEqual(payload["cleaning"]["included_rows"], 12)
            self.assertEqual(payload["cleaning"]["session_logs"], 12)
            self.assertEqual(payload["cleaning"]["rows_by_condition"]["D"], 3)
            self.assertEqual(list(payload["descriptives"]["by_condition"]), ["A", "B", "C", "D"])
            self.assertEqual(len(payload["contrasts"]["discomfort"]), 6)
            self.assertIn("expectancy", payload["contrasts"])
            self.assertEqual(payload["rq2"]["latency"]["A"]["n"], 3)
            self.assertEqual(payload["rq2"]["latency"]["D"]["n"], 3)
            self.assertFalse(payload["rq2"]["gold"]["available"])
            self.assertFalse(payload["rq2"]["double_annotation"]["available"])
            text = markdown.read_text(encoding="utf-8")
        self.assertIn("Data cleaning", text)
        self.assertIn("Planned contrasts", text)
        self.assertIn("Holm over 6", text)
        self.assertIn("RQ2: latency", text)

    def test_missing_inputs_still_produce_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code = analyze.main([
                "--raw-dir", str(root / "no_raw"),
                "--questionnaire", str(root / "no.csv"),
                "--study-report", str(root / "no_report.json"),
                "--gold", str(root / "no_gold.jsonl"),
                "--gold-double", str(root / "no_double.jsonl"),
                "--models-dir", str(root / "no_models"),
                "--out", str(root / "analysis.json"),
                "--markdown", str(root / "analysis.md"),
                "--bootstrap", "50",
                "--quiet",
            ])
            self.assertEqual(code, 0)
            payload = json.loads((root / "analysis.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["cleaning"]["included_rows"], 0)
            self.assertTrue(payload["notes"])
            self.assertTrue((root / "analysis.md").is_file())


if __name__ == "__main__":
    unittest.main()

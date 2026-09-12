import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from traj_web_extractor import extract_search_traj, extract_search_traj_file


class SearchTrajectoryExtractorTests(unittest.TestCase):
    def setUp(self):
        self.trajectory = {
            "user_query": "任贤齐和古天乐演的那个树大招风里边，龙头棍最后给谁了？",
            "goals": [
                {
                    "search_goal_id": "G2", "search_goal": "核实电影与角色",
                    "web_ids": ["3", "1"], "status": "SUPERSEDED",
                },
                {
                    "search_goal_id": "G1", "search_goal": "核实最终归属",
                    "web_ids": ["1", "2"],
                },
            ],
            "webs": [
                {
                    "web_id": "1", "title": "完整网页字段", "url": "https://example.invalid/1",
                    "web_content": "第一项\n第二行", "metadata": {"tags": ["电影"]},
                },
                {"web_id": "2", "web_content": "第二项"},
                {"web_id": "3", "web_content": "第三项"},
                {"web_id": "4", "web_content": "未被目标引用的网页"},
            ],
        }

    def test_exact_shape_and_goal_reference_order(self):
        result = extract_search_traj({"trajectory": self.trajectory})
        self.assertEqual(result, {
            "user_query": self.trajectory["user_query"],
            "search_goals": [
                {
                    "search_goal_id": "G2", "search_goal": "核实电影与角色",
                    "webs": [self.trajectory["webs"][2], self.trajectory["webs"][0]],
                    "web_content_str": "第三项\n第一项\n第二行",
                },
                {
                    "search_goal_id": "G1", "search_goal": "核实最终归属",
                    "webs": [self.trajectory["webs"][0], self.trajectory["webs"][1]],
                    "web_content_str": "第一项\n第二行\n第二项",
                },
            ],
        })

    def test_accepts_inner_trajectory_and_does_not_mutate_it(self):
        original = copy.deepcopy(self.trajectory)
        self.assertEqual(
            extract_search_traj(self.trajectory),
            extract_search_traj({"trajectory": self.trajectory}),
        )
        self.assertEqual(self.trajectory, original)

    def test_output_web_objects_are_independent_copies(self):
        result = extract_search_traj(self.trajectory)
        result["search_goals"][0]["webs"][1]["metadata"]["tags"].append("修改")
        self.assertEqual(self.trajectory["webs"][0]["metadata"]["tags"], ["电影"])
        self.assertEqual(result["search_goals"][1]["webs"][0]["metadata"]["tags"], ["电影"])

    def test_preserves_repeated_references_empty_strings_and_whitespace(self):
        self.trajectory["webs"][0]["web_content"] = " 重复 "
        self.trajectory["webs"][1]["web_content"] = ""
        self.trajectory["webs"][2]["web_content"] = "\n"
        self.trajectory["goals"][0]["web_ids"] = ["1", "2", "1", "3"]
        goal = extract_search_traj(self.trajectory)["search_goals"][0]
        contents = [" 重复 ", "", " 重复 ", "\n"]
        self.assertEqual([web["web_content"] for web in goal["webs"]], contents)
        self.assertEqual(goal["web_content_str"], "\n".join(contents))

    def test_rounds_and_handoff_are_not_used(self):
        expected = extract_search_traj(self.trajectory)
        self.trajectory["rounds"] = [{
            "action": {"search_goal": "不能替代目标"},
            "execution": {"webs": [{"web_id": "1", "web_content": "不能替代全局网页"}]},
        }]
        self.assertEqual(extract_search_traj({
            "trajectory": self.trajectory,
            "answer_agent_handoff": {"webs": [{"web_id": "1", "web_content": "不使用"}]},
        }), expected)

    def test_missing_null_and_empty_web_ids(self):
        for fields in ({}, {"web_ids": None}, {"web_ids": []}):
            with self.subTest(fields=fields):
                self.trajectory["goals"] = [{
                    "search_goal_id": "G1", "search_goal": "无网页目标", **fields,
                }]
                self.assertEqual(extract_search_traj(self.trajectory)["search_goals"], [{
                    "search_goal_id": "G1", "search_goal": "无网页目标",
                    "webs": [], "web_content_str": "",
                }])

    def test_empty_goals_and_empty_web_pool(self):
        self.trajectory["goals"] = []
        self.trajectory["webs"] = []
        self.assertEqual(extract_search_traj(self.trajectory)["search_goals"], [])

    def test_supports_id_fallback_and_string_integer_matching(self):
        self.trajectory["webs"] = [
            {"web_id": 0, "web_content": "零"},
            {"id": 1, "web_content": "一"},
            {"web_id": None, "id": "2", "web_content": "二"},
        ]
        self.trajectory["goals"] = [{
            "search_goal_id": "G1", "search_goal": "混合标识", "web_ids": ["0", "1", 2],
        }]
        goal = extract_search_traj(self.trajectory)["search_goals"][0]
        self.assertEqual(goal["webs"], self.trajectory["webs"])
        self.assertEqual(goal["web_content_str"], "零\n一\n二")

    def test_web_id_takes_precedence_over_id(self):
        self.trajectory["webs"][0]["id"] = "source-1"
        extract_search_traj(self.trajectory)
        self.trajectory["goals"][0]["web_ids"] = ["source-1"]
        with self.assertRaisesRegex(ValueError, "not found"):
            extract_search_traj(self.trajectory)

    def test_unmatched_reference_is_not_silently_dropped(self):
        self.trajectory["goals"][0]["web_ids"] = ["missing"]
        with self.assertRaisesRegex(ValueError, r"goals\[0\]\.web_ids\[0\].*missing.*not found"):
            extract_search_traj(self.trajectory)

    def test_duplicate_global_ids_are_rejected(self):
        self.trajectory["webs"].append({"id": 1, "web_content": "冲突的另一网页"})
        with self.assertRaisesRegex(ValueError, "duplicate web ID '1'"):
            extract_search_traj(self.trajectory)

    def test_invalid_selected_web_content_has_precise_path(self):
        for web in ({"web_id": "3"}, {"web_id": "3", "web_content": None},
                    {"web_id": "3", "web_content": 1}):
            with self.subTest(web=web):
                self.trajectory["webs"][2] = web
                with self.assertRaisesRegex(ValueError, r"trajectory\.webs\[2\]\.web_content"):
                    extract_search_traj(self.trajectory)

    def test_invalid_global_web_objects_and_ids(self):
        for web in ("not an object", {}, {"id": True}, {"web_id": []},
                    {"web_id": 1.5}, {"web_id": "", "id": "fallback"}):
            with self.subTest(web=web):
                self.trajectory["webs"][0] = web
                with self.assertRaisesRegex(ValueError, r"trajectory\.webs\[0\]"):
                    extract_search_traj(self.trajectory)

    def test_invalid_required_fields(self):
        for data in (None, {"trajectory": None}, {}, {"user_query": 1},
                     {"user_query": "问题", "goals": None, "webs": []},
                     {"user_query": "问题", "goals": [], "webs": None},
                     {"user_query": "问题", "goals": [{}], "webs": []}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                extract_search_traj(data)

    def test_invalid_goal_field_types(self):
        for goal in ([], {"search_goal": "缺失目标 ID"},
                     {"search_goal_id": "G1", "search_goal": None},
                     {"search_goal_id": "G1", "search_goal": "目标", "web_ids": {}},
                     {"search_goal_id": "G1", "search_goal": "目标", "web_ids": [None]}):
            with self.subTest(goal=goal):
                self.trajectory["goals"] = [goal]
                with self.assertRaisesRegex(ValueError, r"trajectory\.goals\[0\]"):
                    extract_search_traj(self.trajectory)

    def test_file_round_trip_and_read_only_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "原始轨迹.json"
            source.write_text(json.dumps({"trajectory": self.trajectory}, ensure_ascii=False), encoding="utf-8-sig")
            original_bytes = source.read_bytes()
            expected = extract_search_traj(self.trajectory)
            self.assertEqual(extract_search_traj_file(source), expected)
            self.assertEqual(list(Path(directory).iterdir()), [source])
            destination = Path(directory) / "output" / "搜索轨迹.json"
            self.assertEqual(extract_search_traj_file(source, destination), expected)
            serialized = destination.read_text(encoding="utf-8")
            self.assertIn(self.trajectory["user_query"], serialized)
            self.assertEqual(json.loads(serialized), expected)
            self.assertEqual(source.read_bytes(), original_bytes)
            with self.assertRaises(FileExistsError):
                extract_search_traj_file(source, destination)
            self.assertEqual(destination.read_text(encoding="utf-8"), serialized)

    def test_cannot_overwrite_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "trajectory.json"
            source.write_text(json.dumps(self.trajectory), encoding="utf-8")
            original_bytes = source.read_bytes()
            with self.assertRaisesRegex(ValueError, "input trajectory"):
                extract_search_traj_file(source, source)
            self.assertEqual(source.read_bytes(), original_bytes)

    def test_failed_extraction_does_not_create_output(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "trajectory.json"
            destination = Path(directory) / "output" / "result.json"
            self.trajectory["goals"][0]["web_ids"] = ["missing"]
            source.write_text(json.dumps(self.trajectory), encoding="utf-8")
            with self.assertRaises(ValueError):
                extract_search_traj_file(source, destination)
            self.assertFalse(destination.parent.exists())

    def test_cli_reports_goals_and_writes_new_shape(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "trajectory.json"
            destination = Path(directory) / "result.json"
            source.write_text(json.dumps({"trajectory": self.trajectory}), encoding="utf-8")
            process = subprocess.run(
                [sys.executable, "-m", "traj_web_extractor", str(source), "-o", str(destination)],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True, text=True, check=True,
            )
            summary = json.loads(process.stdout)
            self.assertEqual(summary["goal_count"], 2)
            self.assertEqual(summary["web_counts"], [2, 2])
            self.assertEqual(json.loads(destination.read_text(encoding="utf-8")),
                             extract_search_traj(self.trajectory))


if __name__ == "__main__":
    unittest.main()

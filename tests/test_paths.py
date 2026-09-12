import unittest
from pathlib import Path

from deep_search.paths import (
    build_trajectory_filename,
    build_trajectory_path,
    millisecond_timestamp,
)


class TrajectoryPathTests(unittest.TestCase):
    def test_common_filename_format(self):
        filename = build_trajectory_filename(
            "测试问题？",
            "2026-08-09",
            timestamp_ms=1775861223456,
        )
        self.assertEqual(
            filename,
            "traj_测试问题？_2026-08-09_1775861223456.json",
        )

    def test_path_keeps_query_in_one_component(self):
        path = build_trajectory_path(
            "trajectories",
            "A/B 对比",
            "2026/08/09",
            timestamp_ms=123,
        )
        self.assertEqual(
            path,
            Path("trajectories/traj_A_B 对比_2026_08_09_123.json"),
        )

    def test_rejects_invalid_timestamp(self):
        with self.assertRaises(ValueError):
            build_trajectory_filename("query", "2026-08-09", timestamp_ms=-1)

    def test_generated_timestamps_are_unique_within_process(self):
        values = [millisecond_timestamp() for _ in range(20)]
        self.assertEqual(len(set(values)), len(values))
        self.assertEqual(values, sorted(values))


if __name__ == "__main__":
    unittest.main()

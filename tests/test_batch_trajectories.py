import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import run_batch_trajectories as batch


class BatchTrajectoryTests(unittest.TestCase):
    def test_in_file_configuration_contains_ten_queries(self):
        self.assertEqual(len(batch.QUERIES), 10)
        self.assertGreater(batch.MAX_WORKERS, 0)

    def test_batch_preserves_input_order_and_uses_thread_pool(self):
        thread_names = set()
        lock = threading.Lock()

        def fake_synthesize(index, query, **kwargs):
            with lock:
                thread_names.add(threading.current_thread().name)
            time.sleep(0.01)
            return {"index": index, "query": query, "run_status": "COMPLETED"}

        queries = [f"query-{index}" for index in range(10)]
        with patch.object(batch, "_synthesize_one", side_effect=fake_synthesize):
            results = batch.synthesize_queries(
                queries,
                max_workers=3,
                scheme="scheme_b",
                current_date="2026-08-09",
                output_directory=Path("unused"),
            )

        self.assertEqual([item["query"] for item in results], queries)
        self.assertGreater(len(thread_names), 1)

    def test_batch_validates_worker_count(self):
        with self.assertRaises(ValueError):
            batch.synthesize_queries(
                ["query"],
                max_workers=0,
                scheme="scheme_a",
                current_date="2026-08-09",
                output_directory=Path("unused"),
            )


if __name__ == "__main__":
    unittest.main()

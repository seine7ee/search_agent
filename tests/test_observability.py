import json
import logging
import tempfile
import unittest
from pathlib import Path

from deep_search.observability import configure_logging


class ObservabilityTests(unittest.TestCase):
    def test_json_log_contains_monitoring_context(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.log"
            logger = configure_logging(path, level="DEBUG")
            logger.getChild("test").info(
                "test_event",
                extra={"event": "test_event", "run_id": "run-1", "turn_id": 2},
            )
            for handler in logger.handlers:
                handler.flush()

            records = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(records[-1]["event"], "test_event")
            self.assertEqual(records[-1]["run_id"], "run-1")
            self.assertEqual(records[-1]["turn_id"], 2)
            for handler in list(logger.handlers):
                handler.close()
                logger.removeHandler(handler)
            logger.addHandler(logging.NullHandler())


if __name__ == "__main__":
    unittest.main()

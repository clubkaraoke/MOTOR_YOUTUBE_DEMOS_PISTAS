import json
import tempfile
import unittest
from pathlib import Path

from engine.lab_queue import LabQueue


class LabQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.queue = LabQueue(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_persistent_sequential_queue(self) -> None:
        report = self.queue.add_jobs(
            [
                {
                    ".tag": "file",
                    "name": "A.cdg",
                    "path_display": "/Pack/A.cdg",
                    "id": "id:a",
                    "size": 123,
                },
                {
                    ".tag": "file",
                    "name": "B.cdg",
                    "path_display": "/Pack/B.cdg",
                    "id": "id:b",
                    "size": 456,
                },
            ],
            pack="Pack -7",
        )

        self.assertEqual(report["inserted_or_updated"], 2)
        self.assertEqual(self.queue.counts()["PENDING"], 2)

        first = self.queue.claim_next()
        self.assertIsNotNone(first)
        self.assertEqual(first["name"], "A.cdg")
        self.assertEqual(self.queue.counts()["PROCESSING"], 1)

        result = {
            "filename": "A.cdg",
            "average_confidence": 93.0,
            "quality": "BUENA",
            "pages_detected": 12,
            "lines_detected": 30,
            "corrections_count": 2,
        }
        result_path = self.queue.save_result(first, result)
        self.queue.finish(
            first["id"],
            result_path,
            result,
            91.5,
        )

        self.assertTrue(result_path.exists())
        self.assertEqual(
            json.loads(result_path.read_text())["filename"],
            "A.cdg",
        )
        self.assertEqual(self.queue.counts()["DONE"], 1)
        self.assertEqual(self.queue.counts()["PENDING"], 1)

    def test_retry_errors(self) -> None:
        self.queue.add_jobs(
            [
                {
                    "name": "bad.cdg",
                    "path_display": "/Pack/bad.cdg",
                    "size": 100,
                }
            ],
            pack="Pack -8",
        )

        job = self.queue.claim_next()
        assert job is not None
        self.queue.fail(job["id"], "boom")
        self.assertEqual(self.queue.counts()["ERROR"], 1)

        self.assertEqual(self.queue.retry_errors(), 1)
        self.assertEqual(self.queue.counts()["PENDING"], 1)

    def test_worker_setting_persists(self) -> None:
        self.queue.set_worker_enabled(True)
        second = LabQueue(Path(self.tmp.name))
        self.assertTrue(second.worker_enabled())

    def test_limited_run_auto_pauses(self) -> None:
        self.queue.set_run_limit(2)
        self.queue.set_worker_enabled(True)

        self.assertEqual(self.queue.run_remaining(), 2)
        self.assertEqual(self.queue.consume_run_slot(), 1)
        self.assertTrue(self.queue.worker_enabled())

        self.assertEqual(self.queue.consume_run_slot(), 0)
        self.assertFalse(self.queue.worker_enabled())

        second = LabQueue(Path(self.tmp.name))
        self.assertEqual(second.run_remaining(), 0)
        self.assertFalse(second.worker_enabled())


if __name__ == "__main__":
    unittest.main()

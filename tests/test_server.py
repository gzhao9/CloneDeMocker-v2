import time
import unittest
from unittest.mock import patch

from studio import server


class FakeRefactoring:
    def __init__(self):
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        kwargs["progress_callback"]("CANDIDATE_TESTING", 70, "Running full regression")
        return {
            "proposalId": kwargs["selected_mci_ids"][0].replace(":", ""),
            "stage": "COMPLETED",
            "harness": {"equivalent": True, "baseline": {
                "compileStatus": "PASSED", "testStatus": "PASSED", "pitStatus": "NOT_RUN",
                "commands": [], "diagnostics": [], "testResults": {"demo.Test#x": "PASSED"},
                "mutationTotal": 0, "mutationScore": None, "mutationCounts": {}, "mutants": {},
            }},
        }


class ServerJobTest(unittest.TestCase):
    def test_refactoring_job_processes_each_mci_and_exposes_progress(self):
        fake = FakeRefactoring()
        payload = {"runId": "run", "selectedMciIds": ["A::1", "B::1"], "maxRetries": 0}
        with patch.object(server, "REFACTORING", fake):
            job_id = server.start_refactoring(payload)["jobId"]
            deadline = time.time() + 2
            while time.time() < deadline:
                job = server.refactoring_status(job_id)
                if job["state"] == "COMPLETED":
                    break
                time.sleep(0.01)

        self.assertEqual("COMPLETED", job["state"])
        self.assertEqual(2, job["completed"])
        self.assertEqual(["A::1", "B::1"], [call["selected_mci_ids"][0] for call in fake.calls])
        self.assertTrue(all(item["percent"] == 100 for item in job["items"]))
        self.assertTrue(all(item["state"] == "COMPLETED" for item in job["items"]))


if __name__ == "__main__":
    unittest.main()

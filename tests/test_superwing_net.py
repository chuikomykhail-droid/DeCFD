"""SuperWing tasks on the network: task definition, tolerance audits, a job on the mock ledger."""
import io
import json
import os
import random
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "orchestrator_py"))

import superwing_tasks as sw  # noqa: E402
from network.mock_ledger import MockSolanaProgram  # noqa: E402
from superwing_job import run_job  # noqa: E402

CASE = os.path.join(sw.CASES, "shape0000")
OPTIONS = {"useANKSolver": False, "nCycles": 1500}
CLUSTER = {1: (0.06840604719936484, 0.01038646820948247), 3: (0.21246206808210943, 0.011428850900591783)}
OUR_AREA_SCALE = 1.7633693114510065 / 1.7039010077986356   # our coefficients are larger by the area ratio


def fake_runner(input_path, ic, options, work_name, n_proc):
    """What the container would return: the cluster's numbers on our area, plus a little noise that
    differs per machine (MPI partitions), well inside the audit tolerance."""
    noise = random.Random(work_name).uniform(-2e-6, 2e-6)
    cl, cd = CLUSTER[ic]
    return {"status": "ok", "state": "converged", "CL": cl * OUR_AREA_SCALE + noise, "CD": cd * OUR_AREA_SCALE + noise,
            "CMz": 0.1, "n_step": float(options["nCycles"]), "seconds": 1.0, "output_sha256": "ab" * 32}


class TaskDefinitionTest(unittest.TestCase):
    def test_params_carry_the_condition(self):
        params, params_hash = sw.task_spec(os.path.join(CASE, "input.json"), 3, OPTIONS)
        self.assertEqual(params[:2], [0.0, 3.0])
        self.assertAlmostEqual(params[2], 0.7648770793)
        self.assertEqual(params[5:7], [1500.0, 1.0])
        self.assertEqual(len(params), 8)
        self.assertEqual(len(params_hash), 64)

    def test_node_rebuilds_the_same_hash_from_params(self):
        params, params_hash = sw.task_spec(os.path.join(CASE, "input.json"), 1, OPTIONS)
        again = sw.task_spec(sw.case_input(int(params[0])), int(params[1]), sw.options_from_params(params))[1]
        self.assertEqual(again, params_hash)

    def test_a_changed_case_changes_the_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = os.path.join(tmp, "shape0000")
            os.makedirs(case)
            with open(os.path.join(CASE, "input.json")) as f:
                data = json.load(f)
            data["rootadj"] += 1e-6
            with open(os.path.join(case, "input.json"), "w") as f:
                json.dump(data, f)
            self.assertNotEqual(sw.task_spec(os.path.join(case, "input.json"), 1, OPTIONS)[1],
                                sw.task_spec(os.path.join(CASE, "input.json"), 1, OPTIONS)[1])

    def test_options_outside_the_params_are_refused(self):
        with self.assertRaises(ValueError):
            sw.task_spec(os.path.join(CASE, "input.json"), 1, {"nCycles": 1500, "ANKSubspaceSize": 10})

    def test_pipeline_hash_ignores_line_endings(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = os.path.join(tmp, "superwing")
            shutil.copytree(sw.PILOT, copy, ignore=shutil.ignore_patterns("cases", "runs", "__pycache__"))
            path = os.path.join(copy, "run_case.py")
            with open(path, "rb") as f:
                text = f.read().replace(b"\r\n", b"\n")
            with open(path, "wb") as f:
                f.write(text.replace(b"\n", b"\r\n"))
            self.assertEqual(sw.pipeline_hash(copy), sw.pipeline_hash())
            with open(path, "ab") as f:
                f.write(b"# changed\n")
            self.assertNotEqual(sw.pipeline_hash(copy), sw.pipeline_hash())

    def test_tolerance(self):
        a = {"status": "ok", "CL": 0.2, "CD": 0.0110}
        self.assertTrue(sw.within_tolerance(a, dict(a, CL=0.2009, CD=0.01109)))
        self.assertFalse(sw.within_tolerance(a, dict(a, CL=0.2011)))
        self.assertFalse(sw.within_tolerance(a, dict(a, CD=0.01102 + 1e-4)))
        self.assertFalse(sw.within_tolerance(a, {"status": "error"}))

    def test_result_hash_commits_to_the_output_file(self):
        res = fake_runner(None, 1, OPTIONS, "a", 1)
        self.assertNotEqual(sw.result_hash("g0-t0000", res),
                            sw.result_hash("g0-t0000", dict(res, output_sha256="cd" * 32)))


class MockJobTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)   # cleanups run last-in first-out: the ledger log closes first

    def run_mock_job(self, **kw):
        ledger = MockSolanaProgram(log_dir=self.tmp.name)
        self.addCleanup(ledger.close)
        with redirect_stdout(io.StringIO()):
            report = run_job(ledger, CASE, [1, 3], OPTIONS, runner=fake_runner, log_dir=self.tmp.name, **kw)
        return ledger, report

    def test_honest_miner_passes_the_tolerance_audit(self):
        ledger, report = self.run_mock_job(local=1)
        audited = [t for t in report["tasks"] if t["audited"]]   # one of the miner's tasks, picked by the seed
        self.assertEqual(len(audited), 1)
        self.assertTrue(audited[0]["audit_ok"])
        self.assertEqual({t["status"] for t in report["tasks"]}, {"finalized"})
        for t in report["tasks"]:   # on the cluster's area the numbers are the cluster's
            self.assertAlmostEqual(t["dCL"], 0.0, delta=1e-5)
            self.assertAlmostEqual(t["dCD"], 0.0, delta=1e-5)
        self.assertEqual(report["paid"], 200)
        self.assertEqual(ledger.total_supply_held(), ledger.supply)

    def test_lazy_miner_is_caught_and_slashed(self):
        ledger, report = self.run_mock_job(local=2, lazy=1)
        honest, lazy = report["tasks"]
        self.assertEqual(lazy["miner"], "sw-miner-2")
        self.assertFalse(lazy["audit_ok"])
        self.assertEqual(lazy["status"], "rejected")
        self.assertAlmostEqual(lazy["dCL"], 0.0, delta=1e-5)    # the verifier's numbers replace the fake
        self.assertEqual(honest["status"], "finalized")
        self.assertEqual(report["miners"]["sw-miner-2"]["slashed"], 200)
        self.assertEqual(report["miners"]["sw-miner-2"]["earned"], 0)
        self.assertEqual(report["paid"], 100)
        self.assertEqual(report["refund"], 100)
        self.assertEqual(ledger.total_supply_held(), ledger.supply)


if __name__ == "__main__":
    unittest.main()

"""The C++ worker must be deterministic: verification compares result hashes bit for bit."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "orchestrator_py"))

from worker_runner import WORKER_PATH, canonical_result, run_worker  # noqa: E402

SHAPE = ["60", "2", "2.5", "4", "6", "6", "15.5", "5.6"]
FAST = ["--steps", "300", "--avg", "100"]


@unittest.skipUnless(os.path.exists(WORKER_PATH), "worker binary not built")
class WorkerTest(unittest.TestCase):
    def test_same_input_same_bits(self):
        a = run_worker(SHAPE + FAST)
        b = run_worker(SHAPE + FAST)
        self.assertEqual(a["status"], "ok")
        self.assertEqual(canonical_result(a), canonical_result(b))

    def test_thread_count_does_not_change_the_result(self):
        one = run_worker(SHAPE + FAST, threads=1)
        many = run_worker(SHAPE + FAST, threads=4)
        self.assertEqual(canonical_result(one), canonical_result(many))

    def test_different_shape_different_result(self):
        other = SHAPE[:-1] + ["2.0"]
        self.assertNotEqual(canonical_result(run_worker(SHAPE + FAST)),
                            canonical_result(run_worker(other + FAST)))

    def test_lift_sign_follows_angle_of_attack(self):
        up = run_worker(["60", "2", "8", "6", "3", "0.5", "8", "0"] + FAST)
        down = run_worker(["60", "2", "8", "6", "3", "0.5", "-8", "0"] + FAST)
        self.assertGreater(up["cl"], 0)
        self.assertAlmostEqual(up["cl"], -down["cl"], places=5)

    def test_error_codes(self):
        self.assertEqual(run_worker(["60", "1"])["code"], 2)                          # too few parameters
        self.assertEqual(run_worker(["60", "1", "200", "1", "1", "1"] + FAST)["code"], 3)  # touches the walls


if __name__ == "__main__":
    unittest.main()

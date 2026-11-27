"""Pilot tooling: parsing SuperWing rows into cases and comparing results with the cluster."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pilot", "superwing"))

from build_case import ROW_LEN, SHAPE_LEN, build_case, planform_area, read_configs, wing_params  # noqa: E402
from compare import compare  # noqa: E402

M3 = [0.75 + 0.002 * i for i in range(8)]
AA = [1.0 + i for i in range(8)]
ROW = ([35.0, 5.0, 4.0, 10.0, 0.25, 0.4, 0.3]   # SA DA DA_kink AR TR kink rootadj
       + [0.7, 0.9, 1.0] + [0.6, 0.6, 0.6] + [-3.0, -3.5, -2.5, -1.5]
       + [0.1 * k for k in range(1, 11)] + [-0.1 * k for k in range(1, 11)]
       + [0.12] + M3 + AA)


def run_record(shape, rootadj_in_shape=0.55, aa_shift=0.0, param_shift=0.0):
    vec = ROW[:SHAPE_LEN]
    vec = vec[:6] + [rootadj_in_shape] + vec[7:]
    vec[3] += param_shift   # AR
    rec = {"running_id": shape, "shape": vec, "state": "Preprocess Success",
           "mesh": {"min_Quality": -0.26, "actual_ds": 1.3e-6}}
    for ic in range(8):
        rec[f"{ic}_state"] = "Success"
        rec[f"{ic}_running_info"] = {
            "results": {"wing_cl": 0.1 * ic, "wing_cd": 0.01 + 0.001 * ic, "wing_cmz": 0.0,
                        "err_cl": 1e-8, "err_cd": 1e-9, "n_step": 1800.0},
            "working_conditions": {"M3": M3[ic], "AA": AA[ic] + aa_shift, "re": 2e7, "temp": 300}}
    return rec


class BuildCaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.configs = os.path.join(self.tmp.name, "configs.dat")
        with open(self.configs, "w") as f:
            f.write("VARIABLES = SA DA ...\n")
            f.write("      0 " + " ".join(f"{x:.10f}" for x in ROW) + "\n")
        self.running = os.path.join(self.tmp.name, "running.jsonl")
        with open(self.running, "w") as f:
            f.write(json.dumps(run_record(0)) + "\n")
            f.write('{"running_id": 1, "shape": [1.0, ')   # truncated download

    def tearDown(self):
        self.tmp.cleanup()

    def test_row_layout(self):
        self.assertEqual(len(ROW), ROW_LEN)
        p = wing_params(read_configs(self.configs)[0])
        self.assertEqual(p["SA"], 35.0)
        self.assertEqual(p["rootadj"], 0.3)
        self.assertEqual(p["twists"], [-3.0, -3.5, -2.5, -1.5])
        self.assertAlmostEqual(p["cst_u"][0], 0.1)
        self.assertAlmostEqual(p["cst_l"][-1], -1.0)
        self.assertEqual(p["tcroot"], 0.12)
        self.assertEqual(len(p["M3"]), 8)
        self.assertAlmostEqual(p["AA"][7], 8.0)

    def test_wrong_row_length(self):
        with self.assertRaises(ValueError):
            wing_params(ROW[:-1])

    def test_rootadj_comes_from_configs(self):
        params, reference = build_case(self.configs, 0, os.path.join(self.tmp.name, "case"), self.running)
        self.assertEqual(params["rootadj"], 0.3)   # not the 0.55 in the run log's shape vector
        self.assertEqual(len(reference["conditions"]), 8)
        with open(os.path.join(self.tmp.name, "case", "input.json")) as f:
            self.assertEqual(json.load(f)["rootadj"], 0.3)

    def test_fitted_rootadj_overrides_configs(self):
        params, _ = build_case(self.configs, 0, os.path.join(self.tmp.name, "case"), rootadj=0.29)
        self.assertEqual(params["rootadj"], 0.29)
        self.assertEqual(params["rootadj_configs"], 0.3)

    def test_mismatched_conditions_are_rejected(self):
        with open(self.running, "w") as f:
            f.write(json.dumps(run_record(0, aa_shift=0.5)) + "\n")
        with self.assertRaises(ValueError):
            build_case(self.configs, 0, os.path.join(self.tmp.name, "case"), self.running)

    def test_mismatched_wing_parameters_are_rejected(self):
        with open(self.running, "w") as f:
            f.write(json.dumps(run_record(0, param_shift=0.01)) + "\n")
        with self.assertRaises(ValueError):
            build_case(self.configs, 0, os.path.join(self.tmp.name, "case"), self.running)

    def test_reference_area_uses_the_run_logs_rootadj(self):
        params, reference = build_case(self.configs, 0, os.path.join(self.tmp.name, "case"), self.running,
                                       rootadj=0.29)
        self.assertEqual(reference["area_ref_rootadj"], 0.55)
        self.assertAlmostEqual(reference["area_ref"], planform_area(params, 0.55), places=12)
        self.assertGreater(reference["area_ref"], planform_area(params, 0.29))

    def test_missing_shape(self):
        with self.assertRaises(KeyError):
            build_case(self.configs, 0, os.path.join(self.tmp.name, "case"),
                       self._running_without_shape_0())

    def _running_without_shape_0(self):
        path = os.path.join(self.tmp.name, "other.jsonl")
        with open(path, "w") as f:
            f.write(json.dumps(run_record(5)) + "\n")
        return path


class PlanformAreaTest(unittest.TestCase):
    SHAPE0 = {"SA": 38.1752126008, "AR": 10.0497157391, "TR": 0.2402828032, "kink": 0.4052043164}

    def test_same_as_gen_mesh(self):
        # surface_area written by gen-mesh.superwing.py for shape 0 with the fitted rootadj
        self.assertAlmostEqual(planform_area(self.SHAPE0, 0.290406998), 1.7039010077986356, places=12)

    def test_cluster_area_of_shape_0(self):
        # with the sampled rootadj: our CL / this ratio is the cluster's CL to 1e-5
        ratio = planform_area(self.SHAPE0, 0.5328275736) / planform_area(self.SHAPE0, 0.290406998)
        self.assertAlmostEqual(ratio, 1.0349013, places=6)


class CompareTest(unittest.TestCase):
    REFERENCE = {"conditions": [{"ic": 0, "state": "Success", "CL": 0.4685, "CD": 0.017856, "n_step": 1827},
                                {"ic": 1, "state": "Success", "CL": 0.0684, "CD": 0.010386, "n_step": 1679}]}

    def result(self, cl, cd, state="converged"):
        return {"conditions": [{"ic": 0, "M3": 0.752, "AA": 9.37, "state": state, "CL": cl, "CD": cd,
                                "n_step": 1800, "seconds": 3600}]}

    def test_match_within_tolerance(self):
        rows = compare(self.result(0.4689, 0.017866), self.REFERENCE, 1e-3, 1e-4)
        self.assertTrue(rows[0]["ok"])
        self.assertAlmostEqual(rows[0]["dCD"], 1e-5)

    def test_drag_off_by_two_counts(self):
        rows = compare(self.result(0.4685, 0.018056), self.REFERENCE, 1e-3, 1e-4)
        self.assertFalse(rows[0]["ok"])

    def test_not_converged_is_a_mismatch(self):
        rows = compare(self.result(0.4685, 0.017856, state="not converged"), self.REFERENCE, 1e-3, 1e-4)
        self.assertFalse(rows[0]["ok"])

    def test_coefficients_put_on_the_cluster_area(self):
        reference = dict(self.REFERENCE, area_ref=2.0)
        rows = compare(self.result(0.4932, 0.018796), reference, 1e-3, 1e-4, area=1.9)
        self.assertAlmostEqual(rows[0]["CL"], 0.46854)
        self.assertAlmostEqual(rows[0]["CL_raw"], 0.4932)
        self.assertTrue(rows[0]["ok"])
        self.assertFalse(compare(self.result(0.4932, 0.018796), self.REFERENCE, 1e-3, 1e-4)[0]["ok"])

    def test_failed_run_has_no_coefficients(self):
        result = {"conditions": [{"ic": 1, "M3": 0.756, "AA": 5.47, "state": "failed", "seconds": 10}]}
        rows = compare(result, self.REFERENCE, 1e-3, 1e-4)
        self.assertFalse(rows[0]["ok"])
        self.assertNotIn("CL", rows[0])


if __name__ == "__main__":
    unittest.main()

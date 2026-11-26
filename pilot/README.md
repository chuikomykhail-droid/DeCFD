# Pilot: SuperWing cases on the DeCFD network

**Goal.** Take a few wings from the public [SuperWing](https://huggingface.co/datasets/yunplus/SuperWing) dataset (TU Munich, Thuerey group) and compute them on DeCFD machines with the group's own pipeline. Then show that the results match the published cluster results within convergence tolerance. The comparison covers:

- lift (CL);
- drag (CD);
- surface pressure;
- time and cost per case.

That report is what goes back to the authors.

**Why reproduce first.** Published results are ground truth. We can check the network against them without asking the authors for anything. New cases come after the network has reproduced old ones.

## The pipeline being reproduced

The source is [tum-pbs/AeroTransformer](https://github.com/tum-pbs/AeroTransformer), `simulation/`, licensed Apache-2.0. It runs in the [MDO Lab](https://mdolab-mach-aero.readthedocs-hosted.com/en/latest/installInstructions/dockerInstructions.html) Docker image (Linux, MPI):

1. `input.json`: wing parameters plus a list of flow conditions (Mach `M3`, angle of attack `AA`).
2. `gen-mesh.superwing.py`:
   - builds a CST surface mesh, which needs `cst-modeling3d` from PyPI;
   - then a pyHyp volume mesh (`wing_vol.cgns`), about 44k surface cells × 80 layers ≈ 3.5M cells.
3. `run-adflow.py` solves each condition with ADflow:
   - RANS at Re = 20M;
   - ANK solver, up to 3000 cycles;
   - typically about 1,800 to converge.
4. `output.json` per condition holds CL, CD, CM, the convergence errors and the step count. A case counts as converged if the CL fluctuation over the last steps is below 0.002.

## What is settled

**Where the wing parameters come from.** The dataset carries the root-kink adjustment `rootadj` in two places with different values. In shape 0, `configs.dat` has 0.296 and `running_info.shape` has 0.533. A code comment says the early `rootadj` code "was wrong".

Checked against the published surface coordinates of shape 0:

| Source | Predicted chord at the innermost station | Published |
|---|---|---|
| `configs.dat` (0.296) | 1.072 | **1.069** (0.3% off) |
| `running_info.shape` (0.533) | ≈ 1.19 | 1.069 (12% off) |

The published surface is stored at cell centres: its innermost station is half a cell outboard of the root. The predicted chords are taken at that same station.

**Use `configs.dat` values with the final `gen-mesh.superwing.py`.** `running_info.shape` holds the sampled design value, not the one used for the geometry. This rests on one shape. Repeat the check on every pilot shape before computing it.

## Files

| Path | What |
|---|---|
| `superwing/vendor/` | The TU Munich scripts, byte for byte, with hashes (`NOTICE.md`) |
| `superwing/build_case.py` | SuperWing row → `input.json` plus `reference.json` (the cluster's results) |
| `superwing/compare.py` | Our results vs. the cluster: CL, drag in counts, steps, time, verdict |
| `superwing/cases/` | Prepared cases (data CC BY-SA 4.0, see the README there) |
| `../tests/test_pilot_superwing.py` | Parsing and comparison tests |

```
python pilot/superwing/build_case.py --configs configs.dat --running running.jsonl --shape 0 --out pilot/superwing/cases/shape0000
python -m unittest tests.test_pilot_superwing
```

## Steps

1. **One case by hand.**
   - Install WSL2 and Docker, pull the MDO Lab image, add `cst-modeling3d`.
   - Run shape 0, condition 0: M = 0.752, AoA = 9.37°. The published result is CL = 0.4685, CD = 0.01786, after 1,827 steps.
   - Measure wall time and peak RAM.

   This decides whether the office laptops can take these cases at all.
2. **`pilot/` tooling.**
   - Case builder: `configs.dat` row → `input.json`.
   - Container runner: `docker run` with the image pinned by digest.
   - Comparison: our `output.json` and surface against the published ones.
3. **Container tasks in the network.**
   - A job pins the image digest and the hash of the pipeline scripts, the way it pins `worker.exe` today.
   - Audits compare with a tolerance instead of bit for bit, because MPI partitioning changes the last bits.
   - Later, cheaper audits by restart: load the submitted solution, run about 5% of the cycles, and check that residuals stay down and CL/CD stay put. ADflow supports restart files.
4. **Pilot run.** A few shapes, about 7 conditions each, on the four laptops. Then a report: agreement with the cluster, time, cost per case, and caught or failed cases.

## Open questions

- RAM and time of one case on a 4-core laptop. Step 1 answers this.
- Result size per case. Surface output is small, but the volume solution needed for restart audits is hundreds of MB.
- Which MDO Lab image tag the authors used. Pin one and note it in the report.

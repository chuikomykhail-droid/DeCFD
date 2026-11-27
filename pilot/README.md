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

`running_info.shape` holds the sampled design value, not the one used for the geometry. `configs.dat` is close, but it is not exact either.

**Full surface check (shape 0).** We built the surface mesh with `gen-mesh.superwing.py` from `configs.dat` and compared all 44,096 cell centres with the published surface:

| Part of the wing | Largest gap to the published surface (chord = 1) |
|---|---|
| Kink to tip (29,760 cells) | 1.5e-7: identical |
| Root to kink, `rootadj` from `configs.dat` (0.295870) | 2.8e-3: root chord 0.26% too long; leading edge identical |
| Root to kink, `rootadj` fitted by least squares (0.290407) | **5.4e-8: identical** |
| Tip cap (span 3.116–3.126) | 1.1e-3 with either value; not explained yet |

So `configs.dat` only approximates the root geometry the cluster used. The fitted value reproduces it exactly. The ratio of the fitted value to the sampled one is 0.545 for this shape; we found no formula for it yet. Each pilot shape therefore gets its `rootadj` fitted to the published surface (`build_case.py --rootadj`). The published geometry of any shape can be read from the dataset's `origingeom.npy` with an HTTP range request (about 0.8 MB per shape).

The cap at the very tip is the one part that still differs. It is about 0.3% of the span, so its effect on the forces should be small. The comparison will show it.

**Reference area.** `run-adflow.py` divides forces by `surface_area`, which `gen-mesh.superwing.py` computes from the planform, `rootadj` included. The cluster's coefficients fit an area computed with the *sampled* `rootadj` (0.533, from `running_info.shape`), while the geometry corresponds to the fitted 0.290. For shape 0 that area is 3.49% larger than the one our run uses.

Evidence, from condition 0 after 1,538 DADI cycles:

| | CL | CMz | CD |
|---|---|---|---|
| Ours / cluster's | 1.0349 | 1.0350 | 1.0380 (still converging) |
| Area with sampled `rootadj` / area with fitted `rootadj` | 1.0349 | 1.0349 | 1.0349 |

All three coefficients carry the same factor, and that factor is exactly the area ratio. So the forces agree, and only the normalisation differs. `build_case.py` records the cluster's area in `reference.json` (`area_ref`), and `compare.py` puts our coefficients on it before comparing.

The ratio depends on each shape's two `rootadj` values. So the published CL and CD carry a per-shape scale offset of a few percent relative to the geometry's own reference area. That is worth telling the authors once a second shape confirms it.

## Files

| Path | What |
|---|---|
| `superwing/vendor/` | The TU Munich scripts, byte for byte, with hashes (`NOTICE.md`) |
| `superwing/build_case.py` | SuperWing row → `input.json` plus `reference.json` (the cluster's results) |
| `superwing/compare.py` | Our results vs. the cluster on the cluster's reference area: CL, drag in counts, steps, time, verdict |
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

## Measured so far (shape 0)

Laptop: Ryzen 5 6600H (6 cores / 12 threads), 16 GB RAM, WSL2 limited to 11 GB. Image: MDO Lab `u22-gcc-ompi-stable` pinned by digest.

| Stage | Result |
|---|---|
| Mesh: CST surface plus pyHyp volume, 3.53M cells, 6 MPI processes | 15.5 min. First-cell height equals the cluster's (1.32e-6). Minimum cell quality 0.058, the cluster's −0.260: probably a different pyHyp version, since the surface is identical. |
| ADflow, the cluster's settings (ANK, ILU(2), 40 Krylov vectors) | **Out of memory** when the ANK preconditioner is built on the fine grid |
| ANK, lean (ILU(0), 10 Krylov vectors, no ASM overlap, thin-layer viscous preconditioner) | Still out of memory, at 10.3 GiB |
| Multigrid DADI only (no ANK) | 4.55 GiB peak. 3.1 s per fine-grid 3w cycle on 12 MPI processes, 7.5 s on 6. |

**What this means for miners.** With the authors' solver, a 3.5M-cell SuperWing case needs well over 16 GB of RAM. A 16 GB machine can run it only with the DADI smoother, which needs far more cycles. For the network this is a hard requirement per task type: a task has to state its minimum RAM, and a miner gets only the tasks it can hold.

**DADI convergence on condition 0** (M = 0.752, AoA = 9.37°; cluster CL 0.46849, CD 0.017856). Our coefficients are shown on the cluster's reference area, 12 MPI processes, about 3.1 s per cycle:

| Fine-grid cycles | Density residual | CL | ΔCL | CD | ΔCD, counts |
|---|---|---|---|---|---|
| 500 | 13.0 | 0.47139 | +0.0029 | 0.017951 | +1.0 |
| 1,000 | 4.1 | 0.46923 | +0.0007 | 0.017932 | +0.8 |
| 1,500 | 1.3 | 0.46831 | −0.0002 | 0.017914 | +0.6 |
| 2,000 | 0.39 | 0.46842 | −0.0001 | 0.017842 | −0.1 |
| **3,000 (final)** | **0.027** | **0.46850** | **+0.00001** | **0.017863** | **+0.07** |

The final result agrees with the cluster to 1e-5 in CL and 0.07 counts in drag. It took 2 h 47 min for 3,000 cycles, with a 5.1 GiB peak. The solution was within tolerance (CL 1e-3, CD 1 count) from about 1,000 cycles, about 55 minutes on this laptop. The cluster's ANK solver needed 1,827 steps.

The other conditions run with a 2,000-cycle cap instead of the authors' 3,000 to save time. Any condition still moving at that point continues from ADflow's volume solution.

**Shape 0, all conditions** (DADI, 12 MPI processes; ours on the cluster's reference area):

| Condition | Mach | AoA | CL ours | CL cluster | ΔCL | CD ours | CD cluster | ΔCD, counts | Cycles | Time |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 0.752 | 9.37 | 0.46850 | 0.46849 | +0.00001 | 0.017863 | 0.017856 | +0.07 | 3,000 | 2 h 47 min |
| 1 | 0.756 | 5.47 | 0.06840 | 0.06841 | −0.000005 | 0.010394 | 0.010386 | +0.07 | 2,000 | 1 h 56 min |
| 2 | 0.767 | 2.27 | −0.24214 | −0.24202 | −0.00013 | 0.028357 | 0.028362 | −0.05 | 2,000 | 1 h 56 min |
| 3 | 0.765 | 6.84 | 0.21247 | 0.21246 | +0.00001 | 0.011407 | 0.011429 | −0.22 | 2,000 | 1 h 57 min |

## Open questions

- Condition 0 matches with DADI. Do the low-angle conditions with negative lift (2, 4, 6) converge as well? On the cluster they had the largest CL fluctuation.
- The reference-area offset: confirm it on a second shape, then report it to the authors.
- Result size per case. Surface output is small, but the volume solution needed for restart audits is hundreds of MB.
- Which MDO Lab image tag the authors used. We pinned one; ask the authors.
- The tip cap geometry (1.1e-3 off) and the mesh quality gap: pyHyp version or the tip template?

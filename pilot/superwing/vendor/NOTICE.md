# Vendored from tum-pbs/AeroTransformer

The files here are unchanged copies from
[tum-pbs/AeroTransformer](https://github.com/tum-pbs/AeroTransformer), commit
`3dc350ff69e354d1451eae468c686a368bd6151f` (23 Apr 2026), directory `simulation/`.
Yang Yunjia et al., Thuerey group, TU Munich. Licensed under Apache-2.0 (see `LICENSE`).

| File | sha256 |
|---|---|
| `gen-mesh.superwing.py` | `7daf656cdb23c6de32fba1585e22bb850d1b7e86c2ad7d031239989f2d1f73b4` |
| `run-adflow.py` | `e05f3e8b00bda5e8f20945c77639c1b8c712d94df2417e60d28e8a0560bf9c88` |
| `original_tip.xyz` | `c93c1e2c74da73c9610d5c77e81b2f1d9bc0af53df04e1d608aaa61cda3e7db1` |

`.gitattributes` keeps these bytes exact on every checkout, so the hashes stay valid.

`single-point.py` is not vendored. `../run_case.py` does the same steps and adds two
arguments: the number of MPI processes and the conditions to run. The original
hard-codes 8 processes and runs every condition.

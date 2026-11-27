"""Run a vendored ADflow script with option overrides, leaving the script untouched.

    DECFD_ADFLOW_OPTIONS='{"ANKSubspaceSize": 10}' mpirun -np 6 python adflow_options.py run-adflow.py

The overrides (JSON in $DECFD_ADFLOW_OPTIONS) are merged into the options dictionary
that the script passes to ADFLOW(...). The pilot uses this for the linear-solver
settings of the ANK solver: the cluster's defaults (40 Krylov vectors, ILU(2)) need
far more memory than a 16 GB laptop has on a 3.5M-cell mesh. These settings change
how the solver gets to the steady state, not the discretisation or the converged
solution.
"""
import json
import os
import runpy
import sys

from adflow import ADFLOW

OVERRIDES = json.loads(os.environ.get("DECFD_ADFLOW_OPTIONS") or "{}")

if OVERRIDES:
    _original_init = ADFLOW.__init__

    def _init_with_overrides(self, *args, **kwargs):
        options = dict(kwargs.get("options") or {})
        options.update(OVERRIDES)
        kwargs["options"] = options
        _original_init(self, *args, **kwargs)

    ADFLOW.__init__ = _init_with_overrides

if __name__ == "__main__":
    script = sys.argv[1]
    sys.argv = sys.argv[1:]
    runpy.run_path(script, run_name="__main__")

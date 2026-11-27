"""Fit `rootadj` so that our inner wing reproduces the published SuperWing surface.

configs.dat gives the root adjustment only approximately (pilot/README.md). This builds the
surface mesh with the vendored gen-mesh script, matches every published cell centre to its
nearest cell of ours, and takes Newton steps on rootadj using the inner-wing points. The
matching is redone every iteration; it converges in two or three steps.

Run it inside the pilot image (it needs gen-mesh and the tip template):

    docker run --rm --entrypoint bash -v "$PWD":/data decfd-superwing -c \\
      'source "$BASHRC_MDOLAB"; python /data/pilot/superwing/fit_rootadj.py \\
       /data/pilot/superwing/cases/shape0001/input.json /data/pub_shape0001_xyz.npy'

The published surface comes from fetch_surface.py.
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys

import numpy as np
from scipy.spatial import cKDTree

INNER_BLOCKS = 4   # gen-mesh writes the inner wing (root to kink) as the first four blocks


def surface_builder(workdir="/tmp/fit_rootadj"):
    case = os.path.join(workdir, "a", "case")   # gen-mesh reads ../../original_tip.xyz
    os.makedirs(case, exist_ok=True)
    shutil.copy("/sim/original_tip.xyz", os.path.join(workdir, "original_tip.xyz"))
    spec = importlib.util.spec_from_file_location("genmesh", "/sim/vendor/gen-mesh.superwing.py")
    gm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gm)

    def centres(params, rootadj):
        p = dict(params, rootadj=rootadj)
        p["half_span"] = p["AR"] * (1 + p["TR"]) / 4
        p["ref_area"] = (1 + p["TR"]) * p["half_span"] / 2
        cwd = os.getcwd()
        os.chdir(case)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                gm.surface_meshing(p, folder=case)
                blocks = gm.read_plot3d("wing.xyz")
        finally:
            os.chdir(cwd)
        out, block = [], []
        for b, P in enumerate(blocks):
            P = np.asarray(P)[:, :, 0, :]
            c = (0.25 * (P[:-1, :-1] + P[1:, :-1] + P[:-1, 1:] + P[1:, 1:])).reshape(-1, 3)
            out.append(c)
            block += [b] * len(c)
        return np.concatenate(out), np.array(block)

    return centres


def fit(params, published, start, iterations=6, h=1e-4, log=print):
    centres = surface_builder()
    r = start
    for it in range(iterations):
        c, block = centres(params, r)
        d, j = cKDTree(c).query(published)
        inner = block[j] < INNER_BLOCKS
        g = (centres(params, r + h)[0] - c)[j[inner]] / h
        res = published[inner] - c[j[inner]]
        step = float(np.sum(g * res) / np.sum(g * g))
        log(f"iteration {it}: rootadj {r:.9f}, inner points {inner.sum()}, largest gap {d[inner].max():.2e}")
        r += step
        if abs(step) < 1e-10:
            break
    c, block = centres(params, r)
    d, j = cKDTree(c).query(published)
    return r, {"inner": float(d[block[j] < INNER_BLOCKS].max()),
               "outer": float(d[(block[j] >= INNER_BLOCKS) & (block[j] < 8)].max()),
               "tip": float(d[block[j] >= 8].max())}


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    with open(sys.argv[1]) as f:
        params = json.load(f)
    published = np.load(sys.argv[2])
    start = float(sys.argv[3]) if len(sys.argv) > 3 else params.get("rootadj_configs", params["rootadj"])
    r, gaps = fit(params, published, start)
    print(f"fitted rootadj {r:.9f}; largest gap to the published surface: inner wing {gaps['inner']:.1e}, "
          f"outer wing {gaps['outer']:.1e}, tip cap {gaps['tip']:.1e}")


if __name__ == "__main__":
    main()

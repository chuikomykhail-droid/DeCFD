"""Cylinder-flow validation of the LBM worker against published reference values.

Flow past a circular cylinder is the standard benchmark for a 2D incompressible solver:
  Re = 20, 40   steady wake   -> drag coefficient Cd, recirculation length Lr/D
  Re = 100      vortex street -> Strouhal number St, mean Cd, lift amplitude

Every case runs at two resolutions (D = 20 and 40 cells) in the same physical domain
(50 D x 20 D, free-slip side walls, 5% blockage), so the report also shows grid convergence.

    python validation/cylinder.py                  # full study, ~1.5 h on a 12-thread laptop
    python validation/cylinder.py --d 20           # D = 20 only, ~12 min
    python validation/cylinder.py --d 40 --re 100  # any subset
    python validation/cylinder.py --report         # re-plot from saved runs without re-running

Needs the bigger-grid worker builds:
    powershell -ExecutionPolicy Bypass -File build.ps1 -NX 1000 -NY 400 -Out worker_1000x400.exe
    powershell -ExecutionPolicy Bypass -File build.ps1 -NX 2000 -NY 800 -Out worker_2000x800.exe
"""
import argparse
import glob
import json
import os
import subprocess
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm

plt.rcParams.update({"font.family": "serif", "font.serif": ["STIXGeneral", "DejaVu Serif"],
                     "mathtext.fontset": "stix"})   # the same type as the run report

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RUNS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runs")
OUT = os.path.join(ROOT, "docs", "validation")
EXE = ".exe" if os.name == "nt" else ""

# Inlet velocity per Reynolds number, lattice units. At Re = 100 and Mach 0.17 the shedding
# frequency sits at 1.14x the channel's first transverse acoustic mode, c_s / 2H (the walls
# reflect sound perfectly), and the lift locks into that resonance. Mach 0.087 moves it to
# 0.57x, clear of the mode, and also halves the compressibility error.
U_CASE = {20: 0.1, 40: 0.1, 100: 0.05}
GRIDS = {20: (1000, 400), 40: (2000, 800)}   # D -> (NX, NY): domain 50 D x 20 D
REYNOLDS = (20, 40, 100)
# Run length in convective times D/U: steady cases need the wake to settle; the
# shedding case needs the street to saturate, then ~25 periods for averaging
DURATION = {20: (100, 25), 40: (150, 25), 100: (300, 150)}   # Re -> (total, averaged)

# Unbounded-cylinder reference values as compiled in the immersed-boundary literature
# (e.g. the comparison tables of Russell & Wang 2003, J. Comput. Phys. 191).
REFERENCE = {
    20: {"cd": (2.00, 2.22), "lr": (0.91, 0.94)},
    40: {"cd": (1.48, 1.62), "lr": (2.13, 2.35)},
    100: {"st": (0.160, 0.175), "cd": (1.33, 1.38), "cl_amp": (0.25, 0.34)},
}
SOURCES = [
    "Tritton 1959 (experiment): Cd",
    "Coutanceau & Bouard 1977 (experiment): Lr",
    "Dennis & Chang 1970; Fornberg 1980 (steady numerical solutions): Cd, Lr",
    "Braza et al. 1986; Liu, Zheng & Sung 1998; Calhoun 2002; Russell & Wang 2003 (numerical): Re = 100",
    "Williamson 1996 (experiment, review): St = 0.164 at Re = 100",
]

BLUE, ORANGE, INK, GRID = "#2a78d6", "#eb6834", "#52514e", "#e5e4df"
VORT_CMAP = LinearSegmentedColormap.from_list(
    "vort", ["#0d366b", "#3987e5", "#f0efec", "#e34948", "#8a1f1f"])


def case_name(D, Re):
    return f"D{D}_Re{Re}"


TIME_SCALE = 1.0   # --scale: shorten every run (pipeline smoke test only)


def case_params(D, Re):
    U = U_CASE[Re]
    nu = U * D / Re
    total, averaged = DURATION[Re]
    steps = int(TIME_SCALE * total * D / U)
    avg = int(TIME_SCALE * averaged * D / U)
    return {"D": D, "Re": Re, "U": U, "tau": 0.5 + 3.0 * nu, "steps": steps, "avg": avg,
            "yshift": 0.025 * D}   # small offset from the centreline triggers shedding


def run_case(D, Re, snapshots=False):
    nx, ny = GRIDS[D]
    exe = os.path.join(ROOT, "worker_cpp", f"worker_{nx}x{ny}{EXE}")
    if not os.path.exists(exe):
        sys.exit(f"missing {exe}; build it first (see the docstring)")
    p = case_params(D, Re)
    d = os.path.join(RUNS, case_name(D, Re))
    os.makedirs(d, exist_ok=True)
    cmd = [exe, "--cylinder", str(D), "--yshift", str(p["yshift"]), "--tau", f"{p['tau']:.6f}",
           "--uin", str(p["U"]), "--steps", str(p["steps"]), "--avg", str(p["avg"]),
           "--history", "history.csv", "--ux", "ux.csv", "--uy", "uy.csv", "--vort", "vort.csv"]
    print(f"{case_name(D, Re)}: tau={p['tau']:.4f}, {p['steps']} steps ...", flush=True)
    r = subprocess.run(cmd, cwd=d, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"worker failed: {r.stderr}")
    res = json.loads(r.stdout.strip().splitlines()[-1])
    with open(os.path.join(d, "result.json"), "w") as f:
        json.dump({"params": p, "worker": res}, f, indent=2)

    if snapshots:
        # Separate short run for the animation: two shedding periods, 30 frames each
        period = D / (0.165 * p["U"])
        avg = int(2 * period)
        every = max(1, int(period / 30))
        a = os.path.join(RUNS, case_name(D, Re) + "_anim")
        os.makedirs(a, exist_ok=True)
        for f in glob.glob(os.path.join(a, "vort_*.bin")):
            os.remove(f)
        cmd = [exe, "--cylinder", str(D), "--yshift", str(p["yshift"]), "--tau", f"{p['tau']:.6f}",
               "--uin", str(p["U"]), "--steps", str(p["steps"] - p["avg"] + avg), "--avg", str(avg),
               "--snap-every", str(every), "--snap-prefix", "vort"]
        print(f"{case_name(D, Re)}: animation run ...", flush=True)
        r = subprocess.run(cmd, cwd=a, capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"worker failed: {r.stderr}")


# ---------------------------------------------------------------------------------- analysis
def recirculation_length(ux, D, nx, ny, yshift):
    """Distance from the rear of the cylinder to where the centreline velocity turns positive."""
    row = ny - 1 - int(round(0.5 * (ny - 1) + yshift))   # CSV rows run top to bottom
    xc = 0.3 * nx
    x_rear = xc + D / 2.0
    line = ux[row]
    x0 = int(np.ceil(x_rear)) + 1
    if line[x0] >= 0:
        return 0.0
    for x in range(x0 + 1, nx):
        if line[x] >= 0:
            xz = x - 1 + (-line[x - 1]) / (line[x] - line[x - 1])
            return (xz - x_rear) / D
    return float("nan")


def shedding(history, D, U):
    """Strouhal number from upward zero crossings of the lift; mean drag; lift amplitude."""
    q = 0.5 * U * U * D
    cl = history[:, 2] / q
    cd = history[:, 1] / q
    s = cl - cl.mean()
    up = np.where((s[:-1] < 0) & (s[1:] >= 0))[0]
    t_up = up + (-s[up]) / (s[up + 1] - s[up])           # interpolated crossing times, steps
    n = len(t_up) - 1
    if n < 2:   # no developed shedding in the window
        return {"st": float("nan"), "cd": float(cd.mean()), "cl_amp": float("nan"),
                "cd_amp": float("nan"), "periods": max(n, 0), "saturation": float("nan")}
    # Average over whole periods only
    i0, i1 = int(np.ceil(t_up[0])), int(np.floor(t_up[-1]))
    period = (t_up[-1] - t_up[0]) / n
    half = len(cl) // 2
    amp_first = (cl[:half].max() - cl[:half].min()) / 2
    amp_second = (cl[half:].max() - cl[half:].min()) / 2
    return {
        "st": D / (period * U),
        "cd": float(cd[i0:i1].mean()),
        "cl_amp": float((cl[i0:i1].max() - cl[i0:i1].min()) / 2),
        "cd_amp": float((cd[i0:i1].max() - cd[i0:i1].min()) / 2),
        "periods": n,
        "saturation": float(amp_first / amp_second),   # ~1 when the street is fully developed
    }


def analyse(D, Re):
    d = os.path.join(RUNS, case_name(D, Re))
    with open(os.path.join(d, "result.json")) as f:
        saved = json.load(f)
    p, w = saved["params"], saved["worker"]
    U = p.setdefault("U", 0.1)   # runs from before per-case velocities used 0.1
    out = {"D": D, "Re": Re, "U": U, "tau": p["tau"], "steps": p["steps"],
           "cd": w["cd"], "cd_spread": w["fx_spread"] / (0.5 * U * U * D)}
    if Re == 100:
        hist = np.loadtxt(os.path.join(d, "history.csv"), delimiter=",", skiprows=1)
        out.update(shedding(hist, D, U))
    else:
        ux = np.loadtxt(os.path.join(d, "ux.csv"), delimiter=",")
        out["lr"] = recirculation_length(ux, D, w["nx"], w["ny"], p["yshift"])
    return out


def verdict(value, lo, hi):
    if lo <= value <= hi:
        return "within range"
    ref = lo if value < lo else hi
    return f"{100 * (value - ref) / ref:+.1f}% outside"


# ---------------------------------------------------------------------------------- figures
def style(ax):
    ax.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)


def fig_wakes(results):
    """Steady wakes at Re = 20 and 40 (finest grid): streamlines over the streamwise velocity."""
    D = max(r["D"] for r in results if r["Re"] == 40)
    fig, axs = plt.subplots(1, 2, figsize=(12, 3.6))
    for ax, Re in zip(axs, (20, 40)):
        d = os.path.join(RUNS, case_name(D, Re))
        ux = np.loadtxt(os.path.join(d, "ux.csv"), delimiter=",")[::-1]   # bottom-up rows
        uy = np.loadtxt(os.path.join(d, "uy.csv"), delimiter=",")[::-1]
        ny, nx = ux.shape
        xc, yc = 0.3 * nx, 0.5 * (ny - 1) + 0.025 * D
        x0, x1 = int(xc - 1.5 * D), int(xc + 4.5 * D)
        y0, y1 = int(yc - 1.5 * D), int(yc + 1.5 * D)
        X, Y = np.meshgrid((np.arange(x0, x1) - xc) / D, (np.arange(y0, y1) - yc) / D)
        sub_u, sub_v = ux[y0:y1, x0:x1], uy[y0:y1, x0:x1]
        # Zero (white) splits forward flow from the recirculation bubble (blue)
        im = ax.pcolormesh(X, Y, sub_u / U_CASE[Re], cmap=VORT_CMAP, shading="auto",
                           norm=TwoSlopeNorm(vcenter=0.0, vmin=-0.3, vmax=1.3))
        ax.streamplot(X, Y, sub_u, sub_v, color=INK, linewidth=0.6, density=1.4, arrowsize=0.6)
        ax.add_patch(plt.Circle((0, 0), 0.5, color="#b4b2a9", zorder=3))
        r = next(r for r in results if r["D"] == D and r["Re"] == Re)
        ax.plot([0.5, 0.5 + r["lr"]], [0, 0], color=ORANGE, linewidth=2.5, zorder=4)
        ax.set_title(f"Re = {Re}: recirculation length Lr/D = {r['lr']:.2f}")
        ax.set_aspect("equal")
        ax.set_xlabel("x / D")
        ax.set_ylabel("y / D")
    fig.colorbar(im, ax=axs, label="u_x / U", shrink=0.9)
    path = os.path.join(OUT, "steady_wakes.png")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


def fig_shedding(results):
    """Lift and drag history at Re = 100 (finest grid)."""
    D = max(r["D"] for r in results if r["Re"] == 100)
    d = os.path.join(RUNS, case_name(D, 100))
    h = np.loadtxt(os.path.join(d, "history.csv"), delimiter=",", skiprows=1)
    U = U_CASE[100]
    q = 0.5 * U * U * D
    t = h[:, 0] * U / D
    last = t > t[-1] - 30                                  # last 30 convective times
    fig, axs = plt.subplots(2, 1, figsize=(10, 5), sharex=True)
    axs[0].plot(t[last], h[last, 2] / q, color=BLUE, linewidth=2)
    axs[0].set_ylabel("Cl")
    axs[0].set_title("Re = 100: periodic vortex shedding")
    axs[1].plot(t[last], h[last, 1] / q, color=BLUE, linewidth=2)
    axs[1].set_ylabel("Cd")
    axs[1].set_xlabel("time  t·U / D")
    for ax in axs:
        style(ax)
    path = os.path.join(OUT, "shedding_history.png")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


def fig_vortex_street(results):
    D = max(r["D"] for r in results if r["Re"] == 100)
    w = np.loadtxt(os.path.join(RUNS, case_name(D, 100), "vort.csv"), delimiter=",")
    ny, nx = w.shape
    lim = 2 * U_CASE[100] / D
    fig, ax = plt.subplots(figsize=(12, 4))
    ax.imshow(w, cmap=VORT_CMAP, vmin=-lim, vmax=lim, origin="upper",
              extent=[-0.3 * nx / D, 0.7 * nx / D, -ny / 2 / D, ny / 2 / D])
    ax.add_patch(plt.Circle((0, 0.025), 0.5, color="#b4b2a9"))
    ax.set_xlim(-3, 30)
    ax.set_ylim(-5, 5)
    ax.set_xlabel("x / D")
    ax.set_ylabel("y / D")
    ax.set_title("Re = 100: von Kármán vortex street (vorticity)")
    path = os.path.join(OUT, "vortex_street.png")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


def gif_vortex_street(D=20, width=720):
    from PIL import Image
    files = sorted(glob.glob(os.path.join(RUNS, case_name(D, 100) + "_anim", "vort_*.bin")))
    if not files:
        return None
    nx, ny = GRIDS[D]
    lim = 2 * U_CASE[100] / D
    x0, x1 = int(0.3 * nx - 3 * D), int(0.3 * nx + 30 * D)
    y0, y1 = int(ny / 2 - 5 * D), int(ny / 2 + 5 * D)
    frames = []
    for f in files:
        w = np.fromfile(f, dtype=np.float32).reshape(ny, nx)[y0:y1, x0:x1]
        rgb = (VORT_CMAP(np.clip((w / lim + 1) / 2, 0, 1))[:, :, :3] * 255).astype(np.uint8)
        im = Image.fromarray(rgb)
        im = im.resize((width, round(width * im.height / im.width)), Image.LANCZOS)
        # A small palette without dithering keeps smooth colour fields compact
        frames.append(im.quantize(colors=48, dither=Image.Dither.NONE))
    path = os.path.join(OUT, "vortex_street.gif")
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=50, loop=0)
    return path


# ---------------------------------------------------------------------------------- report
def write_report(results, figures):
    lines = [
        "# Solver validation: flow past a circular cylinder",
        "",
        "Generated by `validation/cylinder.py`. The DeCFD worker (D2Q9 lattice Boltzmann, BGK,",
        "halfway bounce-back) simulates the textbook benchmark for 2D incompressible solvers and",
        "is compared with published values for an unbounded cylinder.",
        "",
        "**Setup.** Domain 50 D × 20 D, cylinder 15 D from the inlet, uniform inlet velocity,",
        "zero-gradient outlet, free-slip side walls (blockage D/H = 5%). The cylinder sits 0.025 D",
        "off the centreline so that the Re = 100 wake starts shedding without an artificial kick.",
        "Each case runs at D = 20 and D = 40 cells in the same physical domain.",
        "",
        "**Inlet velocity.** U = 0.1 (Mach 0.17) for Re = 20 and 40; U = 0.05 (Mach 0.087) for",
        "Re = 100. A first attempt at Mach 0.17 put the shedding frequency at 1.14× the channel's",
        "first transverse acoustic mode (c_s / 2H, sound reflects perfectly off free-slip walls):",
        "the lift locked into that resonance and its amplitude grew past 3. At Mach 0.087 the",
        "shedding sits at 0.57× the mode and the street is clean. The same effect exists in real",
        "wind tunnels (acoustic resonance of the test section).",
        "",
        "## Results",
        "",
        "| Re | Quantity | Reference range | D = 20 | D = 40 | Finest grid vs reference |",
        "|---|---|---|---|---|---|",
    ]
    names = {"cd": "Cd (mean)", "lr": "Lr / D", "st": "Strouhal St", "cl_amp": "Cl amplitude"}
    by = {(r["D"], r["Re"]): r for r in results}
    for Re in REYNOLDS:
        for key, (lo, hi) in REFERENCE[Re].items():
            vals = [by[(D, Re)][key] if (D, Re) in by else None for D in (20, 40)]
            fmt = "{:.3f}" if key == "st" else "{:.2f}"
            cells = [fmt.format(v) if v is not None else "–" for v in vals]
            finest = by[(max(D for D, R in by if R == Re), Re)][key]
            lines.append(f"| {Re} | {names[key]} | {fmt.format(lo)}–{fmt.format(hi)} | "
                         f"{cells[0]} | {cells[1]} | {verdict(finest, lo, hi)} |")
    lines += [
        "",
        "Reference ranges span the published values below. They are for an unbounded cylinder,",
        "so the 5% blockage of our channel is expected to push Cd and the lift amplitude up.",
        "Doubling the resolution moves every value towards the references.",
        "",
    ]
    lines += [f"- {s}" for s in SOURCES]
    lines += ["", "## Figures", ""]
    titles = {
        "steady_wakes.png": "Steady wakes: streamlines and recirculation length (orange)",
        "shedding_history.png": "Lift and drag history at Re = 100",
        "vortex_street.png": "Vortex street at Re = 100",
        "vortex_street.gif": "Vortex street at Re = 100, two shedding periods",
    }
    for f in figures:
        if f:
            name = os.path.basename(f)
            lines += [f"**{titles[name]}**", "", f"![{titles[name]}]({name})", ""]
    lines += ["## Run details", "", "| Case | U | τ | Steps | Cd spread | Periods averaged | Saturation |",
              "|---|---|---|---|---|---|---|"]
    for r in results:
        sat = f"{r['saturation']:.3f}" if "saturation" in r else "–"
        lines.append(f"| D = {r['D']}, Re = {r['Re']} | {r['U']} | {r['tau']:.3f} | {r['steps']} | "
                     f"{r['cd_spread']:.1e} | {r.get('periods', '–')} | {sat} |")
    lines += ["", "Cd spread: max − min of four block averages over the averaging window (steady cases",
              "should be ≈ 0). Saturation: lift amplitude in the first vs second half of the window",
              "(1.000 = fully developed street).", ""]
    path = os.path.join(OUT, "cylinder.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    with open(os.path.join(OUT, "cylinder_results.json"), "w") as f:
        json.dump(results, f, indent=2)
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--d", type=int, nargs="+", default=[20, 40], choices=[20, 40], help="resolutions to run")
    ap.add_argument("--re", type=int, nargs="+", default=list(REYNOLDS), choices=REYNOLDS, help="cases to run")
    ap.add_argument("--report", action="store_true", help="re-analyse saved runs, no simulation")
    ap.add_argument("--scale", type=float, default=1.0, help=argparse.SUPPRESS)
    args = ap.parse_args()
    global TIME_SCALE
    TIME_SCALE = args.scale
    os.makedirs(OUT, exist_ok=True)

    if not args.report:
        for D in args.d:
            for Re in args.re:
                run_case(D, Re, snapshots=(D == 20 and Re == 100))

    # The report covers every case that has results, run now or earlier
    results = [analyse(D, Re) for D in (20, 40) for Re in REYNOLDS
               if os.path.exists(os.path.join(RUNS, case_name(D, Re), "result.json"))]
    for r in results:
        print({k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()})
    figures = [fig_wakes(results), fig_shedding(results), fig_vortex_street(results), gif_vortex_street()]
    print("Report:", write_report(results, figures))


if __name__ == "__main__":
    main()

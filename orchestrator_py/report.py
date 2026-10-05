"""Static report for a DeCFD run: what the search found, how it got there, who computed it.

    python orchestrator_py/report.py               # latest run
    python orchestrator_py/report.py <run_dir>     # a specific run
    python orchestrator_py/report.py --no-anim     # skip the start-up animation (it re-runs the worker)

Writes <run_dir>/report/:
  convergence.png   best L/D per generation over the whole population
  shapes.png        best shape at a few generations, overlaid
  before_after.png  flow around the generation-0 winner vs the final winner
  genes.png         how each design parameter of the best shape evolved
  network.png       work, audits and fraud per epoch; earnings and stakes per miner
  evolution.gif     the best shape's flow field, generation by generation
  startup.gif       the flow starting around the final shape (starting vortex)
  summary.json      headline numbers (used by the dashboard)
"""
import argparse
import glob
import json
import os
import shutil
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import MaxNLocator

from geometry import shape_outline
from runs import latest_run_dir, write_json
from visualizer import BLUE, BODY, DIVERGING_CMAP, GRID, INK, SPEED_MAX, U_IN, draw_speed, load_field

LIGHT_BLUE = "#86b6ef"
ORANGE = "#eb6834"      # categorical slot 2
RED = "#e34948"         # reserved for fraud / critical status, always with a text label
RAMP = ["#b7d3f6", "#86b6ef", "#5598e7", "#2a78d6", "#0d366b"]   # sequential: early -> late


# ------------------------------------------------------------------------------ loading
def load_run(run_dir):
    with open(os.path.join(run_dir, "history.json"), encoding="utf-8") as f:
        history = json.load(f)
    net = os.path.join(run_dir, "network")
    events = []
    log = os.path.join(net, "ledger_tx.jsonl")
    if os.path.exists(log):
        with open(log, encoding="utf-8") as f:
            events = [json.loads(line) for line in f if line.strip()]
    participants = {}
    if os.path.exists(os.path.join(net, "participants.json")):
        with open(os.path.join(net, "participants.json"), encoding="utf-8") as f:
            participants = json.load(f)["participants"]
    return history, events, participants


def epoch_stats(events):
    """Tasks, audits and fraud per epoch, reconstructed from the ledger event log."""
    epoch_of, stats = {}, {}
    for e in events:
        if e["ix"] == "create_task":
            epoch_of[e["task"]] = e["epoch"]
            stats.setdefault(e["epoch"], {"tasks": 0, "audits": 0, "fraud": 0})["tasks"] += 1
        elif e["ix"] in ("verify_ok", "slash"):
            s = stats[epoch_of[e["task"]]]
            s["audits"] += 1
            s["fraud"] += e["ix"] == "slash"
    return [stats.get(g, {"tasks": 0, "audits": 0, "fraud": 0}) for g in range(max(stats) + 1)] if stats else []


def miner_stats(events, participants):
    miners = {}
    for e in events:
        if e["ix"] == "register_miner":
            miners[e["signer"]] = {"name": e["name"], "stake": e["stake"], "earned": 0, "tasks": 0,
                                   "caught": 0, "banned": False}
        elif e["ix"] == "submit_result":
            miners[e["signer"]]["tasks"] += 1
        elif e["ix"] == "settle_task":
            miners[e["miner"]]["earned"] += e["paid"]
        elif e["ix"] == "slash":
            m = miners[e["miner"]]
            m["stake"] -= e["penalty"]
            m["caught"] += 1
            m["banned"] = m["banned"] or e["banned"]
    return miners


# ------------------------------------------------------------------------------ figures
def style(ax):
    ax.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.spines["left"].set_color(INK)
    ax.spines["bottom"].set_color(INK)
    ax.tick_params(colors=INK)


def save(fig, out, name):
    path = os.path.join(out, name)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


def fig_convergence(history, out):
    gens = history["generations"]
    fig, ax = plt.subplots(figsize=(9, 4.2))
    for g in gens:
        pts = [v for v in g["population_ld"] if v > 0]
        ax.scatter([g["gen"]] * len(pts), pts, s=14, color=LIGHT_BLUE, alpha=0.7, linewidths=0,
                   label="population" if g["gen"] == 0 else None)
    best = [g["best"]["ld"] for g in gens]
    ax.plot([g["gen"] for g in gens], best, color=BLUE, linewidth=2, marker="o", markersize=5,
            label="best of generation")
    gain = 100 * (best[-1] / best[0] - 1) if best[0] > 0 else float("nan")
    ax.annotate(f"{best[-1]:.2f}  (+{gain:.0f}% vs generation 0)", (gens[-1]["gen"], best[-1]),
                xytext=(-8, 10), textcoords="offset points", ha="right", color=INK)
    ax.set_xlabel("generation")
    ax.set_ylabel("lift / drag")
    ax.set_title("Search progress", loc="left")
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax.legend(frameon=False, loc="lower right")
    style(ax)
    return save(fig, out, "convergence.png")


def pick_generations(gens, k=5):
    """Generation 0, the last one, and evenly spaced generations in between where the best shape changed."""
    changed = [g for i, g in enumerate(gens) if i == 0 or g["best"]["task_id"] != gens[i - 1]["best"]["task_id"]]
    if changed[-1] is not gens[-1] and changed[-1]["best"]["task_id"] != gens[-1]["best"]["task_id"]:
        changed.append(gens[-1])
    if len(changed) <= k:
        return changed
    idx = np.round(np.linspace(0, len(changed) - 1, k)).astype(int)
    return [changed[i] for i in idx]


def fig_shapes(history, out):
    picks = pick_generations(history["generations"])
    colors = RAMP[-len(picks):] if len(picks) < len(RAMP) else RAMP
    fig, ax = plt.subplots(figsize=(9, 4))
    for g, c in zip(picks, colors):
        b = g["best"]
        x, y = shape_outline(b)
        L = b["L"]
        last = g is picks[-1]
        ax.plot(x / L, y / L, color=c, linewidth=2.4 if last else 1.6,
                label=f"generation {g['gen']}   L/D {b['ld']:.2f}")
        if last:
            ax.fill(x / L, y / L, color=c, alpha=0.15)
    ax.annotate("flow", xy=(0.16, 0.93), xytext=(0.02, 0.93), xycoords="axes fraction",
                textcoords="axes fraction", va="center", color=INK,
                arrowprops=dict(arrowstyle="->", color=INK, linewidth=1.2))
    ax.set_aspect("equal")
    ax.set_xlabel("x / chord")
    ax.set_ylabel("y / chord")
    ax.set_title("Best shape over the generations", loc="left")
    ax.legend(frameon=False, loc="center left", bbox_to_anchor=(1.0, 0.5))
    style(ax)
    return save(fig, out, "shapes.png")


def fig_before_after(history, run_dir, out):
    gens = history["generations"]
    first, last = gens[0], gens[-1]
    if not (first.get("field") and last.get("field")):
        return None
    fig, axs = plt.subplots(2, 1, figsize=(10, 7.2))
    for ax, g, label in zip(axs, (first, last), ("Generation 0 winner", f"Final winner (generation {last['gen']})")):
        b = g["best"]
        im = draw_speed(ax, *load_field(os.path.join(run_dir, g["field"])), streamlines=True)
        ax.set_title(f"{label}:  L/D {b['ld']:.2f}   (Cd {b['cd']:.3f}, Cl {b['cl']:.3f})", loc="left")
    cb = fig.colorbar(im, ax=axs, fraction=0.02, pad=0.01)
    ticks = np.arange(0, SPEED_MAX + 1e-9, 0.05)
    cb.set_ticks(ticks)
    cb.set_ticklabels([f"{t / U_IN:.1f}" for t in ticks])
    cb.set_label("speed / inlet speed")
    return save(fig, out, "before_after.png")


def fig_genes(history, out):
    gens = history["generations"]
    x = [g["gen"] for g in gens]
    panels = [("angle of attack, °", [g["best"]["alpha"] for g in gens]),
              ("camber, cells", [g["best"]["camber"] for g in gens])]
    names = ["nose t0", "t1 (¼ chord)", "t2 (½ chord)", "t3 (¾ chord)", "tail t4"]
    panels += [(f"thickness {n}, cells", [g["best"]["t_pts"][i] for g in gens]) for i, n in enumerate(names)]
    panels.append(("mutation step (1/5 rule)", [g["mutation_scale"] for g in gens]))
    fig, axs = plt.subplots(2, 4, figsize=(13, 5.2), sharex=True)
    for ax, (title, y) in zip(axs.flat, panels):
        ax.plot(x, y, color=BLUE, linewidth=2, drawstyle="steps-post")
        ax.set_title(title, loc="left", fontsize=10)
        ax.xaxis.set_major_locator(MaxNLocator(integer=True, nbins=5))
        style(ax)
    for ax in axs[1]:
        ax.set_xlabel("generation")
    fig.suptitle("Design parameters of the best shape", x=0.01, ha="left")
    fig.tight_layout()
    return save(fig, out, "genes.png")


def fig_network(events, participants, out):
    per_epoch = epoch_stats(events)
    miners = miner_stats(events, participants)
    if not per_epoch:
        return None
    fig, (a, b) = plt.subplots(1, 2, figsize=(13, 4.2), gridspec_kw={"width_ratios": [1.6, 1]})

    x = np.arange(len(per_epoch))
    w = 0.38
    a.bar(x - w / 2, [s["tasks"] for s in per_epoch], w, color=BLUE, label="tasks computed")
    a.bar(x + w / 2, [s["audits"] for s in per_epoch], w, color=ORANGE, label="tasks re-computed by the verifier")
    for i, s in enumerate(per_epoch):
        if s["fraud"]:
            top = max(s["tasks"], s["audits"])
            a.annotate(f"{s['fraud']} fake{'s' if s['fraud'] > 1 else ''} caught", (i, top), xytext=(0, 6),
                       textcoords="offset points",
                       ha="center", color=RED, fontsize=9, fontweight="bold")
    a.set_xlabel("epoch (generation)")
    a.set_ylabel("tasks")
    a.set_title("Work and audits per epoch", loc="left")
    a.xaxis.set_major_locator(MaxNLocator(integer=True))
    a.legend(frameon=False, loc="upper right")
    style(a)

    names = [m["name"] for m in miners.values()]
    y = np.arange(len(names))
    b.barh(y + 0.2, [m["earned"] for m in miners.values()], 0.38, color=BLUE, label="earned")
    b.barh(y - 0.2, [m["stake"] for m in miners.values()], 0.38, color=BODY, label="stake left")
    for i, m in enumerate(miners.values()):
        if m["banned"]:
            b.text(m["stake"] + 5, i - 0.2, f"banned: caught {m['caught']}×", va="center", color=RED, fontsize=9,
                   fontweight="bold")
    b.set_yticks(y)
    b.set_yticklabels(names)
    b.invert_yaxis()
    b.set_xlabel("tokens")
    b.set_title("Miners", loc="left")
    b.legend(frameon=False, loc="lower right")
    style(b)
    fig.tight_layout()
    return save(fig, out, "network.png")


# ------------------------------------------------------------------------------ animations
def gif_evolution(run_dir, out, width=900, duration=500):
    from PIL import Image
    files = sorted(glob.glob(os.path.join(run_dir, "frames", "field_*.png")))
    if not files:
        return None
    frames = []
    for f in files:
        with Image.open(f) as im:
            h = round(im.height * width / im.width)
            # A small palette without dithering keeps the GIF compact
            frames.append(im.convert("RGB").resize((width, h), Image.LANCZOS)
                          .quantize(colors=64, dither=Image.Dither.NONE))
    frames += [frames[-1]] * 4   # hold the final design for a moment
    path = os.path.join(out, "evolution.gif")
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=duration, loop=0)
    return path


def gif_startup(history, run_dir, out, n_frames=60):
    """The flow starting around the final shape: the starting vortex leaves the trailing edge.

    A dedicated worker run from rest, with vorticity snapshots over the whole evaluation
    window. (The final shapes have steady wakes: the oscillation seen early on decays.)"""
    from PIL import Image
    from worker_runner import run_worker, shape_args

    class _S:  # shape_args() expects attributes
        pass
    b = history["generations"][-1]["best"]
    s = _S()
    s.L, s.t_pts, s.alpha, s.camber = b["L"], b["t_pts"], b["alpha"], b["camber"]
    steps = history["config"].get("steps", 6000)
    every = max(1, steps // n_frames)
    tmp = os.path.join(run_dir, "report", "startup_tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    res = run_worker(shape_args(s), run_dir=tmp, threads=None, timeout=None,
                     extra_args=["--steps", str(steps), "--avg", str(steps),
                                 "--snap-every", str(every), "--snap-prefix", "w"])
    if res.get("status") != "ok":
        return None
    nx, ny = res["nx"], res["ny"]
    snaps = [np.fromfile(f, dtype=np.float32).reshape(ny, nx)[:, 60:]
             for f in sorted(glob.glob(os.path.join(tmp, "w_*.bin")))]
    stack = np.stack(snaps)
    # Colour scale from the developed flow; the first instants are far stronger and saturate
    lim = float(np.percentile(np.abs(stack[len(snaps) // 2:]), 99.5)) or 1e-6
    # The body is zero in every snapshot; an isolated exact zero in the flow is not
    solid = np.all(stack == 0, axis=0)
    solid[[0, 1, -2, -1], :] = False
    solid[:, -1] = False
    frames = []
    for k, w in enumerate(snaps):
        fig, ax = plt.subplots(figsize=(9, 3.4))
        ax.imshow(np.ma.masked_where(solid, w), cmap=DIVERGING_CMAP.with_extremes(bad=BODY),
                  vmin=-lim, vmax=lim, origin="upper", interpolation="bilinear")
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_title("Flow starting around the final shape (vorticity)", loc="left", fontsize=12)
        chords = (k * every + 1) * U_IN / b["L"]
        ax.set_title(f"{chords:4.1f} chord lengths travelled", loc="right", fontsize=12, color=INK)
        fig.tight_layout()
        fig.canvas.draw()
        frames.append(Image.fromarray(np.asarray(fig.canvas.buffer_rgba())[:, :, :3])
                      .quantize(colors=64, dither=Image.Dither.NONE))
        plt.close(fig)
    shutil.rmtree(tmp, ignore_errors=True)
    frames += [frames[-1]] * 10   # rest on the developed flow
    path = os.path.join(out, "startup.gif")
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=80, loop=0)
    return path


# ------------------------------------------------------------------------------ summary
def summary(history, events, participants):
    gens = history["generations"]
    best = gens[-1]["best"]
    first_ld = gens[0]["best"]["ld"]
    first_reach = next(g["gen"] for g in gens if g["best"]["task_id"] == best["task_id"])
    per_epoch = epoch_stats(events)
    miners = miner_stats(events, participants)
    slashes = [e for e in events if e["ix"] == "slash"]
    refunds = [e["refund"] for e in events if e["ix"] == "close_job"]
    budget = next((e["budget"] for e in events if e["ix"] == "create_job"), None)
    v = history.get("verification")
    return {
        "run": history["run"],
        "finished": history.get("finished", False),
        "elapsed_s": history.get("elapsed", gens[-1].get("elapsed")),
        "generations": len(gens),
        "best": {**best, "found_in_generation": first_reach},
        "first_ld": first_ld,
        "improvement_pct": 100 * (best["ld"] / first_ld - 1) if first_ld > 0 else None,
        "verification": None if not v else {
            "short_ld": v["short_ld"], "long_ld": v["ld"], "steps": v["steps"],
            "diff_pct": 100 * (v["ld"] / v["short_ld"] - 1) if v["short_ld"] else None},
        "network": {
            "tasks": sum(s["tasks"] for s in per_epoch),
            "audits": sum(s["audits"] for s in per_epoch),
            "fraud_caught": len(slashes),
            "slashed": sum(e["penalty"] for e in slashes),
            "to_verifier": sum(e["to_verifier"] for e in slashes),
            "budget": budget,
            "paid_to_miners": sum(m["earned"] for m in miners.values()),
            "refunded": refunds[0] if refunds else None,
            "banned": [m["name"] for m in miners.values() if m["banned"]],
        },
    }


def build_report(run_dir, animate=True):
    history, events, participants = load_run(run_dir)
    if not history["generations"]:
        print("No generations yet.")
        return None
    out = os.path.join(run_dir, "report")
    os.makedirs(out, exist_ok=True)
    made = [fig_convergence(history, out), fig_shapes(history, out), fig_before_after(history, run_dir, out),
            fig_genes(history, out), fig_network(events, participants, out), gif_evolution(run_dir, out)]
    if animate:
        made.append(gif_startup(history, run_dir, out))
    write_json(os.path.join(out, "summary.json"), summary(history, events, participants))
    for p in made:
        if p:
            print(f"  {os.path.relpath(p, run_dir)}")
    print(f"Report: {out}")
    return out


def main():
    ap = argparse.ArgumentParser(description="Build the static report for a DeCFD run")
    ap.add_argument("run_dir", nargs="?", help="run folder (default: the latest run)")
    ap.add_argument("--no-anim", action="store_true", help="skip the start-up animation")
    args = ap.parse_args()
    run_dir = args.run_dir or latest_run_dir()
    if not run_dir or not os.path.exists(os.path.join(run_dir, "history.json")):
        sys.exit("No run folder found. Run app.py first, or pass a run folder.")
    build_report(run_dir, animate=not args.no_anim)


if __name__ == "__main__":
    main()

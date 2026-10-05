"""Flow fields: storage and the per-generation frame (one panel: speed field + body)."""
import os

import matplotlib
matplotlib.use("Agg")  # render to files only; no GUI backend needed
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LinearSegmentedColormap

BLUE = "#2a78d6"     # series colour (categorical slot 1)
BODY = "#b4b2a9"     # solid cells
INK = "#52514e"      # outlines, secondary text
GRID = "#e5e4df"
U_IN = 0.1
SPEED_MAX = 0.15     # fixed colour scale so frames are comparable across generations
# Sequential single-hue ramp for speed: light = slow (wake), dark = fast
SPEED_CMAP = LinearSegmentedColormap.from_list(
    "speed", ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"]).with_extremes(bad=BODY)
# Diverging blue <-> red with a neutral midpoint, for signed quantities (vorticity, u_x)
DIVERGING_CMAP = LinearSegmentedColormap.from_list(
    "signed", ["#0d366b", "#3987e5", "#f0efec", "#e34948", "#8a1f1f"])


# ------------------------------------------------------------------------------ field storage
def csv_to_npz(ux_csv, uy_csv, npz_path):
    """Pack the worker's u_x / u_y CSVs (rows top to bottom) into one compressed file."""
    ux = np.loadtxt(ux_csv, delimiter=",", dtype=np.float32)
    uy = np.loadtxt(uy_csv, delimiter=",", dtype=np.float32)
    np.savez_compressed(npz_path, ux=ux, uy=uy)
    os.remove(ux_csv)
    os.remove(uy_csv)
    return npz_path


def load_field(npz_path):
    with np.load(npz_path) as f:
        return f["ux"], f["uy"]


def draw_speed(ax, ux, uy, x0=60, streamlines=False):
    """Speed field with the body in grey, cropped to x >= x0 (the inlet region is empty)."""
    speed = np.hypot(ux, uy)
    solid = speed == 0.0  # the worker writes exactly 0 inside the body
    ny, nx = speed.shape
    sl = (slice(None), slice(x0, nx))
    extent = (x0 - 0.5, nx - 0.5, ny - 0.5, -0.5)
    im = ax.imshow(np.ma.masked_where(solid[sl], speed[sl]), cmap=SPEED_CMAP, vmin=0, vmax=SPEED_MAX,
                   origin="upper", extent=extent, interpolation="bilinear")
    ax.contour(np.arange(x0, nx), np.arange(ny), solid[sl], levels=[0.5], colors=INK, linewidths=0.8)
    if streamlines:
        X, Y = np.meshgrid(np.arange(x0, nx), np.arange(ny))
        u, v = np.where(solid, 0, ux)[sl], np.where(solid, 0, -uy)[sl]   # rows run downward
        ax.streamplot(X, Y, u, v, color=INK, linewidth=0.5, density=1.6, arrowsize=0.5)
        ax.set_xlim(x0 - 0.5, nx - 0.5)
        ax.set_ylim(ny - 0.5, -0.5)
    ax.set_xticks([])
    ax.set_yticks([])
    return im


# ------------------------------------------------------------------------------ frames
class Visualizer:
    def __init__(self, frames_dir):
        self.frames_dir = frames_dir
        os.makedirs(self.frames_dir, exist_ok=True)

    def render_field(self, gen, ld, field_path):
        """One frame per generation: the best shape's flow field. Returns the image path."""
        fig, ax = plt.subplots(figsize=(9, 3.6))
        if field_path and os.path.exists(field_path):
            im = draw_speed(ax, *load_field(field_path))
            cb = fig.colorbar(im, ax=ax, fraction=0.025, pad=0.01)
            cb.set_label("speed / inlet speed", color=INK)
            cb.set_ticks(np.arange(0, SPEED_MAX + 1e-9, 0.05))
            cb.set_ticklabels([f"{t / U_IN:.1f}" for t in np.arange(0, SPEED_MAX + 1e-9, 0.05)])
        else:
            ax.text(0.5, 0.5, "flow field not available", ha="center", va="center", transform=ax.transAxes)
            ax.set_axis_off()
        ax.set_title(f"Generation {gen}", loc="left", fontsize=13)
        ax.set_title(f"best L/D = {ld:.2f}", loc="right", fontsize=13, color=BLUE)
        fig.tight_layout()
        path = os.path.join(self.frames_dir, f"field_{gen:03d}.png")
        fig.savefig(path, dpi=120)
        plt.close(fig)
        return path

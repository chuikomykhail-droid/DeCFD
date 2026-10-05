import os

import matplotlib
matplotlib.use("Agg")  # render to files only; no GUI backend needed
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.ticker import MaxNLocator

SERIES = "#2a78d6"   # shape and L/D line
BODY = "#b4b2a9"     # solid cells on the heatmap
INK = "#52514e"      # body outline
GRID = "#e5e4df"
# Sequential single-hue ramp for speed: light = slow (wake), dark = fast
SPEED_CMAP = LinearSegmentedColormap.from_list(
    "speed", ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"]).with_extremes(bad=BODY)


class Visualizer:
    def __init__(self, frames_dir):
        self.frames_dir = frames_dir
        os.makedirs(self.frames_dir, exist_ok=True)

    def render_generation(self, gen, best_shape, ld_history):
        """Save the frame for one generation and return its path."""
        fig, axs = plt.subplots(3, 1, figsize=(10, 12))

        # 1. Shape Boundary Approximation
        axs[0].set_title(f"Best Shape Outline (Gen {gen}, α={best_shape.alpha:.1f}°, c={best_shape.camber:.1f})")
        axs[0].set_xlim(0, 100)
        axs[0].set_ylim(-30, 30)

        # Simple rendering for top/bottom curve using Cosine Interpolation
        xs = np.linspace(0, best_shape.L, 200)
        ys = []
        n_segments = len(best_shape.t_pts) - 1
        seg_len = best_shape.L / n_segments

        for x in xs:
            seg = min(int(x / seg_len), n_segments - 1)
            x0 = seg * seg_len
            x1 = (seg + 1) * seg_len
            t0 = best_shape.t_pts[seg]
            t1 = best_shape.t_pts[seg + 1]

            fraction = (x - x0) / (x1 - x0) if x1 > x0 else 0
            mu = (1.0 - np.cos(fraction * np.pi)) / 2.0
            half_T = (t0 * (1.0 - mu) + t1 * mu) / 2.0

            # Simple camber approximation for visualization
            yc = 4.0 * best_shape.camber * (x / best_shape.L) * (1.0 - x / best_shape.L)
            ys.append(half_T + yc)

        # Rotation by angle of attack for visualization
        a_rad = np.radians(-best_shape.alpha)
        ca, sa = np.cos(a_rad), np.sin(a_rad)

        top_x = [x * ca - ys[i] * sa for i, x in enumerate(xs)]
        top_y = [x * sa + ys[i] * ca for i, x in enumerate(xs)]

        bottom_y_pts = [yc - (ys[i] - yc) for i, yc in enumerate([4.0 * best_shape.camber * (x / best_shape.L) * (1.0 - x / best_shape.L) for x in xs])]
        bot_x = [x * ca - bottom_y_pts[i] * sa for i, x in enumerate(xs)]
        bot_y = [x * sa + bottom_y_pts[i] * ca for i, x in enumerate(xs)]

        axs[0].plot(top_x, top_y, color=SERIES, linewidth=2)
        axs[0].plot(bot_x, bot_y, color=SERIES, linewidth=2)

        # Correctly fill the polygon by connecting top and reversed bottom
        poly_x = top_x + bot_x[::-1]
        poly_y = top_y + bot_y[::-1]
        axs[0].fill(poly_x, poly_y, color=SERIES, alpha=0.25)

        axs[0].set_aspect('equal', 'box')
        axs[0].grid(True, color=GRID)
        axs[0].set_axisbelow(True)

        # 2. Velocity Heatmap
        axs[1].set_title(f"Velocity Magnitude (Gen {gen}, L/D={best_shape.ld:.2f})")
        if best_shape.heatmap and os.path.exists(best_shape.heatmap):
            data = np.loadtxt(best_shape.heatmap, delimiter=',')
            solid = data == 0.0  # the worker writes exactly 0 inside the body
            im = axs[1].imshow(np.ma.masked_where(solid, data), cmap=SPEED_CMAP, origin='upper', vmin=0)
            axs[1].contour(solid, levels=[0.5], colors=INK, linewidths=0.8)
            plt.colorbar(im, ax=axs[1], label="|u| (lattice units)")
            axs[1].set_xlabel("X (Grid cells)")
            axs[1].set_ylabel("Y (Grid cells)")
        else:
            axs[1].text(0.5, 0.5, 'Heatmap data not found', horizontalalignment='center', verticalalignment='center')

        # 3. Evolution Line Chart
        axs[2].set_title("Best Lift/Drag over Generations")
        axs[2].plot(range(len(ld_history)), ld_history, color=SERIES, linewidth=2,
                    marker='o', markersize=4 if len(ld_history) > 30 else 6)
        axs[2].set_xlabel("Generation")
        axs[2].set_ylabel("Lift / Drag")
        axs[2].xaxis.set_major_locator(MaxNLocator(integer=True))
        axs[2].grid(True, color=GRID)
        axs[2].set_axisbelow(True)

        plt.tight_layout()
        # Every run writes into a fresh folder, so a frame is never overwritten (or locked by a viewer)
        save_path = os.path.join(self.frames_dir, f"generation_{gen:03d}.png")
        plt.savefig(save_path, dpi=200)
        plt.close(fig)

        print(f"Rendered visualization to {save_path}")
        return save_path

import matplotlib.pyplot as plt
import numpy as np
import os

class Visualizer:
    def __init__(self):
        self.results_dir = os.path.join(os.path.dirname(__file__), "results")
        os.makedirs(self.results_dir, exist_ok=True)
        
    def render_generation(self, gen, best_shape, best_drags):
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
        
        axs[0].plot(top_x, top_y, 'b-', label='Top')
        axs[0].plot(bot_x, bot_y, 'b-', label='Bottom')
        
        # Correctly fill the polygon by connecting top and reversed bottom
        poly_x = top_x + bot_x[::-1]
        poly_y = top_y + bot_y[::-1]
        axs[0].fill(poly_x, poly_y, color='blue', alpha=0.3)
        
        axs[0].set_aspect('equal', 'box')
        axs[0].grid(True, linestyle='--', alpha=0.6)
        
        # 2. Velocity Heatmap
        axs[1].set_title(f"Velocity Heatmap (Gen {gen}, L/D={(-best_shape.fitness):.2f})")
        if best_shape.heatmap and os.path.exists(best_shape.heatmap):
            data = np.loadtxt(best_shape.heatmap, delimiter=',')
            im = axs[1].imshow(data, cmap='jet', origin='upper')
            plt.colorbar(im, ax=axs[1], label="Velocity")
            axs[1].set_xlabel("X (Grid cells)")
            axs[1].set_ylabel("Y (Grid cells)")
        else:
            axs[1].text(0.5, 0.5, 'Heatmap data not found', horizontalalignment='center', verticalalignment='center')
            
        # 3. Evolution Line Chart
        axs[2].set_title("Best L/D Ratio over Generations")
        
        # Invert the negative fitness values for plotting positive L/D
        ld_ratios = [-f for f in best_drags]
        
        axs[2].plot(range(len(ld_ratios)), ld_ratios, marker='o', color='red')
        axs[2].set_xlabel("Generation")
        axs[2].set_ylabel("Lift / Drag")
        axs[2].set_xticks(range(len(ld_ratios)))
        axs[2].grid(True, linestyle='--', alpha=0.6)
        
        plt.tight_layout()
        save_path = os.path.join(self.results_dir, f"generation_{gen}.png")
        try:
            plt.savefig(save_path, dpi=200)
        except OSError:
            import time
            save_path = os.path.join(self.results_dir, f"generation_{gen}_{int(time.time())}.png")
            plt.savefig(save_path, dpi=200)
        plt.close()
        
        print(f"Rendered visualization to {save_path}")


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
        axs[0].set_title(f"Best Shape Outline (Gen {gen})")
        axs[0].set_xlim(0, 100) 
        axs[0].set_ylim(-30, 30)
        
        # Simple rendering for top/bottom curve (Ellipse front, linear back)
        xs = np.linspace(0, best_shape.L, 200)
        ys = []
        for x in xs:
            if x <= best_shape.P:
                # Ellipse
                if best_shape.P > 0:
                    v = 1.0 - ((x - best_shape.P) / best_shape.P)**2
                    ys.append((best_shape.T / 2.0) * np.sqrt(max(0, v)))
                else:
                    ys.append(0)
            else:
                # Linear taper
                L_back = best_shape.L - best_shape.P
                if L_back > 0:
                    v = 1.0 - (x - best_shape.P) / L_back
                    ys.append((best_shape.T / 2.0) * max(0, v))
                else:
                    ys.append(0)
                
        axs[0].plot(xs, ys, 'b-', label='Top')
        axs[0].plot(xs, [-y for y in ys], 'b-', label='Bottom')
        axs[0].fill_between(xs, ys, [-y for y in ys], color='blue', alpha=0.3)
        axs[0].set_aspect('equal', 'box')
        axs[0].grid(True, linestyle='--', alpha=0.6)
        
        # 2. Velocity Heatmap
        axs[1].set_title(f"Velocity Heatmap (Gen {gen}, Drag={best_shape.drag:.6f})")
        if best_shape.heatmap and os.path.exists(best_shape.heatmap):
            data = np.loadtxt(best_shape.heatmap, delimiter=',')
            im = axs[1].imshow(data, cmap='jet', origin='upper')
            plt.colorbar(im, ax=axs[1], label="Velocity")
            axs[1].set_xlabel("X (Grid cells)")
            axs[1].set_ylabel("Y (Grid cells)")
        else:
            axs[1].text(0.5, 0.5, 'Heatmap data not found', horizontalalignment='center', verticalalignment='center')
            
        # 3. Evolution Line Chart
        axs[2].set_title("Best Drag over Generations")
        axs[2].plot(range(len(best_drags)), best_drags, marker='o', color='red')
        axs[2].set_xlabel("Generation")
        axs[2].set_ylabel("Drag Coefficient")
        axs[2].set_xticks(range(len(best_drags)))
        axs[2].grid(True, linestyle='--', alpha=0.6)
        
        plt.tight_layout()
        save_path = os.path.join(self.results_dir, f"gen_{gen}.png")
        plt.savefig(save_path, dpi=200)
        plt.close()
        
        print(f"Rendered visualization to {save_path}")

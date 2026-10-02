import os
import random
import subprocess
import concurrent.futures
import argparse
from visualizer import Visualizer

# Config
WORKER_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'worker_cpp', 'worker.exe'))

class Shape:
    def __init__(self, L, t_pts):
        self.L = L # Length
        self.t_pts = t_pts # List of 5 thickness points
        self.drag = float('inf')
        self.heatmap = None

    def mutate(self):
        self.L += random.gauss(0, 5)
        self.L = max(20.0, min(self.L, 80.0))
        
        for i in range(len(self.t_pts)):
            self.t_pts[i] += random.gauss(0, 3)
            # Clamp values to avoid crashing simulation and keep it reasonable
            self.t_pts[i] = max(0.0, min(self.t_pts[i], 30.0))

def create_random_shape():
    L = random.uniform(40, 70)
    # 5 random thickness points. It will start looking like a blob!
    t_pts = [random.uniform(5, 25) for _ in range(5)]
    return Shape(L, t_pts)

def run_worker(shape, shape_id, gen):
    # Unique directory for this run to avoid u_mag.csv conflicts
    run_dir = os.path.join(os.path.dirname(__file__), f"run_g{gen}_s{shape_id}")
    os.makedirs(run_dir, exist_ok=True)
    
    # Pass L and the 5 thickness points to C++
    cmd = [WORKER_PATH, str(shape.L)] + [str(t) for t in shape.t_pts]
    
    try:
        # Run C++ worker subprocess
        result = subprocess.run(cmd, cwd=run_dir, stdout=subprocess.PIPE, text=True, check=True)
        # Parse drag from last line of stdout
        drag = float(result.stdout.strip().split('\n')[-1])
        return drag, os.path.join(run_dir, "u_mag.csv")
    except Exception as e:
        print(f"Error running worker in {run_dir}: {e}")
        return float('inf'), None

def main():
    parser = argparse.ArgumentParser(description="DeCFD Genetic Algorithm Orchestrator")
    parser.add_argument('--pop', type=int, default=4, help='Population size per generation')
    parser.add_argument('--gen', type=int, default=3, help='Number of generations to run')
    args = parser.parse_args()

    POPULATION_SIZE = args.pop
    GENERATIONS = args.gen

    if not os.path.exists(WORKER_PATH):
        print(f"Worker not found at {WORKER_PATH}")
        print("Please compile the C++ worker first.")
        return

    population = [create_random_shape() for _ in range(POPULATION_SIZE)]
    vis = Visualizer()
    
    best_drags = []

    for gen in range(GENERATIONS):
        print(f"\n--- Generation {gen} ---")
        
        # Evaluate fitness in parallel using ThreadPool
        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = {executor.submit(run_worker, shape, i, gen): shape for i, shape in enumerate(population)}
            for future in concurrent.futures.as_completed(futures):
                shape = futures[future]
                drag, csv_path = future.result()
                shape.drag = drag
                shape.heatmap = csv_path
                
        # Sort by fitness (lower drag is better)
        population.sort(key=lambda s: s.drag)
        
        best_shape = population[0]
        best_drags.append(best_shape.drag)
        t_str = ", ".join([f"{t:.1f}" for t in best_shape.t_pts])
        print(f"Best Drag: {best_shape.drag:.6f} (L={best_shape.L:.1f}, Thick=[{t_str}])")
        
        # Visualize best shape of the generation
        vis.render_generation(gen, best_shape, best_drags)

        # Evolution (Tournament + Crossover)
        next_population = [best_shape] # Elitism (keep the best)
        
        while len(next_population) < POPULATION_SIZE:
            # Tournament selection (size=2)
            parent1 = min(random.sample(population, 2), key=lambda s: s.drag)
            parent2 = min(random.sample(population, 2), key=lambda s: s.drag)
            
            # Crossover (average)
            child_t_pts = [(t1 + t2) / 2.0 for t1, t2 in zip(parent1.t_pts, parent2.t_pts)]
            child = Shape(
                (parent1.L + parent2.L) / 2.0,
                child_t_pts
            )
            
            # Mutation
            child.mutate()
            next_population.append(child)
            
        population = next_population
        
    print("\nEvolution complete! Check the 'orchestrator_py/results' folder for visualizations.")

if __name__ == "__main__":
    main()


import os
import random
import subprocess
import concurrent.futures
from visualizer import Visualizer

# Config
POPULATION_SIZE = 4
GENERATIONS = 3
WORKER_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'worker_cpp', 'worker.exe'))

class Shape:
    def __init__(self, L, T, P):
        self.L = L # Length
        self.T = T # Thickness
        self.P = P # Position of max thickness
        self.drag = float('inf')
        self.heatmap = None

    def mutate(self):
        self.L += random.gauss(0, 5)
        self.T += random.gauss(0, 2)
        self.P += random.gauss(0, 2)
        
        # Clamp values to avoid simulation crashes (e.g. going outside grid)
        self.L = max(20.0, min(self.L, 80.0))
        self.T = max(5.0, min(self.T, 30.0))
        self.P = max(5.0, min(self.P, self.L - 5.0))

def create_random_shape():
    L = random.uniform(40, 70)
    T = random.uniform(10, 25)
    P = random.uniform(10, L - 10)
    return Shape(L, T, P)

def run_worker(shape, shape_id, gen):
    # Unique directory for this run to avoid u_mag.csv conflicts
    run_dir = os.path.join(os.path.dirname(__file__), f"run_g{gen}_s{shape_id}")
    os.makedirs(run_dir, exist_ok=True)
    
    cmd = [WORKER_PATH, str(shape.L), str(shape.T), str(shape.P)]
    
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
        print(f"Best Drag: {best_shape.drag:.6f} (L={best_shape.L:.1f}, T={best_shape.T:.1f}, P={best_shape.P:.1f})")
        
        # Visualize best shape of the generation
        vis.render_generation(gen, best_shape, best_drags)

        # Evolution (Tournament + Crossover)
        next_population = [best_shape] # Elitism (keep the best)
        
        while len(next_population) < POPULATION_SIZE:
            # Tournament selection (size=2)
            parent1 = min(random.sample(population, 2), key=lambda s: s.drag)
            parent2 = min(random.sample(population, 2), key=lambda s: s.drag)
            
            # Crossover (average)
            child = Shape(
                (parent1.L + parent2.L) / 2,
                (parent1.T + parent2.T) / 2,
                (parent1.P + parent2.P) / 2
            )
            
            # Mutation
            child.mutate()
            next_population.append(child)
            
        population = next_population
        
    print("\nEvolution complete! Check the 'orchestrator_py/results' folder for visualizations.")

if __name__ == "__main__":
    main()

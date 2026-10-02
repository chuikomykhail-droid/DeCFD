import os
import random
import subprocess
import concurrent.futures
import argparse
from visualizer import Visualizer

# Config
WORKER_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'worker_cpp', 'worker.exe'))

import json

class Shape:
    def __init__(self, L, t_pts, alpha=0.0, camber=0.0):
        self.L = L # Length
        self.t_pts = t_pts # List of 5 thickness points
        self.alpha = alpha # Angle of attack
        self.camber = camber # Camber (curvature)
        self.drag = float('inf')
        self.lift = 0.0
        self.fitness = float('inf')
        self.heatmap = None

    def mutate(self):
        self.L += random.gauss(0, 5)
        self.L = max(20.0, min(self.L, 80.0))
        
        # Only mutate the middle 3 points. Nose (0) and Tail (4) stay 0.
        for i in range(1, len(self.t_pts) - 1):
            self.t_pts[i] += random.gauss(0, 3)
            # Clamp values to avoid crashing simulation and keep it reasonable
            self.t_pts[i] = max(0.0, min(self.t_pts[i], 30.0))
            
        # Mutate angle of attack (-5 to 20 degrees)
        self.alpha += random.gauss(0, 2.0)
        self.alpha = max(-5.0, min(self.alpha, 20.0))
        
        # Mutate camber (0 to 10 lattice cells)
        self.camber += random.gauss(0, 1.0)
        self.camber = max(0.0, min(self.camber, 10.0))

def create_random_shape():
    L = random.uniform(40, 70)
    # Nose=0, Tail=0, 3 random middle points
    t_pts = [0.0] + [random.uniform(5, 25) for _ in range(3)] + [0.0]
    alpha = random.uniform(0, 10.0)
    camber = random.uniform(0, 5.0)
    return Shape(L, t_pts, alpha, camber)

def run_worker(shape, shape_id, gen):
    # Unique directory for this run to avoid u_mag.csv conflicts
    run_dir = os.path.join(os.path.dirname(__file__), f"run_g{gen}_s{shape_id}")
    os.makedirs(run_dir, exist_ok=True)
    
    # Pass L, 5 thickness points, alpha, camber, and request csv
    cmd = [WORKER_PATH, str(shape.L)] + [str(t) for t in shape.t_pts] + [str(shape.alpha), str(shape.camber), "--csv", "u_mag.csv"]
    
    try:
        # Run C++ worker subprocess
        result = subprocess.run(cmd, cwd=run_dir, stdout=subprocess.PIPE, text=True, check=True)
        # Parse JSON output from stdout
        output = json.loads(result.stdout.strip())
        drag = output.get("cd", float('inf'))
        lift = output.get("cl", 0.0)
        return drag, lift, os.path.join(run_dir, "u_mag.csv")
    except Exception as e:
        print(f"Error running worker in {run_dir}: {e}")
        return float('inf'), 0.0, None

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
                drag, lift, csv_path = future.result()
                shape.drag = drag
                shape.lift = lift
                shape.heatmap = csv_path
                
        # Calculate fitness
        MIN_AREA = 250.0
        for s in population:
            # Approximate area
            area = s.L * sum(s.t_pts) / len(s.t_pts)
            
            # We want to maximize Lift / Drag, which is equivalent to minimizing -(Lift / Drag).
            # If a shape generates negative lift or zero drag, it's terrible.
            if s.lift <= 0.001 or s.drag <= 0.001:
                s.fitness = 1000.0 # huge penalty
            else:
                s.fitness = -(s.lift / s.drag)
                
            # Add penalty for being too thin
            if area < MIN_AREA:
                s.fitness += (MIN_AREA - area) * 0.5
                
        # Sort by fitness (lower is better, since we use negative L/D)
        population.sort(key=lambda s: s.fitness)
        
        best_shape = population[0]
        best_drags.append(best_shape.fitness)
        t_str = ", ".join([f"{t:.1f}" for t in best_shape.t_pts])
        print(f"Best: L/D={(-best_shape.fitness):.3f} (Drag={best_shape.drag:.5f}, Lift={best_shape.lift:.5f}) | L={best_shape.L:.1f}, α={best_shape.alpha:.1f}°, c={best_shape.camber:.1f}")
        
        # Visualize best shape of the generation
        vis.render_generation(gen, best_shape, best_drags)

        # Evolution (Tournament + Crossover)
        next_population = [best_shape] # Elitism (keep the best)
        
        while len(next_population) < POPULATION_SIZE:
            # Tournament selection (size=2)
            parent1 = min(random.sample(population, 2), key=lambda s: s.fitness)
            parent2 = min(random.sample(population, 2), key=lambda s: s.fitness)
            
            # Crossover (average)
            child_t_pts = [(t1 + t2) / 2.0 for t1, t2 in zip(parent1.t_pts, parent2.t_pts)]
            child = Shape(
                (parent1.L + parent2.L) / 2.0,
                child_t_pts,
                (parent1.alpha + parent2.alpha) / 2.0,
                (parent1.camber + parent2.camber) / 2.0
            )
            
            # Mutation
            child.mutate()
            next_population.append(child)
            
        population = next_population
        
    print("\nEvolution complete! Check the 'orchestrator_py/results' folder for visualizations.")

if __name__ == "__main__":
    main()


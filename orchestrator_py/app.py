import os
import random
import subprocess
import concurrent.futures
import argparse
import json
from visualizer import Visualizer

# Config
# Get path from env var if set, otherwise fallback to local exe
WORKER_PATH = os.environ.get("WORKER_PATH", os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'worker_cpp', 'worker.exe')))

class Shape:
    def __init__(self, t_pts, alpha=0.0, camber=0.0):
        self.L = 60.0 # Fixed chord length to avoid Reynolds number hacking
        self.t_pts = [round(t, 3) for t in t_pts]
        # Restrict trailing edge thickness to at most 0.1 * L to enforce a physical tail
        self.t_pts[-1] = min(self.t_pts[-1], round(0.1 * self.L, 3))
        
        self.alpha = round(alpha, 3)
        self.camber = round(camber, 3)
        self.drag = float('inf')
        self.lift = 0.0
        self.fitness = float('inf')
        self.heatmap = None
        self.parent_best_fitness = float('inf')

    def mutate(self, scale=1.0):
        # Mutate all 5 points independently to allow the AI to discover the optimal nose/tail
        for i in range(len(self.t_pts)):
            if random.random() < 0.3:
                self.t_pts[i] += random.gauss(0, 3.0 * scale)
                self.t_pts[i] = max(0.0, min(self.t_pts[i], 30.0))
            
        if random.random() < 0.3:
            # Mutate angle of attack (-5 to 20 degrees)
            self.alpha += random.gauss(0, 2.0 * scale)
            self.alpha = max(-5.0, min(self.alpha, 20.0))
        
        if random.random() < 0.3:
            # Mutate camber (0 to 10 lattice cells)
            self.camber += random.gauss(0, 1.0 * scale)
            self.camber = max(0.0, min(self.camber, 10.0))

        # Enforce trailing edge constraint after mutation
        self.t_pts[-1] = min(self.t_pts[-1], 0.1 * self.L)

        # Round to keep reproducible
        self.t_pts = [round(t, 3) for t in self.t_pts]
        self.alpha = round(self.alpha, 3)
        self.camber = round(self.camber, 3)

def create_random_shape():
    # 5 fully random thickness points! It starts as a "flying brick"
    t_pts = [random.uniform(5, 25) for _ in range(5)]
    alpha = random.uniform(0, 10.0)
    camber = random.uniform(0, 5.0)
    return Shape(t_pts, alpha, camber)

def run_worker(shape, shape_id, gen, generate_csv=False):
    run_dir = os.path.join(os.path.dirname(__file__), f"run_g{gen}_s{shape_id}")
    os.makedirs(run_dir, exist_ok=True)
    
    # Pass exact string representations of the numbers for perfect reproducibility
    cmd = [WORKER_PATH, str(shape.L)] + [str(t) for t in shape.t_pts] + [str(shape.alpha), str(shape.camber)]
    if generate_csv:
        cmd += ["--csv", "u_mag.csv"]
    
    # We restrict OpenMP to 1 thread so ThreadPoolExecutor can scale by core count efficiently
    env = {**os.environ, "OMP_NUM_THREADS": "1"}
    
    try:
        # Run C++ worker subprocess
        result = subprocess.run(cmd, cwd=run_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True, env=env, timeout=60)
        # Parse JSON output from stdout
        output = json.loads(result.stdout.strip())
        drag = output.get("cd", float('inf'))
        lift = output.get("cl", 0.0)
        csv_path = os.path.join(run_dir, "u_mag.csv") if generate_csv else None
        return drag, lift, csv_path
    except subprocess.CalledProcessError as e:
        print(f"Error running worker in {run_dir}. Exit code: {e.returncode}")
        print(f"Stderr: {e.stderr}")
        return float('inf'), 0.0, None
    except subprocess.TimeoutExpired as e:
        print(f"Worker timeout in {run_dir}.")
        return float('inf'), 0.0, None
    except Exception as e:
        print(f"Error running worker in {run_dir}: {e}")
        return float('inf'), 0.0, None

def evaluate(shape, shape_id, gen):
    drag, lift, _ = run_worker(shape, shape_id, gen, generate_csv=False)
    shape.drag = drag
    shape.lift = lift
    return shape

def main():
    parser = argparse.ArgumentParser(description="DeCFD Genetic Algorithm Orchestrator")
    parser.add_argument('--pop', type=int, default=4, help='Population size per generation')
    parser.add_argument('--gen', type=int, default=3, help='Number of generations to run')
    args = parser.parse_args()

    POPULATION_SIZE = args.pop
    GENERATIONS = args.gen

    if not os.path.exists(WORKER_PATH) and "WORKER_PATH" not in os.environ:
        print(f"Worker not found at {WORKER_PATH}")
        print("Please compile the C++ worker first.")
        return

    # Seed for deterministic and reproducible demo
    random.seed(42)

    population = [create_random_shape() for _ in range(POPULATION_SIZE)]
    vis = Visualizer()
    
    best_drags = []
    mutation_scale = 1.0
    succ_hist = []
    best_ever_fitness = float('inf')
    gens_without_improvement = 0
    WINDOW = 4
    SCALE_MIN = 0.18
    SCALE_MAX = 2.0

    for gen in range(GENERATIONS):
        print(f"\n--- Generation {gen} ---")
        
        # Only evaluate shapes that haven't been evaluated yet
        todo = [(i, shape) for i, shape in enumerate(population) if shape.fitness == float('inf')]
        
        # Evaluate fitness in parallel using ThreadPool
        # We spawn exactly as many workers as CPU cores, and each C++ worker runs on 1 thread
        with concurrent.futures.ThreadPoolExecutor(max_workers=os.cpu_count()) as executor:
            futures = {executor.submit(evaluate, shape, i, gen): shape for i, shape in todo}
            for future in concurrent.futures.as_completed(futures):
                shape = futures[future]
                pass
                
        # Calculate fitness
        MIN_AREA = 250.0
        for s in population:
            if s.fitness != float('inf'): # Already evaluated
                continue

            # Exact integral area for cosine interpolation: L * (t0 + 2*(t1+t2+t3) + t4) / 8
            area = s.L * (s.t_pts[0] + 2*sum(s.t_pts[1:4]) + s.t_pts[4]) / 8.0
            
            # We want to maximize Lift / Drag, which is equivalent to minimizing -(Lift / Drag).
            if s.drag <= 0.001 or s.drag == float('inf'):
                s.fitness = float('inf') # invalid / diverged
            else:
                s.fitness = -(s.lift / s.drag)
                
            # Soft penalty for volume, doesn't completely overwhelm the fitness anymore
            s.fitness += 5.0 * max(0.0, 1.0 - area / MIN_AREA)
            
        # 1/5th success rule (Rechenberg) with Windowing
        if gen > 0 and todo:
            evaluated_children = [s for _, s in todo]
            # Since fitness is negative L/D, lower is better
            success_count = sum(1 for c in evaluated_children if c.fitness < c.parent_best_fitness)
            success_rate = success_count / len(evaluated_children)
            succ_hist.append(success_rate)
            
            if len(succ_hist) % WINDOW == 0:
                p = sum(succ_hist[-WINDOW:]) / WINDOW
                if p > 0.2:
                    mutation_scale *= 1.15
                else:
                    mutation_scale *= 0.87
                mutation_scale = max(SCALE_MIN, min(mutation_scale, SCALE_MAX))
                print(f"1/5th Rule Window: Avg Success {p*100:.0f}%, new mutation scale = {mutation_scale:.2f}")
                
        # Sort by fitness (lower is better, since we use negative L/D)
        population.sort(key=lambda s: s.fitness)
        
        best_shape = population[0]
        
        # Track stagnation
        if best_shape.fitness < best_ever_fitness - 1e-4:
            best_ever_fitness = best_shape.fitness
            gens_without_improvement = 0
        else:
            gens_without_improvement += 1
        
        # Re-run best shape to get CSV for heatmap!
        _, _, csv_path = run_worker(best_shape, "best", gen, generate_csv=True)
        best_shape.heatmap = csv_path
        
        best_drags.append(best_shape.fitness)
        t_str = ", ".join([f"{t:.1f}" for t in best_shape.t_pts])
        print(f"Best: L/D={(-best_shape.fitness):.3f} (Drag={best_shape.drag:.5f}, Lift={best_shape.lift:.5f}) | L={best_shape.L:.1f}, alpha={best_shape.alpha:.1f}, c={best_shape.camber:.1f}")
        
        # Visualize best shape of the generation
        vis.render_generation(gen, best_shape, best_drags)

        # Evolution (Tournament + Crossover)
        next_population = [best_shape] # Elitism (keep the best)
        
        # Stagnation shake-up
        if gens_without_improvement >= 8:
            print("Stagnation detected! Applying shake-up (scale=1.5, adding immigrants).")
            mutation_scale = 1.5
            gens_without_improvement = 0
            
            # Add 15% random immigrants to escape local minima
            num_immigrants = max(1, int(POPULATION_SIZE * 0.15))
            for _ in range(num_immigrants):
                if len(next_population) < POPULATION_SIZE:
                    next_population.append(create_random_shape())
        
        while len(next_population) < POPULATION_SIZE:
            # Tournament selection (size=2)
            parent1 = min(random.sample(population, 2), key=lambda s: s.fitness)
            parent2 = min(random.sample(population, 2), key=lambda s: s.fitness)
            
            # Blend Crossover (Random mix of genes)
            child_t_pts = []
            for t1, t2 in zip(parent1.t_pts, parent2.t_pts):
                w = random.random()
                child_t_pts.append(w * t1 + (1 - w) * t2)
            
            w_a = random.random()
            w_c = random.random()
            
            child = Shape(
                child_t_pts,
                w_a * parent1.alpha + (1 - w_a) * parent2.alpha,
                w_c * parent1.camber + (1 - w_c) * parent2.camber
            )
            child.parent_best_fitness = min(parent1.fitness, parent2.fitness)
            
            # Mutation
            child.mutate(scale=mutation_scale)
            next_population.append(child)
            
        population = next_population
        
    print("\nEvolution complete! Check the 'orchestrator_py/results' folder for visualizations.")
    
    print("\n--- Running High-Fidelity Verification on Best Shape ---")
    best_overall = min(population, key=lambda s: s.fitness)
    print(f"Candidate: L={best_overall.L:.1f}, Thick={best_overall.t_pts}, alpha={best_overall.alpha:.1f}, c={best_overall.camber:.1f}")
    
    run_dir = os.path.join(os.path.dirname(__file__), "run_verification")
    os.makedirs(run_dir, exist_ok=True)
    cmd = [
        WORKER_PATH, str(best_overall.L)
    ] + [str(t) for t in best_overall.t_pts] + [
        str(best_overall.alpha), str(best_overall.camber), 
        "--steps", "30000", "--avg", "20000", "--csv", "u_mag_verified.csv"
    ]
    
    env = {**os.environ, "OMP_NUM_THREADS": "1"}
    try:
        print("Running long simulation (30,000 steps)... this may take a minute.")
        result = subprocess.run(cmd, cwd=run_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True, env=env)
        output = json.loads(result.stdout.strip())
        v_drag = output.get("cd", float('inf'))
        v_lift = output.get("cl", 0.0)
        v_ld = v_lift / v_drag if v_drag > 0.001 else 0
        print(f"Verification Result:")
        print(f"  Short Run (6k steps) L/D: {-best_overall.fitness:.3f}")
        print(f"  Long Run (30k steps) L/D: {v_ld:.3f}")
        print(f"  Drag: {v_drag:.5f}, Lift: {v_lift:.5f}")
    except Exception as e:
        print("Verification failed.")

if __name__ == "__main__":
    main()


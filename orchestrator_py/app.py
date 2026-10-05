import argparse
import math
import os
import random
import time

from network import ComputeNetwork
from runs import new_run_dir, write_json
from report import build_report
from visualizer import Visualizer, csv_to_npz
from worker_runner import WORKER_PATH, run_worker, shape_args

MIN_AREA = 250.0


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

        # Network bookkeeping (filled in by ComputeNetwork)
        self.evaluated = False
        self.verified = False
        self.task_id = None
        self.miner = None
        self.args = None
        self.result_hash = None
        self.corrected = False  # True if the verifier replaced a fraudulent result

    @property
    def ld(self):
        """Plain Lift/Drag (fitness additionally carries the area penalty)."""
        return self.lift / self.drag if math.isfinite(self.drag) and self.drag > 0.001 else 0.0

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

def compute_fitness(s):
    # Exact integral area for cosine interpolation: L * (t0 + 2*(t1+t2+t3) + t4) / 8
    area = s.L * (s.t_pts[0] + 2 * sum(s.t_pts[1:4]) + s.t_pts[4]) / 8.0

    # We want to maximize Lift / Drag, which is equivalent to minimizing -(Lift / Drag).
    if not math.isfinite(s.drag) or s.drag <= 0.001:
        s.fitness = float('inf') # invalid / diverged
        return
    s.fitness = -(s.lift / s.drag)
    # Soft penalty for volume, doesn't completely overwhelm the fitness anymore
    s.fitness += 5.0 * max(0.0, 1.0 - area / MIN_AREA)

def tournament(population):
    return min(random.sample(population, 2), key=lambda s: s.fitness)

def pick_parents(population, n_pairs, net, audit=False):
    """Tournament-select parent pairs. With audit on, every unverified winner is re-computed
    by the verifier before it may reproduce, so a faked score can't steer the search.
    If an audit catches fraud the fitness landscape changed, so selection is redone."""
    while True:
        pairs = [(tournament(population), tournament(population)) for _ in range(n_pairs)]
        if not audit:
            return pairs
        chosen = {p for pair in pairs for p in pair}
        todo = [s for s in population if s in chosen and not s.verified]
        if not todo or net.verify_many(todo, reason="parent check") == 0:
            return pairs
        for s in population:
            compute_fitness(s)

def _num(x):
    return x if math.isfinite(x) else None  # JSON has no inf/nan

def shape_record(s):
    return {
        "L": s.L, "t_pts": s.t_pts, "alpha": s.alpha, "camber": s.camber,
        "cd": _num(s.drag), "cl": _num(s.lift), "ld": s.ld, "fitness": _num(s.fitness),
        "task_id": s.task_id, "miner": s.miner.name if s.miner else None,
        "verified": s.verified, "corrected": s.corrected,
    }

def main():
    parser = argparse.ArgumentParser(description="DeCFD Genetic Algorithm Orchestrator")
    parser.add_argument('--pop', type=int, default=4, help='Population size per generation')
    parser.add_argument('--gen', type=int, default=3, help='Number of generations to run')
    parser.add_argument('--miners', type=int, default=4, help='Number of mock miner nodes')
    parser.add_argument('--cheaters', type=int, default=1, help='How many of the miners are lazy (fake results)')
    parser.add_argument('--cheat-prob', type=float, default=0.5, help='Probability a lazy miner fakes a given task')
    parser.add_argument('--verify-rate', type=float, default=0.2, help='Fraction of tasks randomly audited')
    parser.add_argument('--parent-audit', action='store_true',
                        help='Also audit every unverified tournament winner before it reproduces (~2.5x more audits)')
    parser.add_argument('--steps', type=int, default=6000, help='LBM steps per evaluation (job-wide solver setting)')
    parser.add_argument('--avg', type=int, default=2000, help='Steps averaged for the forces (job-wide solver setting)')
    parser.add_argument('--ledger', choices=['mock'], default='mock', help='Chain backend (a Solana devnet backend plugs in here)')
    parser.add_argument('--seed', type=int, default=42, help='GA random seed')
    parser.add_argument('--quiet-net', action='store_true', help='Hide per-task network log lines (fraud events are still shown)')
    args = parser.parse_args()

    POPULATION_SIZE = args.pop
    GENERATIONS = args.gen

    if not os.path.exists(WORKER_PATH):
        print(f"Worker not found at {WORKER_PATH}")
        print("Please compile the C++ worker first (see README: build.ps1 or CMake).")
        return

    # Seed for deterministic and reproducible demo
    random.seed(args.seed)

    run_dir = new_run_dir(args.seed)
    history_path = os.path.join(run_dir, "history.json")
    history = {"run": os.path.basename(run_dir), "config": dict(vars(args)), "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "finished": False, "generations": [], "verification": None}
    t_start = time.time()
    print(f"Run folder: {run_dir}")

    net = ComputeNetwork(n_miners=args.miners, n_cheaters=args.cheaters, cheat_prob=args.cheat_prob,
                         verify_rate=args.verify_rate, sim_args=["--steps", str(args.steps), "--avg", str(args.avg)],
                         ledger=args.ledger, log_dir=os.path.join(run_dir, "network"), verbose=not args.quiet_net)
    # Budget for the worst case: every shape of every generation is a new task
    net.open_job(budget=POPULATION_SIZE * GENERATIONS * net.reward)
    history["config"].update(binary_hash=net.binary_hash, cluster=net.ledger.cluster,
                             program_id=net.ledger.program_id, job_id=net.job_id)
    population = [create_random_shape() for _ in range(POPULATION_SIZE)]
    vis = Visualizer(os.path.join(run_dir, "frames"))

    mutation_scale = 1.0
    succ_hist = []
    best_ever_fitness = float('inf')
    gens_without_improvement = 0
    WINDOW = 4
    SCALE_MIN = 0.18
    SCALE_MAX = 2.0

    for gen in range(GENERATIONS):
        print(f"\n--- Generation {gen} ---")
        last_gen = gen == GENERATIONS - 1

        # Only evaluate shapes that haven't been evaluated yet (elite is carried over as-is,
        # including a diverged one, so it is never recomputed)
        todo = [s for s in population if not s.evaluated]
        net.evaluate_batch(todo, gen)

        for s in population:
            compute_fitness(s)
        population.sort(key=lambda s: s.fitness)

        # Never accept an unverified leader: a faked L/D would otherwise become the elite forever
        while not population[0].verified:
            net.verify_many([population[0]], reason="leader check")
            for s in population:
                compute_fitness(s)
            population.sort(key=lambda s: s.fitness)

        # 1/5th success rule (Rechenberg) with Windowing.
        # Only real children count: random immigrants have no parents (parent_best_fitness = inf)
        children = [s for s in todo if math.isfinite(s.parent_best_fitness)]
        if children:
            # Since fitness is negative L/D, lower is better
            success_count = sum(1 for c in children if c.fitness < c.parent_best_fitness)
            success_rate = success_count / len(children)
            succ_hist.append(success_rate)

            if len(succ_hist) % WINDOW == 0:
                p = sum(succ_hist[-WINDOW:]) / WINDOW
                if p > 0.2:
                    mutation_scale *= 1.15
                else:
                    mutation_scale *= 0.87
                mutation_scale = max(SCALE_MIN, min(mutation_scale, SCALE_MAX))
                print(f"1/5th Rule Window: Avg Success {p*100:.0f}%, new mutation scale = {mutation_scale:.2f}")

        best_shape = population[0]

        # Track stagnation
        if best_shape.fitness < best_ever_fitness - 1e-4:
            best_ever_fitness = best_shape.fitness
            gens_without_improvement = 0
        else:
            gens_without_improvement += 1

        # Plan the next generation before closing the epoch: parent audits must land
        # while this epoch's tasks are still unpaid, so a caught miner forfeits the reward
        if not last_gen:
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

            # Tournament selection (size=2)
            pairs = pick_parents(population, POPULATION_SIZE - len(next_population), net,
                                 audit=args.parent_audit)

        paid = net.finalize_epoch(gen)

        # Re-run the (verified) best shape locally to keep its flow field
        fields_dir = os.path.join(run_dir, "fields")
        tmp = os.path.join(fields_dir, "tmp")
        res = run_worker(net.task_args(best_shape), run_dir=tmp, extra_args=["--ux", "ux.csv", "--uy", "uy.csv"],
                         threads=None)
        field = None
        if res.get("status") == "ok":
            field = csv_to_npz(os.path.join(tmp, "ux.csv"), os.path.join(tmp, "uy.csv"),
                               os.path.join(fields_dir, f"best_g{gen:03d}.npz"))

        if best_shape.corrected:
            source = f"verifier (fraud by {best_shape.miner.name} corrected)"
        else:
            source = f"{best_shape.miner.name if best_shape.miner else '-'} [verified]"
        print(f"Best: L/D={best_shape.ld:.3f} (Drag={best_shape.drag:.5f}, Lift={best_shape.lift:.5f}) | "
              f"L={best_shape.L:.1f}, alpha={best_shape.alpha:.1f}, c={best_shape.camber:.1f} | "
              f"by {source} | epoch paid {paid}")

        # One frame per generation: the best shape's flow field
        frame = vis.render_field(gen, best_shape.ld, field)

        history["generations"].append({
            "gen": gen, "best": shape_record(best_shape), "mutation_scale": mutation_scale,
            "epoch_paid": paid, "population_ld": [round(s.ld, 4) for s in population],
            "elapsed": round(time.time() - t_start, 1),
            "frame": os.path.relpath(frame, run_dir).replace(os.sep, "/"),
            "field": os.path.relpath(field, run_dir).replace(os.sep, "/") if field else None,
        })
        write_json(history_path, history)

        if last_gen:
            break

        # Evolution (Crossover + Mutation)
        for parent1, parent2 in pairs:
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

    print(f"\nEvolution complete! Results: {run_dir}")
    net.close_job()
    net.report()

    print("\n--- Running High-Fidelity Verification on Best Shape ---")
    best_overall = population[0]
    print(f"Candidate: L={best_overall.L:.1f}, Thick={best_overall.t_pts}, alpha={best_overall.alpha:.1f}, c={best_overall.camber:.1f}")
    print("Running long simulation (30,000 steps, all cores)... this may take a minute.")
    res = run_worker(shape_args(best_overall), run_dir=os.path.join(run_dir, "verification"),
                     extra_args=["--steps", "30000", "--avg", "20000"], timeout=None, threads=None)
    if res.get("status") != "ok":
        print(f"Verification failed: {res}")
    else:
        v_drag, v_lift = res["cd"], res["cl"]
        v_ld = v_lift / v_drag if v_drag > 0.001 else 0
        history["verification"] = {"steps": 30000, "avg": 20000, "cd": v_drag, "cl": v_lift, "ld": v_ld,
                                   "short_ld": best_overall.ld}
        print("Verification Result:")
        print(f"  Short Run ({args.steps} steps) L/D: {best_overall.ld:.3f}")
        print(f"  Long Run (30k steps) L/D: {v_ld:.3f}")
        print(f"  Drag: {v_drag:.5f}, Lift: {v_lift:.5f}")
    history["finished"] = True
    history["elapsed"] = round(time.time() - t_start, 1)
    write_json(history_path, history)

    print("\n--- Building the report ---")
    build_report(run_dir)

if __name__ == "__main__":
    main()

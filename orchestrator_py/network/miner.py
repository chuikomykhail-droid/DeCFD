"""Mock miner nodes. An honest miner runs the worker; a 'lazy' miner sometimes skips
the simulation and returns plausible-looking random numbers to collect the reward
without spending compute -- the main economic attack a compute network must handle."""
import random
import time

from worker_runner import run_worker


class Miner:
    def __init__(self, name, pubkey, honest=True, cheat_prob=0.0):
        self.name = name
        self.pubkey = pubkey
        self.honest = honest
        self.cheat_prob = cheat_prob
        self.faked_tasks = set()  # ground truth, used only for the end-of-run report

    def compute(self, task_id, args):
        if not self.honest:
            # Seeded by task + miner so the run is reproducible
            r = random.Random(f"{task_id}:{self.pubkey}")
            if r.random() < self.cheat_prob:
                self.faked_tasks.add(task_id)
                time.sleep(0.05)
                cd = r.uniform(0.25, 0.6)
                cl = r.uniform(0.5, 2.0)
                return {"status": "ok", "fx": cd * 0.25, "fy": cl * 0.25, "cd": cd, "cl": cl}
        return run_worker(args)


"""ComputeNetwork: the orchestrator's only entry point to "the network".

Flow:
  open_job       the client escrows the budget for the whole optimization run
  per generation (epoch):
    1. create_task for every new shape (reserves one reward from the job)
    2. tasks are dispatched round-robin to active miners, computed in parallel
    3. miners submit a result hash (commitment) + the values
    4. optimistic verification: a random sample (verify_rate) is re-computed by the verifier;
       the GA additionally forces verification of the generation leader before accepting it
    5. a mismatch slashes the miner's stake (part of it rewards the verifier); all of that
       miner's other unverified work in the current epoch is re-audited
    6. finalize_epoch settles every task of the epoch (pays the survivors)
  close_job      the unspent budget returns to the client

All chain access goes through a `Ledger` (network/ledger.py), so swapping the mock for a
Solana backend does not touch this class or the GA.
"""
import json
import os
import random
from concurrent.futures import ThreadPoolExecutor, as_completed

from worker_runner import WORKER_PATH, file_hash, result_hash, run_worker, sha256_hex, shape_args

from .ledger import create_ledger
from .miner import Miner
from .wallet import MockWallet, short


def _threads_per_task(n_tasks):
    # Worker results are bit-identical for any thread count, so split the cores between
    # concurrent runs: a lone audit then uses the whole machine instead of a single core
    return max(1, (os.cpu_count() or 1) // n_tasks)


def _apply_result(shape, res):
    if res.get("status") == "ok":
        shape.drag = res["cd"]
        shape.lift = res["cl"]
    else:
        shape.drag = float("inf")
        shape.lift = 0.0


def _ld(shape):
    return shape.lift / shape.drag if shape.drag not in (0, float("inf")) else 0.0


class ComputeNetwork:
    def __init__(self, n_miners=4, n_cheaters=1, verify_rate=0.2, cheat_prob=0.5, seed=7,
                 reward=10, stake=100, min_stake=50, slash_frac=0.5, verifier_share=0.5,
                 client_funds=100_000, sim_args=(), ledger="mock", log_dir=None, verbose=True):
        if n_cheaters >= n_miners:
            raise ValueError("Need at least one honest miner")
        self.rng = random.Random(seed)  # separate RNG: network randomness must not perturb the GA
        self.verify_rate = verify_rate
        self.reward = reward
        self.sim_args = list(sim_args)  # solver settings shared by every task of the job
        self.verbose = verbose

        self.log_dir = log_dir or os.path.join(os.path.dirname(os.path.dirname(__file__)), "network_logs")
        os.makedirs(self.log_dir, exist_ok=True)
        self.ledger = create_ledger(ledger, log_dir=self.log_dir)

        # Every job pins the exact binary: bit-exact verification only holds for identical builds
        self.binary_hash = file_hash(WORKER_PATH)
        self.client = MockWallet(self.rng, "client")
        self.verifier = MockWallet(self.rng, "verifier")
        self.ledger.initialize(self.client, self.verifier.pubkey, min_stake,
                               int(slash_frac * 10_000), int(verifier_share * 10_000))
        self.ledger.airdrop(self.client.pubkey, client_funds)

        self.miners = []
        n_honest = n_miners - n_cheaters
        for i in range(n_miners):
            honest = i < n_honest
            m = Miner(f"miner-{i + 1}", MockWallet(self.rng, f"miner-{i + 1}"), honest=honest,
                      cheat_prob=0.0 if honest else cheat_prob)
            self.miners.append(m)
            self.ledger.airdrop(m.pubkey, stake)
            self.ledger.register_miner(m.wallet, m.name, stake)

        # Who is who, for the dashboard (the chain only knows public keys)
        participants = {self.client.pubkey: {"role": "client", "name": "client"},
                        self.verifier.pubkey: {"role": "verifier", "name": "verifier"}}
        for m in self.miners:
            participants[m.pubkey] = {"role": "miner", "name": m.name, "honest": m.honest}
        with open(os.path.join(self.log_dir, "participants.json"), "w", encoding="utf-8") as f:
            json.dump({"cluster": self.ledger.cluster, "program_id": self.ledger.program_id,
                       "participants": participants}, f, indent=2)

        self.job_id = None
        self.refund = None
        self._rr = 0
        self._seq = 0
        self._pending = []  # shapes computed in the current (not yet settled) epoch
        self.stats = {"tasks": 0, "audits": 0, "caught": 0}

    # ------------------------------------------------------------------ helpers
    def _log(self, msg):
        if self.verbose:
            print(msg)

    def active_miners(self):
        return [m for m in self.miners if self.ledger.miner_account(m.pubkey)["status"] == "active"]

    def task_args(self, shape):
        """The exact worker command line of a task: shape parameters + the job's solver settings."""
        return shape_args(shape) + self.sim_args

    # --------------------------------------------------------------- public API
    def open_job(self, budget):
        self.job_id = "job-1"
        self.ledger.create_job(self.client, self.job_id, budget, self.reward, self.binary_hash)

    def evaluate_batch(self, shapes, gen):
        """Compute drag/lift for every shape through the network. Mutates the shapes."""
        if not shapes:
            return
        for s in shapes:
            active = self.active_miners()
            if not active:
                raise RuntimeError("All miners are banned, the network cannot process tasks")
            s.args = self.task_args(s)
            s.task_id = f"g{gen}-t{self._seq:04d}"
            self._seq += 1
            self.ledger.create_task(self.client, self.job_id, s.task_id,
                                    sha256_hex(" ".join(s.args).encode()), gen)
            s.miner = active[self._rr % len(active)]
            self._rr += 1

        threads = _threads_per_task(len(shapes))
        with ThreadPoolExecutor(max_workers=os.cpu_count()) as ex:
            futures = {ex.submit(s.miner.compute, s.task_id, s.args, threads): s for s in shapes}
            for f in as_completed(futures):
                s = futures[f]
                res = f.result()
                s.result_hash = result_hash(s.task_id, res)
                self.ledger.submit_result(s.miner.wallet, s.task_id, s.result_hash)
                _apply_result(s, res)
                s.evaluated = True
                s.verified = False
                self._log(f"  [slot {self.ledger.current_slot():05d}] {s.task_id} <- {s.miner.name} "
                          f"({short(s.miner.pubkey)})  L/D={_ld(s):6.3f}  hash={s.result_hash[:10]}")

        self.stats["tasks"] += len(shapes)
        self._pending.extend(shapes)

        # Optimistic verification: random audit (sampled in a fixed order -> reproducible)
        sample = [s for s in shapes if self.rng.random() < self.verify_rate]
        if sample:
            self.verify_many(sample, reason="random audit")

    def verify_many(self, shapes, reason="audit"):
        """Re-compute shapes on the verifier and resolve each challenge on the ledger.
        Returns the number of fraudulent results caught. Corrects caught shapes in place."""
        caught_total = 0
        todo = [s for s in shapes if not s.verified]
        while todo:
            threads = _threads_per_task(len(todo))
            with ThreadPoolExecutor(max_workers=os.cpu_count()) as ex:
                refs = list(ex.map(lambda s: run_worker(s.args, threads=threads), todo))

            newly_caught = set()
            for s, ref in zip(todo, refs):
                ref_hash = result_hash(s.task_id, ref)
                ok = self.ledger.resolve_challenge(self.verifier, s.task_id, ref_hash)
                s.verified = True
                self.stats["audits"] += 1
                if ok:
                    self._log(f"  [verify] {s.task_id} ({s.miner.name}) OK  [{reason}]")
                    continue
                caught_total += 1
                self.stats["caught"] += 1
                acc = self.ledger.miner_account(s.miner.pubkey)
                fake_ld = _ld(s)
                _apply_result(s, ref)
                s.result_hash = ref_hash
                s.corrected = True
                # Always shown, even with --quiet-net: catching fraud is the point of the demo
                print(f"  [FRAUD] {s.task_id} by {s.miner.name}: claimed L/D={fake_ld:.3f}, "
                      f"real L/D={_ld(s):.3f}. Slashed -> stake {acc['stake']}"
                      f"{'  >>> BANNED' if acc['status'] == 'banned' else ''}  [{reason}]")
                newly_caught.add(s.miner)

            # A caught miner's other unverified work in this epoch can't be trusted either
            todo = [s for s in self._pending if not s.verified and s.miner in newly_caught]
            if todo:
                self._log(f"  [verify] re-auditing {len(todo)} more task(s) from caught miner(s)")
                reason = "re-audit after fraud"
        return caught_total

    def finalize_epoch(self, gen):
        """End of the challenge window: settle every task of the epoch. Returns the total paid."""
        paid = sum(self.ledger.settle_task(self.client, s.task_id) for s in self._pending)
        self._pending = []
        return paid

    def close_job(self):
        self.refund = self.ledger.close_job(self.client, self.job_id)
        return self.refund

    # ------------------------------------------------------------------ reports
    def report(self):
        snap = self.ledger.snapshot()
        with open(os.path.join(self.log_dir, "ledger_state.json"), "w", encoding="utf-8") as f:
            json.dump(snap, f, indent=2)

        job = snap["jobs"].get(self.job_id, {})
        print("\n=== Network report ===")
        print(f"Program {snap['program_id'][:12]}.. ({snap['cluster']})  slot {snap['slot']}  "
              f"binary {self.binary_hash[:12]}..")
        print(f"Tasks: {self.stats['tasks']}  audits: {self.stats['audits']}  fraud caught: {self.stats['caught']}")
        paid = sum(m["earned"] for m in snap["miners"].values())
        print(f"Job {self.job_id}: budget {job.get('budget')}  paid to miners {paid}  "
              f"refunded {self.refund}  status {job.get('status')}")
        print(f"Balances: client={snap['wallets'].get(self.client.pubkey, 0)}  "
              f"verifier={snap['wallets'].get(self.verifier.pubkey, 0)}  treasury={snap['treasury']}")
        print(f"{'miner':<9} {'pubkey':<11} {'type':<7} {'tasks':>5} {'earned':>7} {'stake':>6} "
              f"{'caught':>6} {'faked':>6} {'status':<7}")
        for m in self.miners:
            a = snap["miners"][m.pubkey]
            print(f"{m.name:<9} {short(m.pubkey):<11} {'honest' if m.honest else 'lazy':<7} {a['tasks']:>5} "
                  f"{a['earned']:>7} {a['stake']:>6} {a['caught']:>6} {len(m.faked_tasks):>6} {a['status']:<7}")

        faked = set().union(*(m.faked_tasks for m in self.miners))
        undetected = [t for t in faked if snap["tasks"][t]["status"] != "rejected"]
        if faked:
            msg = f"Faked results: {len(faked)}, undetected: {len(undetected)}"
            if undetected:
                msg += " (missed by the random audit and never forced into a leader/parent check)"
            print(msg)
        print(f"Ledger log: {os.path.join(self.log_dir, 'ledger_tx.jsonl')}")
        self.ledger.close()

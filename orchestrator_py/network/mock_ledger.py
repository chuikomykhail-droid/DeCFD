"""In-memory implementation of the DeCFD program (see ledger.py for the spec).

Balances are integers and every instruction conserves the total supply:
  sum(wallets) + treasury + sum(miner stakes) + sum(job escrows) == total airdropped
"""
import json
import os
import threading
import time

from .ledger import Ledger, LedgerError
from .wallet import MockWallet, short


class MockSolanaProgram(Ledger):
    cluster = "mock"
    program_id = "DeCFD" + "1" * 39

    def __init__(self, log_dir):
        self._lock = threading.RLock()
        self.slot = 0
        self.config = None
        self.wallets = {}    # pubkey -> balance
        self.treasury = 0
        self.supply = 0      # total airdropped, for the conservation check
        self.miners = {}     # pubkey -> MinerAccount
        self.jobs = {}       # job_id -> Job
        self.tasks = {}      # task_id -> TaskAccount
        os.makedirs(log_dir, exist_ok=True)
        self._log = open(os.path.join(log_dir, "ledger_tx.jsonl"), "w", encoding="utf-8")

    # ------------------------------------------------------------------ internals
    def _tx(self, ix, signer, **data):
        self.slot += 1
        record = {"slot": self.slot, "ix": ix, "signer": signer.pubkey, **data}
        sig = signer.sign(json.dumps(record, sort_keys=True).encode())
        # Wall-clock time is not part of the signed record: it only feeds the dashboard's timeline
        self._log.write(json.dumps({"sig": sig, **record, "t": round(time.time(), 3)}) + "\n")
        self._log.flush()
        return sig

    def _debit(self, pubkey, amount):
        if self.wallets.get(pubkey, 0) < amount:
            raise LedgerError(f"{short(pubkey)}: insufficient funds ({self.wallets.get(pubkey, 0)} < {amount})")
        self.wallets[pubkey] -= amount

    def _credit(self, pubkey, amount):
        self.wallets[pubkey] = self.wallets.get(pubkey, 0) + amount

    def _task(self, task_id):
        if task_id not in self.tasks:
            raise LedgerError(f"Unknown task {task_id}")
        return self.tasks[task_id]

    def _require_config(self):
        if self.config is None:
            raise LedgerError("Program not initialized")

    # ------------------------------------------------------------------ instructions
    def wallet(self, label, rng=None):
        return MockWallet(rng, label)

    def airdrop(self, pubkey, amount):
        with self._lock:
            self._credit(pubkey, amount)
            self.supply += amount

    def initialize(self, admin, verifier_pubkey, min_stake, slash_bps, verifier_share_bps):
        with self._lock:
            if self.config is not None:
                raise LedgerError("Already initialized")
            self.config = {"admin": admin.pubkey, "verifier": verifier_pubkey, "min_stake": min_stake,
                           "slash_bps": slash_bps, "verifier_share_bps": verifier_share_bps}
            return self._tx("initialize", admin, verifier=verifier_pubkey, min_stake=min_stake,
                            slash_bps=slash_bps, verifier_share_bps=verifier_share_bps)

    def register_miner(self, miner, name, stake):
        with self._lock:
            self._require_config()
            if miner.pubkey in self.miners:
                raise LedgerError(f"Miner {short(miner.pubkey)} already registered")
            if stake < self.config["min_stake"]:
                raise LedgerError("Stake below the minimum")
            self._debit(miner.pubkey, stake)
            self.miners[miner.pubkey] = {
                "name": name, "stake": stake, "earned": 0, "slashed": 0,
                "tasks": 0, "caught": 0, "status": "active",
            }
            return self._tx("register_miner", miner, name=name, stake=stake)

    def create_job(self, client, job_id, budget, reward, binary_hash, steps=0, avg=0):
        with self._lock:
            self._require_config()
            if job_id in self.jobs:
                raise LedgerError(f"Job {job_id} exists")
            self._debit(client.pubkey, budget)
            self.jobs[job_id] = {"client": client.pubkey, "budget": budget, "escrow": budget, "reserved": 0,
                                 "reward": reward, "binary_hash": binary_hash, "steps": steps, "avg": avg,
                                 "tasks": 0, "status": "open"}
            return self._tx("create_job", client, job=job_id, budget=budget, reward=reward,
                            binary_hash=binary_hash)

    def create_task(self, client, job_id, task_id, params_hash, epoch, params=None, assigned=None):
        with self._lock:
            job = self.jobs.get(job_id)
            if job is None or job["status"] != "open":
                raise LedgerError(f"Job {job_id} is not open")
            if job["client"] != client.pubkey:
                raise LedgerError("Only the job's client can create tasks")
            if task_id in self.tasks:
                raise LedgerError(f"Task {task_id} exists")
            if job["escrow"] - job["reserved"] < job["reward"]:
                raise LedgerError(f"Job {job_id} budget exhausted")
            job["reserved"] += job["reward"]
            job["tasks"] += 1
            self.tasks[task_id] = {"job": job_id, "epoch": epoch, "params": params, "params_hash": params_hash,
                                   "reward": job["reward"], "status": "open", "assigned": assigned,
                                   "miner": None, "result": None, "result_hash": None}
            return self._tx("create_task", client, job=job_id, task=task_id, epoch=epoch,
                            params_hash=params_hash, reward=job["reward"], assigned=assigned)

    def submit_result(self, miner, task_id, result_hash, result=None):
        with self._lock:
            t = self._task(task_id)
            if t["status"] != "open":
                raise LedgerError(f"Task {task_id} is not open")
            m = self.miners.get(miner.pubkey)
            if m is None or m["status"] != "active":
                raise LedgerError(f"Miner {short(miner.pubkey)} is not active")
            if t["assigned"] not in (None, miner.pubkey):
                raise LedgerError(f"Task {task_id} is assigned to another miner")
            t.update(status="submitted", miner=miner.pubkey, result_hash=result_hash, result=result)
            m["tasks"] += 1
            return self._tx("submit_result", miner, task=task_id, result_hash=result_hash)

    def resolve_challenge(self, verifier, task_id, verifier_hash):
        with self._lock:
            self._require_config()
            if verifier.pubkey != self.config["verifier"]:
                raise LedgerError("Only the configured verifier can resolve challenges")
            t = self._task(task_id)
            if t["status"] != "submitted":
                raise LedgerError(f"Task {task_id} is {t['status']}, not submitted")
            if verifier_hash == t["result_hash"]:
                t["status"] = "verified"
                self._tx("verify_ok", verifier, task=task_id)
                return True

            m = self.miners[t["miner"]]
            penalty = m["stake"] * self.config["slash_bps"] // 10_000
            to_verifier = penalty * self.config["verifier_share_bps"] // 10_000
            m["stake"] -= penalty
            m["slashed"] += penalty
            m["caught"] += 1
            self._credit(verifier.pubkey, to_verifier)
            self.treasury += penalty - to_verifier
            self.jobs[t["job"]]["reserved"] -= t["reward"]   # reward goes back to the job budget
            t["status"] = "rejected"
            if m["stake"] < self.config["min_stake"]:
                m["status"] = "banned"
            self._tx("slash", verifier, task=task_id, miner=t["miner"], penalty=penalty,
                     to_verifier=to_verifier, expected=verifier_hash, got=t["result_hash"],
                     banned=m["status"] == "banned")
            return False

    def settle_task(self, signer, task_id):
        with self._lock:
            t = self._task(task_id)
            if t["status"] in ("rejected", "cancelled"):
                return 0
            if t["status"] not in ("submitted", "verified"):
                raise LedgerError(f"Task {task_id} is {t['status']}, cannot settle")
            job = self.jobs[t["job"]]
            job["escrow"] -= t["reward"]
            job["reserved"] -= t["reward"]
            self._credit(t["miner"], t["reward"])
            self.miners[t["miner"]]["earned"] += t["reward"]
            t["status"] = "finalized"
            self._tx("settle_task", signer, task=task_id, miner=t["miner"], paid=t["reward"])
            return t["reward"]

    def cancel_task(self, client, task_id):
        with self._lock:
            t = self._task(task_id)
            if t["status"] != "open":
                raise LedgerError(f"Task {task_id} is not open")
            if self.jobs[t["job"]]["client"] != client.pubkey:
                raise LedgerError("Only the job's client can cancel its tasks")
            self.jobs[t["job"]]["reserved"] -= t["reward"]
            t["status"] = "cancelled"
            return self._tx("cancel_task", client, task=task_id)

    def close_job(self, client, job_id):
        with self._lock:
            job = self.jobs.get(job_id)
            if job is None or job["status"] != "open":
                raise LedgerError(f"Job {job_id} is not open")
            if job["client"] != client.pubkey:
                raise LedgerError("Only the job's client can close it")
            if job["reserved"]:
                raise LedgerError(f"Job {job_id} still has unsettled tasks")
            refund = job["escrow"]
            job["escrow"] = 0
            job["status"] = "closed"
            self._credit(client.pubkey, refund)
            self._tx("close_job", client, job=job_id, refund=refund)
            return refund

    # ------------------------------------------------------------------ queries
    def miner_account(self, pubkey):
        with self._lock:
            return dict(self.miners[pubkey])

    def task_account(self, task_id):
        with self._lock:
            return dict(self._task(task_id))

    def job_account(self, job_id):
        with self._lock:
            return dict(self.jobs[job_id])

    def balance(self, pubkey):
        with self._lock:
            return self.wallets.get(pubkey, 0)

    def current_slot(self):
        return self.slot

    def total_supply_held(self):
        """Everything the program accounts for; equals `supply` after every instruction."""
        with self._lock:
            return (sum(self.wallets.values()) + self.treasury
                    + sum(m["stake"] for m in self.miners.values())
                    + sum(j["escrow"] for j in self.jobs.values()))

    def snapshot(self):
        with self._lock:
            return {
                "cluster": self.cluster, "program_id": self.program_id, "slot": self.slot,
                "config": dict(self.config or {}), "supply": self.supply, "treasury": self.treasury,
                "wallets": dict(self.wallets),
                "miners": {k: dict(v) for k, v in self.miners.items()},
                "jobs": {k: dict(v) for k, v in self.jobs.items()},
                "tasks": {k: dict(v) for k, v in self.tasks.items()},
            }

    def close(self):
        self._log.close()

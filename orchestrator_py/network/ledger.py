"""Mock of the on-chain DeCFD program.

The structure deliberately mirrors what an Anchor program on Solana would look like,
so this file is the spec for the future smart contract:

  Accounts:  Client (job poster), Escrow (task rewards), Treasury (slashed stake),
             MinerAccount {stake, earned, slashed, status}, TaskAccount {params_hash, result_hash, status}
  Instructions:
             register_miner, create_task, submit_result, resolve_challenge, finalize_epoch

Every instruction is appended to a JSONL transaction log with a slot number and a
signature-like hash, so the demo can show a "block explorer" view.
"""
import hashlib
import json
import threading

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58encode(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    s = ""
    while n:
        n, r = divmod(n, 58)
        s = _B58[r] + s
    pad = len(b) - len(b.lstrip(b"\0"))
    return "1" * pad + s


def new_pubkey(rng) -> str:
    return b58encode(bytes(rng.getrandbits(8) for _ in range(32)))


def short(pk: str) -> str:
    return f"{pk[:4]}..{pk[-4:]}"


class MockSolanaProgram:
    PROGRAM_ID = "DeCFD" + "1" * 39

    def __init__(self, log_path, rng, client_funds=100_000):
        self._lock = threading.RLock()
        self.slot = 0
        self.client = new_pubkey(rng)
        self.balances = {"client": client_funds, "escrow": 0, "treasury": 0}
        self.miners = {}  # pubkey -> MinerAccount
        self.tasks = {}   # task_id -> TaskAccount
        self._log = open(log_path, "w", encoding="utf-8")

    # ------------------------------------------------------------------ internals
    def _tx(self, ix, signer, **data):
        self.slot += 1
        record = {"slot": self.slot, "ix": ix, "signer": signer, **data}
        sig = b58encode(hashlib.sha256(json.dumps(record, sort_keys=True).encode()).digest())
        self._log.write(json.dumps({"sig": sig, **record}) + "\n")
        self._log.flush()
        return sig

    # --------------------------------------------------------------- instructions
    def register_miner(self, pubkey, name, stake):
        with self._lock:
            self.miners[pubkey] = {
                "name": name, "stake": stake, "earned": 0, "slashed": 0,
                "tasks": 0, "caught": 0, "status": "active",
            }
            return self._tx("register_miner", pubkey, name=name, stake=stake)

    def create_task(self, task_id, params_hash, binary_hash, reward):
        with self._lock:
            if self.balances["client"] < reward:
                raise RuntimeError("Client account has insufficient funds for task escrow")
            self.balances["client"] -= reward
            self.balances["escrow"] += reward
            self.tasks[task_id] = {
                "params_hash": params_hash, "binary_hash": binary_hash, "reward": reward,
                "status": "open", "miner": None, "result_hash": None,
            }
            return self._tx("create_task", self.client, task=task_id,
                            params_hash=params_hash[:16], binary_hash=binary_hash[:16], reward=reward)

    def submit_result(self, task_id, miner, result_hash):
        with self._lock:
            t = self.tasks[task_id]
            if t["status"] != "open":
                raise RuntimeError(f"Task {task_id} is not open")
            if self.miners[miner]["status"] != "active":
                raise RuntimeError(f"Miner {short(miner)} is not active")
            t.update(status="submitted", miner=miner, result_hash=result_hash)
            self.miners[miner]["tasks"] += 1
            return self._tx("submit_result", miner, task=task_id, result_hash=result_hash[:16])

    def resolve_challenge(self, task_id, verifier, verifier_hash, slash_frac, min_stake):
        """Verifier re-ran the task. Matching hash -> verified. Mismatch -> slash the miner,
        refund the client, ban the miner if its stake drops below min_stake."""
        with self._lock:
            t = self.tasks[task_id]
            m = self.miners[t["miner"]]
            if verifier_hash == t["result_hash"]:
                t["status"] = "verified"
                self._tx("verify_ok", verifier, task=task_id)
                return True

            penalty = int(m["stake"] * slash_frac)
            m["stake"] -= penalty
            m["slashed"] += penalty
            m["caught"] += 1
            self.balances["treasury"] += penalty
            self.balances["escrow"] -= t["reward"]
            self.balances["client"] += t["reward"]
            t["status"] = "rejected"
            if m["stake"] < min_stake:
                m["status"] = "banned"
            self._tx("slash", verifier, task=task_id, miner=t["miner"], penalty=penalty,
                     expected=verifier_hash[:16], got=t["result_hash"][:16], banned=m["status"] == "banned")
            return False

    def finalize_epoch(self, epoch):
        """End of the challenge window: pay out every submitted/verified task."""
        with self._lock:
            paid = 0
            for t in self.tasks.values():
                if t["status"] in ("submitted", "verified"):
                    self.miners[t["miner"]]["earned"] += t["reward"]
                    self.balances["escrow"] -= t["reward"]
                    t["status"] = "finalized"
                    paid += t["reward"]
            self._tx("finalize_epoch", self.client, epoch=epoch, paid=paid)
            return paid

    # -------------------------------------------------------------------- queries
    def snapshot(self):
        with self._lock:
            return {
                "program_id": self.PROGRAM_ID, "slot": self.slot, "client": self.client,
                "balances": dict(self.balances),
                "miners": {k: dict(v) for k, v in self.miners.items()},
                "tasks": {k: dict(v) for k, v in self.tasks.items()},
            }

    def close(self):
        self._log.close()


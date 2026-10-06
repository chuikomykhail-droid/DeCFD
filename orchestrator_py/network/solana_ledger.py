"""Ledger backed by the DeCFD Anchor program (solana/programs/decfd) on Solana devnet.

Same interface as the mock: every method sends one instruction, signed by the wallet that
the program requires. Amounts in the interface are "tokens"; on chain they are lamports
(tokens * lamports_per_token).

Throughput: independent instructions (all tasks of a generation, all payouts of an epoch)
are sent back to back without waiting; the ledger waits for confirmations only when the
kind of instruction changes, i.e. when the next step depends on the previous ones.
Queries are answered from a local mirror of the accounts, which follows the program's
deterministic rules; a transaction that fails on chain raises LedgerError.
"""
import hashlib
import json
import os
import struct
import threading
import time

from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.system_program import ID as SYSTEM_PROGRAM
from solders.system_program import TransferParams, transfer
from solders.transaction import Transaction

from .ledger import Ledger, LedgerError
from .solana_rpc import DEVNET, RpcClient, RpcError
from .wallet import short

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
KEYS_DIR = os.path.join(ROOT, ".keys")
DEPLOYMENT = os.path.join(ROOT, "solana", "deployment.json")

TASK_STATUS = {0: "open", 1: "submitted", 2: "verified", 3: "rejected", 4: "finalized", 5: "cancelled"}
TASK_SIZE = 290            # 8 + Task::INIT_SPACE, fixed layout
TASK_ASSIGNED_OFFSET = 40
TASK_STATUS_OFFSET = 72
FUND_BUFFER = 6_000_000    # lamports for account rent + fees on top of a stake (0.006 SOL)


# ------------------------------------------------------------------------------ encoding
def ix_discriminator(name):
    return hashlib.sha256(f"global:{name}".encode()).digest()[:8]


def account_discriminator(name):
    return hashlib.sha256(f"account:{name}".encode()).digest()[:8]


def _u16(v): return struct.pack("<H", v)
def _u32(v): return struct.pack("<I", v)
def _u64(v): return struct.pack("<Q", v)
def _i64(v): return struct.pack("<q", v)
def _pk(v): return bytes(v if isinstance(v, Pubkey) else Pubkey.from_string(v))
def _f64s(vals): return b"".join(struct.pack("<d", float(v)) for v in vals)
def _string(s): return _u32(len(s.encode())) + s.encode()


def _finite(vals, n):
    """Borsh rejects NaN/inf: failed results are sent as zeros (the hash records the error)."""
    vals = list(vals or [0.0] * n)
    return [v if isinstance(v, (int, float)) and v == v and abs(v) != float("inf") else 0.0 for v in vals]


def task_label(epoch, index):
    return f"g{epoch}-t{index:04d}"


def task_index(task_id):
    return int(task_id.rsplit("-t", 1)[1])


# ------------------------------------------------------------------------------ PDAs
def config_pda(program, admin, network_id):
    return Pubkey.find_program_address([b"config", _pk(admin), _u64(network_id)], program)[0]


def miner_pda(program, config, authority):
    return Pubkey.find_program_address([b"miner", _pk(config), _pk(authority)], program)[0]


def job_pda(program, config, job_id):
    return Pubkey.find_program_address([b"job", _pk(config), _u64(job_id)], program)[0]


def task_pda(program, job, index):
    return Pubkey.find_program_address([b"task", _pk(job), _u32(index)], program)[0]


# ------------------------------------------------------------------------------ decoding
def decode_config(d):
    return {"admin": str(Pubkey(d[8:40])), "verifier": str(Pubkey(d[40:72])),
            "network_id": struct.unpack_from("<Q", d, 72)[0], "min_stake": struct.unpack_from("<Q", d, 80)[0],
            "challenge_window": struct.unpack_from("<q", d, 88)[0], "treasury": struct.unpack_from("<Q", d, 96)[0],
            "slash_bps": struct.unpack_from("<H", d, 104)[0], "verifier_share_bps": struct.unpack_from("<H", d, 106)[0]}


def decode_miner(d):
    n = struct.unpack_from("<I", d, 106)[0]
    return {"config": str(Pubkey(d[8:40])), "authority": str(Pubkey(d[40:72])),
            "stake": struct.unpack_from("<Q", d, 72)[0], "earned": struct.unpack_from("<Q", d, 80)[0],
            "slashed": struct.unpack_from("<Q", d, 88)[0], "tasks": struct.unpack_from("<I", d, 96)[0],
            "caught": struct.unpack_from("<I", d, 100)[0],
            "status": "active" if d[104] == 0 else "banned", "name": d[110:110 + n].decode()}


def decode_job(d):
    q = lambda off: struct.unpack_from("<Q", d, off)[0]  # noqa: E731
    return {"config": str(Pubkey(d[8:40])), "client": str(Pubkey(d[40:72])), "job_id": q(72), "budget": q(80),
            "escrow": q(88), "reserved": q(96), "reward": q(104), "binary_hash": d[112:144].hex(),
            "steps": struct.unpack_from("<I", d, 144)[0], "avg": struct.unpack_from("<I", d, 148)[0],
            "tasks": struct.unpack_from("<I", d, 152)[0], "status": "open" if d[156] == 0 else "closed"}


def decode_task(d):
    return {"job": str(Pubkey(d[8:40])), "assigned": str(Pubkey(d[40:72])), "status": TASK_STATUS[d[72]],
            "index": struct.unpack_from("<I", d, 73)[0], "epoch": struct.unpack_from("<I", d, 77)[0],
            "params": list(struct.unpack_from("<8d", d, 81)), "params_hash": d[145:177].hex(),
            "reward": struct.unpack_from("<Q", d, 177)[0], "miner": str(Pubkey(d[185:217])),
            "result": list(struct.unpack_from("<4d", d, 217)), "result_hash": d[249:281].hex(),
            "submitted_at": struct.unpack_from("<q", d, 281)[0]}


# ------------------------------------------------------------------------------ wallets
class SolanaWallet:
    def __init__(self, keypair, label=""):
        self.keypair = keypair
        self.pubkey = str(keypair.pubkey())
        self.label = label

    def sign(self, message: bytes) -> str:
        return str(self.keypair.sign_message(message))

    def __repr__(self):
        return f"SolanaWallet({self.label or short(self.pubkey)})"


def load_keypair(label, create=True):
    """Keypairs live in .keys/<label>.json (Solana CLI format, gitignored)."""
    path = os.path.join(KEYS_DIR, f"{label}.json")
    if os.path.exists(path):
        with open(path) as f:
            return Keypair.from_json(f.read())
    if not create:
        raise FileNotFoundError(path)
    os.makedirs(KEYS_DIR, exist_ok=True)
    kp = Keypair()
    with open(path, "w") as f:
        f.write(kp.to_json())
    return kp


def load_deployment():
    if not os.path.exists(DEPLOYMENT):
        raise LedgerError("solana/deployment.json not found: run `python orchestrator_py/devnet.py setup <PROGRAM_ID>`")
    with open(DEPLOYMENT) as f:
        return json.load(f)


# ------------------------------------------------------------------------------ the ledger
class SolanaLedger(Ledger):
    cluster = "devnet"

    def __init__(self, log_dir, program_id=None, rpc_url=None, lamports_per_token=100_000,
                 challenge_window=10, network_id=None):
        dep = load_deployment() if program_id is None or rpc_url is None else {}
        self.program_id = program_id or dep["program_id"]
        self.program = Pubkey.from_string(self.program_id)
        self.rpc = RpcClient(rpc_url or dep.get("rpc", DEVNET))
        self.lpt = lamports_per_token
        self.window = challenge_window
        self.network_id = network_id or int(time.time())
        self.meta = {"network_id": self.network_id, "lamports_per_token": self.lpt}

        self._lock = threading.RLock()
        self.slot = 0                # local event counter (the dashboard orders events by it)
        self.funder = None
        self.config_key = None
        self.config = None
        self.supply = 0
        self.miners, self.jobs, self.tasks = {}, {}, {}
        self._job_ids, self._job_keys = {}, {}
        self._pending, self._last_kind = [], None
        self._bh, self._bh_time = None, 0.0
        os.makedirs(log_dir, exist_ok=True)
        self._log = open(os.path.join(log_dir, "ledger_tx.jsonl"), "w", encoding="utf-8")

    # ------------------------------------------------------------------ plumbing
    def tok(self, lamports):
        """Lamports -> tokens for the interface and the event log."""
        t = lamports / self.lpt
        return int(t) if t == int(t) else round(t, 2)

    def _blockhash(self):
        if self._bh is None or time.time() - self._bh_time > 30:
            self._bh, self._bh_time = self.rpc.latest_blockhash(), time.time()
        return self._bh

    def _send(self, ixs, signers, payer):
        uniq = {}
        for w in [payer] + list(signers):
            uniq[w.pubkey] = w.keypair
        bh = self._blockhash()
        tx = Transaction(list(uniq.values()), Message.new_with_blockhash(ixs, payer.keypair.pubkey(), bh), bh)
        try:
            sig = self.rpc.send(bytes(tx))
        except RpcError as e:
            raise LedgerError(str(e)) from e
        self._pending.append(sig)
        return sig

    def _sync(self):
        if self._pending:
            try:
                self.rpc.wait(self._pending)
            except RpcError as e:
                raise LedgerError(str(e)) from e
            self._pending = []

    def _ix(self, kind, ix_name, accounts, data, signers, payer, event, **fields):
        """Send one program instruction; wait for earlier ones first if this step depends on them."""
        if kind != self._last_kind:
            self._sync()
        self._last_kind = kind
        ix = Instruction(self.program, ix_discriminator(ix_name) + data, accounts)
        sig = self._send([ix], signers, payer)
        self.slot += 1
        self._log.write(json.dumps({"sig": sig, "slot": self.slot, "ix": event, "signer": payer.pubkey, **fields}) + "\n")
        self._log.flush()
        return sig

    @staticmethod
    def _m(pubkey, signer=False, writable=False):
        return AccountMeta(pubkey if isinstance(pubkey, Pubkey) else Pubkey.from_string(pubkey), signer, writable)

    def _task(self, task_id):
        if task_id not in self.tasks:
            raise LedgerError(f"Unknown task {task_id}")
        return self.tasks[task_id]

    # ------------------------------------------------------------------ wallets and funding
    def wallet(self, label, rng=None):
        return SolanaWallet(load_keypair(label), label)

    def airdrop(self, pubkey, amount):
        """Fund a wallet from the client's wallet (devnet faucets are rate-limited)."""
        with self._lock:
            self.supply += amount
            if self.funder is None or pubkey == self.funder.pubkey:
                return
            target = amount * self.lpt + FUND_BUFFER
            have = self.rpc.balance(pubkey)
            if have >= target:
                return
            if self._last_kind != "fund":
                self._sync()
            self._last_kind = "fund"
            ix = transfer(TransferParams(from_pubkey=self.funder.keypair.pubkey(),
                                         to_pubkey=Pubkey.from_string(pubkey), lamports=target - have))
            self._send([ix], [], self.funder)

    # ------------------------------------------------------------------ instructions
    def initialize(self, admin, verifier_pubkey, min_stake, slash_bps, verifier_share_bps):
        with self._lock:
            self.funder = admin
            need = 300_000_000   # ~0.3 SOL is plenty for a small devnet run
            have = self.rpc.balance(admin.pubkey)
            if have < need:
                raise LedgerError(f"Client wallet {admin.pubkey} has {have / 1e9:.3f} SOL on devnet; fund it first "
                                  "(python orchestrator_py/devnet.py airdrop, or https://faucet.solana.com)")
            self.config_key = config_pda(self.program, admin.pubkey, self.network_id)
            self.meta["config"] = str(self.config_key)
            self.config = {"admin": admin.pubkey, "verifier": verifier_pubkey, "min_stake": min_stake * self.lpt,
                           "slash_bps": slash_bps, "verifier_share_bps": verifier_share_bps, "treasury": 0}
            data = (_u64(self.network_id) + _pk(verifier_pubkey) + _u64(min_stake * self.lpt)
                    + _u16(slash_bps) + _u16(verifier_share_bps) + _i64(self.window))
            accounts = [self._m(self.config_key, writable=True), self._m(admin.pubkey, True, True),
                        self._m(SYSTEM_PROGRAM)]
            return self._ix("init", "initialize", accounts, data, [admin], admin, "initialize",
                            verifier=verifier_pubkey, min_stake=min_stake, slash_bps=slash_bps,
                            verifier_share_bps=verifier_share_bps, config=str(self.config_key))

    def register_miner(self, miner, name, stake):
        with self._lock:
            key = miner_pda(self.program, self.config_key, miner.pubkey)
            accounts = [self._m(self.config_key), self._m(key, writable=True), self._m(miner.pubkey, True, True),
                        self._m(SYSTEM_PROGRAM)]
            sig = self._ix("register", "register_miner", accounts, _string(name) + _u64(stake * self.lpt),
                           [miner], miner, "register_miner", name=name, stake=stake)
            self.miners[miner.pubkey] = {"name": name, "stake": stake * self.lpt, "earned": 0, "slashed": 0,
                                         "tasks": 0, "caught": 0, "status": "active"}
            return sig

    def track_miner(self, pubkey):
        """Load a miner that registered itself (a remote node) into the mirror. Returns it or None."""
        got = self.rpc.account(miner_pda(self.program, self.config_key, pubkey))
        if got is None:
            return None
        m = decode_miner(got[0])
        self.miners[pubkey] = {k: m[k] for k in ("name", "stake", "earned", "slashed", "tasks", "caught", "status")}
        self.slot += 1
        self._log.write(json.dumps({"sig": None, "slot": self.slot, "ix": "register_miner", "signer": pubkey,
                                    "name": m["name"], "stake": self.tok(m["stake"]), "remote": True}) + "\n")
        self._log.flush()
        return self.miners[pubkey]

    def create_job(self, client, job_id, budget, reward, binary_hash, steps=0, avg=0):
        with self._lock:
            jid = len(self._job_ids) + 1
            key = job_pda(self.program, self.config_key, jid)
            self._job_ids[job_id], self._job_keys[job_id] = jid, key
            data = (_u64(jid) + _u64(budget * self.lpt) + _u64(reward * self.lpt) + bytes.fromhex(binary_hash)
                    + _u32(steps) + _u32(avg))
            accounts = [self._m(self.config_key), self._m(key, writable=True), self._m(client.pubkey, True, True),
                        self._m(SYSTEM_PROGRAM)]
            sig = self._ix("job", "create_job", accounts, data, [client], client, "create_job",
                           job=job_id, budget=budget, reward=reward, binary_hash=binary_hash, address=str(key))
            self.jobs[job_id] = {"client": client.pubkey, "budget": budget * self.lpt, "escrow": budget * self.lpt,
                                 "reserved": 0, "reward": reward * self.lpt, "binary_hash": binary_hash,
                                 "steps": steps, "avg": avg, "tasks": 0, "status": "open", "address": str(key)}
            return sig

    def create_task(self, client, job_id, task_id, params_hash, epoch, params=None, assigned=None):
        with self._lock:
            job = self.jobs[job_id]
            if job["escrow"] - job["reserved"] < job["reward"]:
                raise LedgerError(f"Job {job_id} budget exhausted")
            idx = task_index(task_id)
            key = task_pda(self.program, self._job_keys[job_id], idx)
            data = (_u32(idx) + _u32(epoch) + _f64s(_finite(params, 8)) + bytes.fromhex(params_hash)
                    + _pk(assigned or Pubkey.default()))
            accounts = [self._m(self._job_keys[job_id], writable=True), self._m(key, writable=True),
                        self._m(client.pubkey, True, True), self._m(SYSTEM_PROGRAM)]
            sig = self._ix("task", "create_task", accounts, data, [client], client, "create_task",
                           job=job_id, task=task_id, epoch=epoch, params_hash=params_hash,
                           reward=self.tok(job["reward"]), assigned=assigned, address=str(key))
            job["reserved"] += job["reward"]
            job["tasks"] += 1
            self.tasks[task_id] = {"job": job_id, "epoch": epoch, "params_hash": params_hash, "reward": job["reward"],
                                   "status": "open", "miner": None, "result_hash": None, "assigned": assigned,
                                   "address": str(key), "submitted_at": None}
            return sig

    def submit_result(self, miner, task_id, result_hash, result=None):
        with self._lock:
            t = self._task(task_id)
            m = self.miners.get(miner.pubkey)
            if t["status"] != "open":
                raise LedgerError(f"Task {task_id} is not open")
            if m is None or m["status"] != "active":
                raise LedgerError(f"Miner {short(miner.pubkey)} is not active")
            accounts = [self._m(self.config_key), self._m(self._job_keys[t["job"]]), self._m(t["address"], writable=True),
                        self._m(miner_pda(self.program, self.config_key, miner.pubkey), writable=True),
                        self._m(miner.pubkey, signer=True)]
            sig = self._ix("submit", "submit_result", accounts, _f64s(_finite(result, 4)) + bytes.fromhex(result_hash),
                           [miner], miner, "submit_result", task=task_id, result_hash=result_hash)
            t.update(status="submitted", miner=miner.pubkey, result_hash=result_hash, submitted_at=time.time())
            m["tasks"] += 1
            return sig

    def fetch_task(self, task_id):
        """Read a task from the chain (a remote miner may have submitted it) and refresh the mirror."""
        t = self._task(task_id)
        got = self.rpc.account(t["address"])
        if got is None:
            return None
        chain = decode_task(got[0])
        if chain["status"] == "submitted" and t["status"] == "open":
            m = self.miners.get(chain["miner"])
            if m:
                m["tasks"] += 1
            t.update(status="submitted", miner=chain["miner"], result_hash=chain["result_hash"],
                     submitted_at=time.time())
            self.slot += 1
            self._log.write(json.dumps({"sig": None, "slot": self.slot, "ix": "submit_result", "signer": chain["miner"],
                                        "task": task_id, "result_hash": chain["result_hash"], "remote": True}) + "\n")
            self._log.flush()
        return chain

    def resolve_challenge(self, verifier, task_id, verifier_hash):
        with self._lock:
            t = self._task(task_id)
            if t["status"] != "submitted":
                raise LedgerError(f"Task {task_id} is {t['status']}, not submitted")
            accounts = [self._m(self.config_key, writable=True), self._m(self._job_keys[t["job"]], writable=True),
                        self._m(t["address"], writable=True),
                        self._m(miner_pda(self.program, self.config_key, t["miner"]), writable=True),
                        self._m(verifier.pubkey, True, True)]
            data = bytes.fromhex(verifier_hash)
            if verifier_hash == t["result_hash"]:
                self._ix("audit", "resolve_challenge", accounts, data, [verifier], verifier, "verify_ok", task=task_id)
                t["status"] = "verified"
                return True
            m = self.miners[t["miner"]]
            penalty = m["stake"] * self.config["slash_bps"] // 10_000
            to_verifier = penalty * self.config["verifier_share_bps"] // 10_000
            m["stake"] -= penalty
            m["slashed"] += penalty
            m["caught"] += 1
            if m["stake"] < self.config["min_stake"]:
                m["status"] = "banned"
            self.config["treasury"] += penalty - to_verifier
            self.jobs[t["job"]]["reserved"] -= t["reward"]
            t["status"] = "rejected"
            self._ix("audit", "resolve_challenge", accounts, data, [verifier], verifier, "slash", task=task_id,
                     miner=t["miner"], penalty=self.tok(penalty), to_verifier=self.tok(to_verifier),
                     expected=verifier_hash, got=t["result_hash"], banned=m["status"] == "banned")
            return False

    def settle_task(self, signer, task_id):
        with self._lock:
            t = self._task(task_id)
            if t["status"] in ("rejected", "cancelled"):
                return 0
            if t["status"] not in ("submitted", "verified"):
                raise LedgerError(f"Task {task_id} is {t['status']}, cannot settle")
            if t["status"] == "submitted":
                # Unaudited tasks become payable once the challenge window has passed on chain
                wait = t["submitted_at"] + self.window + 3 - time.time()
                if wait > 0:
                    time.sleep(wait)
            job = self.jobs[t["job"]]
            accounts = [self._m(self.config_key), self._m(self._job_keys[t["job"]], writable=True),
                        self._m(t["address"], writable=True),
                        self._m(miner_pda(self.program, self.config_key, t["miner"]), writable=True),
                        self._m(t["miner"], writable=True), self._m(signer.pubkey, signer=True)]
            self._ix("settle", "settle_task", accounts, b"", [signer], signer, "settle_task", task=task_id,
                     miner=t["miner"], paid=self.tok(t["reward"]))
            job["escrow"] -= t["reward"]
            job["reserved"] -= t["reward"]
            self.miners[t["miner"]]["earned"] += t["reward"]
            t["status"] = "finalized"
            return self.tok(t["reward"])

    def cancel_task(self, client, task_id):
        with self._lock:
            t = self._task(task_id)
            if t["status"] != "open":
                raise LedgerError(f"Task {task_id} is not open")
            accounts = [self._m(self._job_keys[t["job"]], writable=True), self._m(t["address"], writable=True),
                        self._m(client.pubkey, signer=True)]
            self._ix("cancel", "cancel_task", accounts, b"", [client], client, "cancel_task", task=task_id)
            self.jobs[t["job"]]["reserved"] -= t["reward"]
            t["status"] = "cancelled"

    def close_job(self, client, job_id):
        with self._lock:
            job = self.jobs[job_id]
            if job["reserved"]:
                raise LedgerError(f"Job {job_id} still has unsettled tasks")
            accounts = [self._m(self._job_keys[job_id], writable=True), self._m(client.pubkey, True, True)]
            refund = job["escrow"]
            self._ix("close", "close_job", accounts, b"", [client], client, "close_job", job=job_id,
                     refund=self.tok(refund))
            self._sync()
            job["escrow"] = 0
            job["status"] = "closed"
            return self.tok(refund)

    # ------------------------------------------------------------------ queries (mirror, in tokens)
    def _tokens(self, d, keys):
        return {k: (self.tok(v) if k in keys else v) for k, v in d.items()}

    def miner_account(self, pubkey):
        return self._tokens(self.miners[pubkey], ("stake", "earned", "slashed"))

    def task_account(self, task_id):
        return self._tokens(self._task(task_id), ("reward",))

    def job_account(self, job_id):
        return self._tokens(self.jobs[job_id], ("budget", "escrow", "reserved", "reward"))

    def balance(self, pubkey):
        return self.tok(self.rpc.balance(pubkey))

    def current_slot(self):
        return self.slot

    def snapshot(self):
        with self._lock:
            return {
                "cluster": self.cluster, "program_id": self.program_id, "slot": self.slot, **self.meta,
                "config": self._tokens(self.config or {}, ("min_stake", "treasury")),
                "supply": self.supply, "treasury": self.tok((self.config or {}).get("treasury", 0)),
                "wallets": {},
                "miners": {k: self.miner_account(k) for k in self.miners},
                "jobs": {k: self.job_account(k) for k in self.jobs},
                "tasks": {k: self.task_account(k) for k in self.tasks},
            }

    def close(self):
        try:
            self._sync()
        finally:
            self._log.close()

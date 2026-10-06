"""The DeCFD on-chain program, as seen by the coordinator.

`Ledger` is the only boundary between the compute network and the blockchain. The mock
(`mock_ledger.MockSolanaProgram`) implements it in memory; `solana_ledger.SolanaLedger`
implements the same methods by sending instructions to the Anchor program in
solana/programs/decfd on devnet. Nothing above this interface (the GA, ComputeNetwork,
the dashboard) changes with the backend.

Accounts (all amounts are integer base units, like lamports):
  Config        verifier, min_stake, slash_bps, verifier_share_bps     (set once by `initialize`)
  Job           client, budget, escrow, reserved, reward, binary_hash, steps, avg, tasks, status
  MinerAccount  stake, earned, slashed, tasks, caught, status (active | banned)
  TaskAccount   job, epoch, params (the shape), params_hash, reward, assigned miner, miner,
                result (fx, fy, cd, cl), result_hash, submitted_at,
                status: open -> submitted -> (verified) -> finalized, or rejected / cancelled
  Treasury      the part of slashed stake that does not go to the verifier

Instructions (every one is signed by a wallet and appended to the event log):
  initialize(admin, verifier, min_stake, slash_bps, verifier_share_bps)
  register_miner(miner, name, stake)          stake moves from the miner's wallet
  create_job(client, job_id, budget, reward, binary_hash, steps, avg)   budget into escrow
  create_task(client, job_id, task_id, params_hash, epoch, params, assigned)   reserves a reward
  submit_result(miner, task_id, result_hash, result)   only the assigned miner, only an open task
  resolve_challenge(verifier, task_id, verifier_hash)
        match    -> verified
        mismatch -> slash stake (verifier_share to the verifier, rest to treasury),
                    release the reward back to the job, ban below min_stake
  settle_task(signer, task_id)                pay a verified task, or a submitted one whose challenge
                                              window has passed (permissionless crank)
  cancel_task(client, task_id)                withdraw an open task nobody computed
  close_job(client, job_id)                   refund unreserved escrow to the client

One instruction touches a bounded set of accounts, so each maps to a single Solana
instruction (unlike a loop over all tasks of an epoch).

Event log: one JSON object per instruction, `{"sig", "slot", "ix", "signer", ...fields}`,
written to `ledger_tx.jsonl`. The dashboard reads only this log plus `snapshot()`, so a
devnet backend writes the same records with real transaction signatures.
"""
from abc import ABC, abstractmethod

TASK_STATUSES = ("open", "submitted", "verified", "rejected", "finalized")


class LedgerError(RuntimeError):
    """An instruction the program would reject (wrong signer, wrong state, no funds)."""


class Ledger(ABC):
    cluster = "abstract"
    program_id = ""

    # ------------------------------------------------------------------ instructions
    @abstractmethod
    def initialize(self, admin, verifier_pubkey, min_stake, slash_bps, verifier_share_bps): ...

    @abstractmethod
    def register_miner(self, miner, name, stake): ...

    @abstractmethod
    def create_job(self, client, job_id, budget, reward, binary_hash, steps=0, avg=0): ...

    @abstractmethod
    def create_task(self, client, job_id, task_id, params_hash, epoch, params=None, assigned=None):
        """`params`: the shape's 8 numbers; `assigned`: the only miner allowed to submit (None = anyone)."""

    @abstractmethod
    def submit_result(self, miner, task_id, result_hash, result=None):
        """`result`: (fx, fy, cd, cl), published next to the hash so the client can read it."""

    @abstractmethod
    def resolve_challenge(self, verifier, task_id, verifier_hash):
        """Returns True if the miner's commitment matched."""

    @abstractmethod
    def settle_task(self, signer, task_id):
        """Returns the reward paid (0 for a rejected task)."""

    @abstractmethod
    def cancel_task(self, client, task_id): ...

    @abstractmethod
    def close_job(self, client, job_id):
        """Returns the refund paid to the client."""

    # ------------------------------------------------------------------ queries
    @abstractmethod
    def miner_account(self, pubkey) -> dict: ...

    @abstractmethod
    def task_account(self, task_id) -> dict: ...

    @abstractmethod
    def job_account(self, job_id) -> dict: ...

    @abstractmethod
    def balance(self, pubkey) -> int: ...

    @abstractmethod
    def current_slot(self) -> int: ...

    @abstractmethod
    def snapshot(self) -> dict: ...

    # ------------------------------------------------------------------ wallets and funding
    @abstractmethod
    def wallet(self, label, rng=None):
        """A signing identity: mock key from `rng`, or a persistent keypair in .keys/<label>.json."""

    def airdrop(self, pubkey, amount):
        """Fund a wallet with `amount` tokens (mock: mint; devnet: transfer from the client)."""
        raise NotImplementedError

    def close(self):
        pass


def create_ledger(kind, **kwargs):
    if kind == "mock":
        from .mock_ledger import MockSolanaProgram
        return MockSolanaProgram(log_dir=kwargs["log_dir"])
    if kind == "devnet":
        from .solana_ledger import SolanaLedger
        return SolanaLedger(**kwargs)
    raise ValueError(f"Unknown ledger backend '{kind}' (available: mock, devnet)")

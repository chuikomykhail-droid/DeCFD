# DeCFD on-chain program

`programs/decfd/src/lib.rs` is an Anchor program implementing the ledger specified in
[`orchestrator_py/network/ledger.py`](../orchestrator_py/network/ledger.py): network config,
miner stakes, job escrow, simulation tasks, audits with slashing, payouts, cancellation.
The Python client is [`orchestrator_py/network/solana_ledger.py`](../orchestrator_py/network/solana_ledger.py).

| Account | Seeds | Holds |
|---|---|---|
| Config | `"config", admin, network_id` | verifier, min stake, slash rate, verifier share, challenge window, treasury (lamports) |
| Miner | `"miner", config, authority` | stake (lamports), earnings, tasks, catches, status |
| Job | `"job", config, job_id` | escrowed budget (lamports), reward, pinned worker hash, solver settings |
| Task | `"task", job, index` | shape parameters, assigned miner, result (fx, fy, cd, cl), result hash, status |

| Instruction | Signer | Effect |
|---|---|---|
| `initialize` | admin | create a network instance |
| `register_miner` | miner | lock the stake |
| `create_job` | client | escrow the budget |
| `create_task` | client | reserve one reward; record the shape and who should compute it |
| `submit_result` | assigned miner | publish the result and commit to its hash |
| `resolve_challenge` | verifier | match: verified; mismatch: slash (part to the verifier), release the reward, ban below min stake |
| `settle_task` | anyone | pay a verified task, or a submitted one whose challenge window has passed |
| `cancel_task` | client | withdraw an open task nobody computed |
| `close_job` | client | refund the unreserved escrow |

## Deploying with Solana Playground (no local toolchain)

1. Open https://beta.solpg.io, create a project with the **Anchor (Rust)** framework.
2. Replace `src/lib.rs` with `programs/decfd/src/lib.rs` from this folder.
3. Create a Playground wallet (bottom-left) and keep its backup file: it is the program's upgrade authority.
4. Get devnet SOL: `solana airdrop 2` in the Playground terminal, or https://faucet.solana.com. Deploying takes ~3 SOL.
5. **Build**, then **Deploy**. Playground writes the program id into `declare_id!`.
6. Locally: `python orchestrator_py/devnet.py setup <PROGRAM_ID>`, fund the client wallet it prints
   (`solana transfer <ADDRESS> 1 --allow-unfunded-recipient` in the Playground terminal), then
   `python orchestrator_py/app.py --ledger devnet --pop 8 --gen 8`.

## Remote miners

`orchestrator_py/miner_node.py` runs a miner on any computer with the same `worker.exe`: it finds the
tasks assigned to it on chain (a `getProgramAccounts` filter on the task's assignee and status),
checks the job's pinned worker hash and the task's parameter hash, computes, and submits.

```
python orchestrator_py/app.py --ledger devnet --remote-miners <NODE_ADDRESS>   # prints the network address
python orchestrator_py/miner_node.py --config <NETWORK_ADDRESS> --name alice   # on the miner's machine
```

## Checking the encoding without a validator

The Python client encodes instructions and decodes accounts by hand (Anchor discriminators +
Borsh). A small Rust program built against this crate serializes sample instructions and accounts
with the real types; the Python encoding matches them byte for byte.

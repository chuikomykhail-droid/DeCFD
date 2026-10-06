"""A DeCFD miner node: finds the tasks assigned to it on Solana devnet, runs the worker and
publishes the result and its hash on chain. It can run on any computer with the same
worker binary (the job pins its hash) and a funded wallet.

    python orchestrator_py/miner_node.py --config <NETWORK_CONFIG> --name alice

The coordinator (app.py --ledger devnet --remote-miners <node address>) prints the network
config address and waits until this node has registered. Ctrl+C stops the node.
"""
import argparse
import hashlib
import random
import sys
import time

from solders.instruction import AccountMeta, Instruction
from solders.message import Message
from solders.pubkey import Pubkey
from solders.system_program import ID as SYSTEM_PROGRAM
from solders.transaction import Transaction

from network.solana_ledger import (FUND_BUFFER, TASK_ASSIGNED_OFFSET, TASK_SIZE, TASK_STATUS_OFFSET, SolanaWallet,
                                   _f64s, _finite, _string, _u64, decode_config, decode_job, decode_task,
                                   ix_discriminator, load_deployment, load_keypair, miner_pda, task_label)
from network.solana_rpc import RpcClient, RpcError
from worker_runner import WORKER_PATH, file_hash, result_hash, run_worker


def send(rpc, program, name, accounts, data, wallet):
    ix = Instruction(program, ix_discriminator(name) + data, accounts)
    bh = rpc.latest_blockhash()
    tx = Transaction([wallet.keypair], Message.new_with_blockhash([ix], wallet.keypair.pubkey(), bh), bh)
    sig = rpc.send(bytes(tx))
    rpc.wait([sig])
    return sig


def main():
    ap = argparse.ArgumentParser(description="DeCFD miner node (devnet)")
    ap.add_argument("--config", required=True, help="network config address printed by the coordinator")
    ap.add_argument("--name", default="node", help="miner name (max 16 bytes); the key is .keys/node-<name>.json")
    ap.add_argument("--stake", type=int, default=100, help="stake in tokens")
    ap.add_argument("--lamports-per-token", type=int, default=100_000)
    ap.add_argument("--cheat-prob", type=float, default=0.0, help="fake this share of results (to demo slashing)")
    ap.add_argument("--poll", type=float, default=2.0, help="seconds between task polls")
    args = ap.parse_args()

    dep = load_deployment()
    rpc = RpcClient(dep["rpc"])
    program = Pubkey.from_string(dep["program_id"])
    me = SolanaWallet(load_keypair(f"node-{args.name}"), args.name)
    config = Pubkey.from_string(args.config)
    binary = file_hash(WORKER_PATH)
    print(f"Miner node {args.name}: {me.pubkey}")
    print(f"Program {program}, network {config}, worker {binary[:12]}..")

    got = rpc.account(config)
    if got is None:
        sys.exit("Network config not found on chain: start the coordinator first.")
    cfg = decode_config(got[0])
    stake = max(args.stake * args.lamports_per_token, cfg["min_stake"])

    need = stake + FUND_BUFFER
    while rpc.balance(me.pubkey) < need:
        print(f"Waiting for funds: need {need / 1e9:.4f} SOL at {me.pubkey}\n"
              f"  e.g. python orchestrator_py/devnet.py fund {me.pubkey} {need / 1e9 + 0.01:.3f}")
        time.sleep(10)

    my_account = miner_pda(program, config, me.pubkey)
    if rpc.account(my_account) is None:
        send(rpc, program, "register_miner",
             [AccountMeta(config, False, False), AccountMeta(my_account, False, True),
              AccountMeta(me.keypair.pubkey(), True, True), AccountMeta(SYSTEM_PROGRAM, False, False)],
             _string(args.name) + _u64(stake), me)
        print(f"Registered with stake {stake / 1e9:.4f} SOL")
    else:
        print("Already registered on this network")

    jobs, warned = {}, set()
    filters = [{"dataSize": TASK_SIZE},
               {"memcmp": {"offset": TASK_ASSIGNED_OFFSET, "bytes": me.pubkey}},
               {"memcmp": {"offset": TASK_STATUS_OFFSET, "bytes": "1"}}]   # base58 of b"\x00" = open
    print("Polling for tasks...")
    while True:
        try:
            found = rpc.program_accounts(program, filters)
        except RpcError as e:
            print(f"  rpc: {e}")
            time.sleep(args.poll * 3)
            continue
        for addr, data in sorted(found, key=lambda a: a[1][73:77]):
            t = decode_task(data)
            if t["job"] not in jobs:
                jobs[t["job"]] = decode_job(rpc.account(t["job"])[0])
            job = jobs[t["job"]]
            if job["config"] != str(config):
                continue
            if job["binary_hash"] != binary:
                if t["job"] not in warned:
                    print(f"  job {t['job'][:8]}.. pins worker {job['binary_hash'][:12]}.., mine is {binary[:12]}..: skipping")
                    warned.add(t["job"])
                continue
            # The exact command line the client hashed: shape parameters + the job's solver settings
            task_args = [str(p) for p in t["params"]] + ["--steps", str(job["steps"]), "--avg", str(job["avg"])]
            if hashlib.sha256(" ".join(task_args).encode()).hexdigest() != t["params_hash"]:
                print(f"  task {addr[:8]}..: parameters do not match their hash, skipping")
                continue
            tid = task_label(t["epoch"], t["index"])
            r = random.Random(f"{tid}:{me.pubkey}")
            if args.cheat_prob and r.random() < args.cheat_prob:
                cd, cl = r.uniform(0.25, 0.6), r.uniform(0.5, 2.0)
                res = {"status": "ok", "fx": cd * 0.25, "fy": cl * 0.25, "cd": cd, "cl": cl}
                note = " (faked)"
            else:
                res = run_worker(task_args, threads=None)
                note = ""
            h = result_hash(tid, res)
            vals = [res["fx"], res["fy"], res["cd"], res["cl"]] if res.get("status") == "ok" else None
            try:
                send(rpc, program, "submit_result",
                     [AccountMeta(config, False, False), AccountMeta(Pubkey.from_string(t["job"]), False, False),
                      AccountMeta(Pubkey.from_string(addr), False, True), AccountMeta(my_account, False, True),
                      AccountMeta(me.keypair.pubkey(), True, False)],
                     _f64s(_finite(vals, 4)) + bytes.fromhex(h), me)
            except RpcError as e:
                print(f"  {tid}: submit failed: {e}")
                continue
            ld = res["cl"] / res["cd"] if vals and res["cd"] else 0.0
            print(f"  {tid}: L/D={ld:6.3f}{note}  submitted, hash {h[:10]}")
        time.sleep(args.poll)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")

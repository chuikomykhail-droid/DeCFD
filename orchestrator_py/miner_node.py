"""A DeCFD miner node: finds the tasks assigned to it on Solana devnet, runs the worker and
publishes the result and its hash on chain. It runs on any computer with the same worker
binary (the job pins its hash) and a funded wallet; packaged, it needs no Python.

    python orchestrator_py/miner_node.py --name alice              # follow the coordinator's runs
    python orchestrator_py/miner_node.py --config <NETWORK> ...    # or join one network explicitly
    python orchestrator_py/miner_node.py --make-selftest           # write the reference for the self-test

The node first re-computes a few reference tasks and compares their hashes with the
coordinator's machine: a different CPU must produce identical bits, or an honest miner would
be slashed. Then it watches the chain for the newest network created by the coordinator,
registers in it (locking its stake) and computes every task assigned to it. A new run is a
new network, and the node follows it automatically. Ctrl+C stops the node.
"""
import argparse
import hashlib
import json
import os
import random
import socket
import sys
import time

from solders.instruction import AccountMeta, Instruction
from solders.message import Message
from solders.pubkey import Pubkey
from solders.system_program import ID as SYSTEM_PROGRAM
from solders.transaction import Transaction

from network.solana_ledger import (CONFIG_SIZE, DEPLOYMENT, FUND_BUFFER, TASK_ASSIGNED_OFFSET, TASK_SIZE,
                                   TASK_STATUS_OFFSET, SolanaWallet, _f64s, _finite, _string, _u64, decode_config,
                                   decode_job, decode_task, ix_discriminator, load_deployment, load_keypair, miner_pda,
                                   task_label)
from network.solana_rpc import RpcClient, RpcError
from worker_runner import APP_DIR, FROZEN, WORKER_PATH, file_hash, result_hash, run_worker

SELFTEST = os.path.join(APP_DIR, "selftest.json") if FROZEN else \
    os.path.join(os.path.dirname(DEPLOYMENT), "selftest.json")
SELFTEST_CASES = [   # thin cambered plate, thick blunt profile, symmetric profile at negative alpha
    ["60.0", "1.069", "4.527", "6.003", "5.003", "6.0", "13.08", "1.688"],
    ["60.0", "18.341", "9.829", "14.308", "10.986", "6.0", "9.6", "3.7"],
    ["60.0", "2.0", "8.0", "6.0", "3.0", "0.5", "-8.0", "0.0"],
]
SELFTEST_SIM = ["--steps", "400", "--avg", "100"]


def make_selftest():
    cases = []
    for args in SELFTEST_CASES:
        res = run_worker(args + SELFTEST_SIM, threads=None)
        if res.get("status") != "ok":
            sys.exit(f"worker failed on {args}: {res}")
        cases.append({"args": args + SELFTEST_SIM, "hash": result_hash("selftest", res)})
    with open(SELFTEST, "w") as f:
        json.dump({"binary_hash": file_hash(WORKER_PATH), "cases": cases}, f, indent=2)
    print(f"Wrote {SELFTEST}")


def run_selftest():
    if not os.path.exists(SELFTEST):
        print("No selftest.json: skipping the self-test")
        return True
    with open(SELFTEST) as f:
        ref = json.load(f)
    if ref["binary_hash"] != file_hash(WORKER_PATH):
        print("Self-test: worker.exe differs from the reference build")
        return False
    ok = True
    for i, case in enumerate(ref["cases"], 1):
        t0 = time.time()
        res = run_worker(case["args"], threads=None)
        same = res.get("status") == "ok" and result_hash("selftest", res) == case["hash"]
        ok &= same
        print(f"Self-test {i}/{len(ref['cases'])}: {'identical bits' if same else 'MISMATCH'} ({time.time() - t0:.1f} s)")
    return ok


def send(rpc, program, name, accounts, data, wallet):
    ix = Instruction(program, ix_discriminator(name) + data, accounts)
    bh = rpc.latest_blockhash()
    tx = Transaction([wallet.keypair], Message.new_with_blockhash([ix], wallet.keypair.pubkey(), bh), bh)
    sig = rpc.send(bytes(tx))
    rpc.wait([sig])
    return sig


def latest_network(rpc, program, coordinator):
    """The newest network config created by the coordinator's wallet, or None."""
    found = rpc.program_accounts(program, [{"dataSize": CONFIG_SIZE}, {"memcmp": {"offset": 8, "bytes": coordinator}}])
    if not found:
        return None
    return max(((k, decode_config(d)) for k, d in found), key=lambda kc: kc[1]["network_id"])[0]


def coordinator_key(dep):
    if dep.get("coordinator"):
        return dep["coordinator"]
    try:
        return str(load_keypair("client", create=False).pubkey())
    except FileNotFoundError:
        return None


def join(rpc, program, me, config, args):
    """Register on a network (lock the stake) unless already registered. Returns the miner account."""
    account = miner_pda(program, config, me.pubkey)
    if rpc.account(account) is not None:
        print(f"Already registered on network {str(config)[:8]}..")
        return account
    cfg = decode_config(rpc.account(config)[0])
    stake = max(args.stake * args.lamports_per_token, cfg["min_stake"])
    need = stake + FUND_BUFFER
    while rpc.balance(me.pubkey) < need:
        print(f"Waiting for funds: need {need / 1e9:.4f} SOL at {me.pubkey}\n"
              f"  e.g. python orchestrator_py/devnet.py fund {me.pubkey} {need / 1e9 + 0.01:.3f}")
        time.sleep(10)
    send(rpc, program, "register_miner",
         [AccountMeta(config, False, False), AccountMeta(account, False, True),
          AccountMeta(me.keypair.pubkey(), True, True), AccountMeta(SYSTEM_PROGRAM, False, False)],
         _string(me.label[:16]) + _u64(stake), me)
    print(f"Joined network {str(config)[:8]}.. with stake {stake / 1e9:.4f} SOL")
    return account


def compute_and_submit(rpc, program, me, config, account, addr, t, job, binary, args):
    if job["binary_hash"] != binary:
        return f"job pins worker {job['binary_hash'][:12]}.., mine is {binary[:12]}..: skipped"
    # The exact command line the client hashed: shape parameters + the job's solver settings
    task_args = [str(p) for p in t["params"]] + ["--steps", str(job["steps"]), "--avg", str(job["avg"])]
    if hashlib.sha256(" ".join(task_args).encode()).hexdigest() != t["params_hash"]:
        return "parameters do not match their hash: skipped"
    tid = task_label(t["epoch"], t["index"])
    r = random.Random(f"{tid}:{me.pubkey}")
    t0 = time.time()
    if args.cheat_prob and r.random() < args.cheat_prob:
        cd, cl = r.uniform(0.25, 0.6), r.uniform(0.5, 2.0)
        res, note = {"status": "ok", "fx": cd * 0.25, "fy": cl * 0.25, "cd": cd, "cl": cl}, " (faked)"
    else:
        res, note = run_worker(task_args, threads=None), ""
    h = result_hash(tid, res)
    vals = [res["fx"], res["fy"], res["cd"], res["cl"]] if res.get("status") == "ok" else None
    send(rpc, program, "submit_result",
         [AccountMeta(config, False, False), AccountMeta(Pubkey.from_string(t["job"]), False, False),
          AccountMeta(Pubkey.from_string(addr), False, True), AccountMeta(account, False, True),
          AccountMeta(me.keypair.pubkey(), True, False)],
         _f64s(_finite(vals, 4)) + bytes.fromhex(h), me)
    ld = res["cl"] / res["cd"] if vals and res["cd"] else 0.0
    return f"L/D={ld:6.3f}{note}  computed in {time.time() - t0:.1f} s, submitted (hash {h[:10]})"


def main():
    default_name = (os.environ.get("COMPUTERNAME") or socket.gethostname() or "node").lower()[:16]
    ap = argparse.ArgumentParser(description="DeCFD miner node (devnet)")
    ap.add_argument("--name", default=default_name, help="miner name (max 16 bytes); key: keys/node-<name>.json")
    ap.add_argument("--config", help="join this network only (default: follow the coordinator's newest network)")
    ap.add_argument("--stake", type=int, default=100, help="stake in tokens")
    ap.add_argument("--lamports-per-token", type=int, default=100_000)
    ap.add_argument("--cheat-prob", type=float, default=0.0, help="fake this share of results (to demo slashing)")
    ap.add_argument("--poll", type=float, default=2.0, help="seconds between task polls")
    ap.add_argument("--skip-selftest", action="store_true")
    ap.add_argument("--make-selftest", action="store_true", help="write the self-test reference and exit")
    args = ap.parse_args()

    if args.make_selftest:
        make_selftest()
        return
    sys.stdout.reconfigure(line_buffering=True)   # live output even when redirected to a log file

    dep = load_deployment()
    rpc = RpcClient(dep["rpc"])
    program = Pubkey.from_string(dep["program_id"])
    me = SolanaWallet(load_keypair(f"node-{args.name}"), args.name)
    binary = file_hash(WORKER_PATH)
    coordinator = coordinator_key(dep)
    print(f"DeCFD miner node '{args.name}'  wallet {me.pubkey}")
    print(f"Program {program} ({dep['cluster']}), worker {binary[:12]}..")
    print(f"Balance {rpc.balance(me.pubkey) / 1e9:.4f} SOL")

    if not args.skip_selftest and not run_selftest():
        sys.exit("Self-test failed: this machine computes different bits, so honest results would be slashed. "
                 "Stopping before joining a network.")
    if not args.config and not coordinator:
        sys.exit("No coordinator known: pass --config <network address>")

    current, account, jobs, done = None, None, {}, set()
    last_lookup = 0.0
    print("Waiting for a network..." if not args.config else "")
    while True:
        try:
            if args.config:
                target = Pubkey.from_string(args.config)
            elif time.time() - last_lookup > 10 or current is None:
                found = latest_network(rpc, program, coordinator)
                last_lookup = time.time()
                target = Pubkey.from_string(found) if found else None
            else:
                target = current
            if target is None:
                time.sleep(5)
                continue
            if target != current:
                account = join(rpc, program, me, target, args)
                current, jobs, done = target, {}, set()
                print("Polling for tasks...")

            found = rpc.program_accounts(program, [
                {"dataSize": TASK_SIZE},
                {"memcmp": {"offset": TASK_ASSIGNED_OFFSET, "bytes": me.pubkey}},
                {"memcmp": {"offset": TASK_STATUS_OFFSET, "bytes": "1"}}])   # base58 of b"\x00" = open
            for addr, data in sorted(found, key=lambda a: a[1][73:77]):
                if addr in done:
                    continue      # submitted already; the RPC node may still be a slot behind
                t = decode_task(data)
                if t["job"] not in jobs:
                    jobs[t["job"]] = decode_job(rpc.account(t["job"])[0])
                job = jobs[t["job"]]
                if job["config"] != str(current):
                    continue      # a task left open by an earlier run
                tid = task_label(t["epoch"], t["index"])
                try:
                    print(f"  {tid}: {compute_and_submit(rpc, program, me, current, account, addr, t, job, binary, args)}")
                    done.add(addr)
                except RpcError as e:
                    print(f"  {tid}: submit failed: {e}")
        except RpcError as e:
            print(f"  rpc: {e}")
            time.sleep(args.poll * 3)
            continue
        time.sleep(args.poll)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        if FROZEN:   # keep the console window open after an error
            try:
                input("Press Enter to close...")
            except EOFError:
                pass

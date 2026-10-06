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
import traceback

from solders.instruction import AccountMeta, Instruction
from solders.message import Message
from solders.pubkey import Pubkey
from solders.system_program import ID as SYSTEM_PROGRAM
from solders.transaction import Transaction

from network.solana_ledger import (CONFIG_SIZE, DEPLOYMENT, FUND_BUFFER, SolanaWallet, _f64s, _finite, _string, _u64,
                                   decode_config, decode_job, decode_task, ix_discriminator, job_pda, load_deployment,
                                   load_keypair, miner_pda, task_label, task_pda)
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
NETWORK_RECHECK = 20   # seconds between looks for a newer network while joined to one


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
    for attempt in range(5):
        bh = rpc.latest_blockhash()
        tx = Transaction([wallet.keypair], Message.new_with_blockhash([ix], wallet.keypair.pubkey(), bh), bh)
        try:
            sig = rpc.send(bytes(tx))
            break
        except RpcError as e:
            # The RPC load balancer: the simulating node may not know a blockhash from another one yet.
            # The transaction did not execute, so sign it again with a newer blockhash
            if "BlockhashNotFound" not in str(e) or attempt == 4:
                raise
            time.sleep(1 + attempt)
    rpc.wait([sig])
    return sig


def latest_network(rpc, program, coordinator):
    """The newest network config created by the coordinator's wallet, or None."""
    found = rpc.program_accounts(program, [{"dataSize": CONFIG_SIZE}, {"memcmp": {"offset": 8, "bytes": coordinator}}])
    if not found:
        return None
    return max(((k, decode_config(d)) for k, d in found), key=lambda kc: kc[1]["network_id"])[0]


def run_finished(rpc, program, config):
    """True when the network's job is closed: joining would lock a stake in a finished run.
    No job yet means the coordinator is still gathering miners, which is the time to join."""
    got = rpc.account(job_pda(program, config, 1))
    return got is not None and decode_job(got[0])["status"] == "closed"


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
    took = time.time() - t0
    # The result is worth minutes of compute: keep retrying the submission through RPC hiccups
    for attempt in range(6):
        try:
            send(rpc, program, "submit_result",
                 [AccountMeta(config, False, False), AccountMeta(Pubkey.from_string(t["job"]), False, False),
                  AccountMeta(Pubkey.from_string(addr), False, True), AccountMeta(account, False, True),
                  AccountMeta(me.keypair.pubkey(), True, False)],
                 _f64s(_finite(vals, 4)) + bytes.fromhex(h), me)
            break
        except RpcError as e:
            if "TaskNotOpen" in str(e):
                return f"computed in {took:.1f} s, but the task is closed (already submitted, or reassigned)"
            if attempt == 5:
                raise
            print(f"  {tid}: submit failed ({str(e)[:80]}), retrying")
            time.sleep(5 * (attempt + 1))
    ld = res["cl"] / res["cd"] if vals and res["cd"] else 0.0
    return f"L/D={ld:6.3f}{note}  computed in {took:.1f} s, submitted (hash {h[:10]})"


def new_tasks(rpc, program, me, config, st):
    """Open tasks assigned to this node that it has not seen yet, in order.

    No getProgramAccounts here: the public RPC rate-limits it hard, and the laptops behind one
    router share one IP. The job account counts the tasks created so far, and a task's address
    is a PDA of (job, index), so two cheap reads find everything new."""
    got = rpc.account(st["job"])
    if got is None:
        return []      # the coordinator has not opened the job yet
    st["job_data"] = job = decode_job(got[0])
    n = job["tasks"]
    if n <= st["seen"]:
        return []
    addrs = [task_pda(program, st["job"], i) for i in range(st["seen"], n)]
    mine = []
    for addr, acc in zip(addrs, rpc.accounts(addrs)):
        if acc is None:
            break      # created, but this RPC node is a slot behind: read it next time
        st["seen"] += 1
        t = decode_task(acc[0])
        if t["assigned"] == me.pubkey and t["status"] == "open":
            mine.append((str(addr), t))
    return mine


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

    current, account, st = None, None, None
    last_lookup, idle_note = 0.0, False
    print("Waiting for a network..." if not args.config else "")
    while True:
        try:
            if args.config:
                target = Pubkey.from_string(args.config)
            elif current is None or time.time() - last_lookup > NETWORK_RECHECK:
                # A new run is a new network; while joined, look for one only now and then
                found = latest_network(rpc, program, coordinator)
                last_lookup = time.time()
                target = Pubkey.from_string(found) if found else current
            else:
                target = current
            if target is not None and target != current and run_finished(rpc, program, target):
                if not idle_note:   # the newest network is a finished run: wait for the next one
                    print("No run in progress: waiting for the coordinator to start one...")
                    idle_note = True
                target = current
            if target is None:
                time.sleep(10)
                continue
            if target != current:
                idle_note = False
                account = join(rpc, program, me, target, args)
                current = target
                st = {"job": job_pda(program, current, 1), "seen": 0, "job_data": None}
                print("Polling for tasks...")

            for addr, t in new_tasks(rpc, program, me, current, st):
                tid = task_label(t["epoch"], t["index"])
                try:
                    print(f"  {tid}: "
                          f"{compute_and_submit(rpc, program, me, current, account, addr, t, st['job_data'], binary, args)}")
                except RpcError as e:
                    print(f"  {tid}: submit failed: {e}")
        except RpcError as e:
            print(f"  rpc: {str(e)[:120]}")
            time.sleep(args.poll * 5)
            continue
        time.sleep(args.poll * random.uniform(0.8, 1.2))   # jitter: nodes behind one IP do not poll in step


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
    except Exception:
        if not FROZEN:
            raise
        # Print it now: an uncaught exception would only show after "Press Enter to close"
        traceback.print_exc()
        sys.exit(1)
    finally:
        if FROZEN:   # keep the console window open after an error
            try:
                input("Press Enter to close...")
            except EOFError:
                pass

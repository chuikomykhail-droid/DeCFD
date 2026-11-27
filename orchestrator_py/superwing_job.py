"""A SuperWing job on the DeCFD network: real RANS CFD of a TU Munich wing, paid on Solana.

The client posts conditions of one SuperWing case as tasks. Miner nodes compute them in the
pilot container (pyHyp mesh + ADflow, pilot/superwing) and publish CL, CD and a commitment
on chain. The verifier re-computes a sample and resolves each audit with a tolerance, since
an MPI solve is not bit-exact (orchestrator_py/superwing_tasks.py). Then every task is settled
and the job closed. At the end our numbers are put next to the TU Munich cluster's.

    # devnet; miner nodes are separate processes (miner_node.py --superwing), here or elsewhere
    python orchestrator_py/superwing_job.py --case pilot/superwing/cases/shape0000 --conditions 1,3,5 \\
        --ledger devnet --remote 2 --adflow-options "{\\"useANKSolver\\": false, \\"nCycles\\": 1500}"
    # everything in this process on the in-memory ledger, one honest and one lazy miner
    python orchestrator_py/superwing_job.py --case pilot/superwing/cases/shape0000 --conditions 1,3 \\
        --ledger mock --local 2 --lazy 1

Audits: every miner gets at least one of its tasks re-computed, picked with a secret seed whose
hash is published before the tasks and the seed itself after them; on top of that every task
is audited with probability --audit-rate. A caught miner's other tasks are all re-computed.
"""
import argparse
import hashlib
import json
import os
import random
import secrets
import sys
import time

from network.ledger import create_ledger
from network.solana_rpc import RpcError
from network.wallet import short
from superwing_tasks import (CL_TOL, CD_TOL, PILOT, canonical_options, fake_result, pipeline_hash, result_hash,
                             result_values, run_superwing, task_spec, within_tolerance)

sys.path.insert(0, PILOT)
from build_case import planform_area  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def audit_draw(seed, task_id):
    digest = hashlib.sha256(f"{seed}:{task_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def wait_for_remote(ledger, n, timeout, local):
    """Wait until `n` miner nodes have registered on this network; returns [(name, pubkey)]."""
    print(f"Network {ledger.meta.get('config')}: waiting for {n} miner node(s) to join "
          f"(python orchestrator_py/miner_node.py --superwing --name <name>)")
    joined, t0 = {}, time.time()
    while len(joined) < n and time.time() - t0 < timeout:
        try:
            for pk, acc in ledger.find_miners():
                if pk not in local and pk not in joined:
                    ledger.track_miner(pk)
                    joined[pk] = acc["name"]
                    print(f"  {acc['name']} ({short(pk)}) joined with stake {ledger.tok(acc['stake'])} "
                          f"[{len(joined)}/{n}]")
        except RpcError as e:
            print(f"  (rpc busy: {str(e)[:60]}..., retrying)")
        if len(joined) < n:
            time.sleep(5)
    if not joined:
        raise RuntimeError(f"No miner node joined within {timeout} s")
    return [(name, pk) for pk, name in joined.items()]


def chain_result(vals):
    """A task's result as read back from chain: CMz, cycles, CD, CL."""
    if not any(vals):
        return {"status": "error", "code": "remote"}
    return {"status": "ok", "CMz": vals[0], "n_step": vals[1], "CD": vals[2], "CL": vals[3]}


def collect_remote(ledger, client, tasks, timeout, poll=30):
    waiting = {t["id"]: t for t in tasks if t["miner"]["remote"]}
    t0, last_note = time.time(), 0.0
    while waiting and time.time() - t0 < timeout:
        try:
            found = ledger.fetch_tasks(list(waiting))
        except RpcError as e:
            print(f"  (rpc busy: {str(e)[:60]}..., retrying)")
            time.sleep(poll)
            continue
        for tid, chain in found.items():
            if chain and chain["status"] != "open":
                t = waiting.pop(tid)
                t["res"], t["hash"] = chain_result(chain["result"]), chain["result_hash"]
                print(f"  {tid} <- {t['miner']['name']}: {describe(t['res'])}  hash {t['hash'][:10]}")
        if waiting and time.time() - last_note > 600:
            names = ", ".join(f"{tid} ({t['miner']['name']})" for tid, t in waiting.items())
            print(f"  [{time.strftime('%H:%M')}] computing: {names}")
            last_note = time.time()
        if waiting:
            time.sleep(poll)
    for tid, t in waiting.items():
        print(f"  {tid}: {t['miner']['name']} did not submit within {timeout} s; cancelled")
        ledger.cancel_task(client, tid)
        t["res"], t["hash"] = {"status": "error", "code": "timeout"}, None


def describe(res):
    if res.get("status") != "ok":
        return f"failed ({res.get('code')})"
    return f"CL {res['CL']:.5f}  CD {res['CD']:.6f}  CMz {res['CMz']:.5f}  {res['n_step']:.0f} cycles"


def run_job(ledger, case_dir, conditions, options, local=1, lazy=0, remote=0, runner=run_superwing, n_proc=None,
            reward=100, stake=400, min_stake=200, slash=0.5, verifier_share=0.5, client_funds=10_000,
            audit_rate=0.0, audit_seed=None, remote_timeout=8 * 3600, seed=1, log_dir=None):
    """Post, compute, audit and settle one job. Returns the report (also written to log_dir)."""
    n_proc = n_proc or os.cpu_count()
    options = canonical_options(options)
    input_path = os.path.join(case_dir, "input.json")
    shape = int(os.path.basename(os.path.normpath(case_dir))[len("shape"):])
    rng = random.Random(seed)
    audit_seed = audit_seed or secrets.token_hex(32)
    commitment = hashlib.sha256(audit_seed.encode()).hexdigest()

    client = ledger.wallet("client", rng)
    verifier = ledger.wallet("verifier", rng)
    ledger.initialize(client, verifier.pubkey, min_stake, int(slash * 10_000), int(verifier_share * 10_000))
    ledger.airdrop(client.pubkey, client_funds)
    ledger.airdrop(verifier.pubkey, 0)
    # Confirm now: waiting for miner nodes takes minutes, and by then the RPC's recent-status cache
    # no longer knows these signatures, so a late check would take them for dropped and resend them
    getattr(ledger, "_sync", lambda: None)()
    print(f"Network on {ledger.cluster}; audit seed commitment {commitment[:16]}..")

    miners = []
    for i in range(local):
        name = f"sw-miner-{i + 1}"
        w = ledger.wallet(name, rng)
        ledger.airdrop(w.pubkey, stake)
        ledger.register_miner(w, name, stake)
        miners.append({"name": name, "pubkey": w.pubkey, "wallet": w, "remote": False, "lazy": i >= local - lazy})
    if remote:
        for name, pk in wait_for_remote(ledger, remote, remote_timeout, {m["pubkey"] for m in miners}):
            miners.append({"name": name, "pubkey": pk, "wallet": None, "remote": True, "lazy": None})
    if not miners:
        raise ValueError("no miners: pass --local or --remote")

    binary = pipeline_hash()
    steps = int(options.get("nCycles", 3000))
    ledger.create_job(client, "job-1", reward * len(conditions), reward, binary, steps, 0)
    print(f"Job: shape {shape}, conditions {conditions}, reward {reward} each, pipeline {binary[:12]}..")
    tasks = []
    for i, ic in enumerate(conditions):
        params, params_hash = task_spec(input_path, ic, options)
        m = miners[i % len(miners)]
        tid = f"g0-t{i:04d}"
        ledger.create_task(client, "job-1", tid, params_hash, 0, params=params, assigned=m["pubkey"])
        tasks.append({"id": tid, "ic": ic, "miner": m, "params_hash": params_hash})
        print(f"  {tid}: condition {ic} (M {params[2]:.4f}, AoA {params[3]:.3f}) -> {m['name']}")

    t_start = time.time()
    for t in tasks:   # local miners compute in this process, one task at a time
        m = t["miner"]
        if m["remote"]:
            continue
        res = fake_result(t["id"], m["pubkey"]) if m["lazy"] else \
            runner(input_path, t["ic"], options, f"{m['name']}-shape{shape:04d}", n_proc)
        t["res"], t["hash"] = res, result_hash(t["id"], res)
        ledger.submit_result(m["wallet"], t["id"], t["hash"], result=result_values(res))
        print(f"  {t['id']} <- {m['name']}: {describe(res)}{'  (faked)' if m['lazy'] else ''}")
    collect_remote(ledger, client, tasks, remote_timeout)
    compute_seconds = time.time() - t_start

    # Audits: one task per miner at least, picked by the committed seed, plus --audit-rate
    todo = []
    for m in miners:
        theirs = [t for t in tasks if t["miner"] is m and t["hash"]]
        if theirs:
            todo.append(min(theirs, key=lambda t: audit_draw(audit_seed, t["id"])))
    todo += [t for t in tasks if t["hash"] and t not in todo and audit_draw(audit_seed, t["id"]) < audit_rate]
    caught = set()
    while todo:
        t = todo.pop(0)
        if "audit" in t:
            continue
        ref = runner(input_path, t["ic"], options, f"verifier-shape{shape:04d}", n_proc)
        ok = within_tolerance(t["res"], ref)
        verdict_hash = t["hash"] if ok else result_hash(t["id"], ref)
        matched = ledger.resolve_challenge(verifier, t["id"], verdict_hash)
        t["audit"] = {"ok": matched, "verifier": ref}
        if matched:
            print(f"  [audit] {t['id']} ({t['miner']['name']}) within tolerance: "
                  f"dCL {ref['CL'] - t['res']['CL']:+.1e}, dCD {(ref['CD'] - t['res']['CD']) * 1e4:+.2f} counts")
        else:
            acc = ledger.miner_account(t["miner"]["pubkey"])
            print(f"  [FRAUD] {t['id']} by {t['miner']['name']}: claimed {describe(t['res'])}; "
                  f"verifier {describe(ref)}. Slashed -> stake {acc['stake']}"
                  f"{'  >>> BANNED' if acc['status'] == 'banned' else ''}")
            t["res"] = ref   # the verifier's result replaces the fake
            caught.add(t["miner"]["name"])
            todo += [o for o in tasks if o["miner"] is t["miner"] and "audit" not in o and o["hash"] and o not in todo]

    paid = sum(ledger.settle_task(client, t["id"]) for t in tasks if t["hash"])
    refund = ledger.close_job(client, "job-1")
    report = build_report(ledger, case_dir, input_path, tasks, miners, binary, audit_seed, commitment, paid, refund,
                          compute_seconds)
    if log_dir:
        with open(os.path.join(log_dir, "superwing_report.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        participants = {client.pubkey: {"role": "client", "name": "client"},
                        verifier.pubkey: {"role": "verifier", "name": "verifier"}}
        for m in miners:
            participants[m["pubkey"]] = {"role": "miner", "name": m["name"], "honest": not m["lazy"],
                                         "remote": m["remote"]}
        with open(os.path.join(log_dir, "participants.json"), "w", encoding="utf-8") as f:
            json.dump({"cluster": ledger.cluster, "program_id": ledger.program_id, **getattr(ledger, "meta", {}),
                       "participants": participants}, f, indent=2)
    print_report(report)
    return report


def build_report(ledger, case_dir, input_path, tasks, miners, binary, audit_seed, commitment, paid, refund,
                 compute_seconds):
    with open(input_path) as f:
        case = json.load(f)
    area = planform_area(case, case["rootadj"])
    ref_path = os.path.join(case_dir, "reference.json")
    reference = {}
    if os.path.exists(ref_path):
        with open(ref_path) as f:
            reference = json.load(f)
    scale = area / reference["area_ref"] if reference.get("area_ref") else 1.0
    cluster = {c["ic"]: c for c in reference.get("conditions", [])}
    rows = []
    for t in tasks:
        res, ref = t.get("res") or {}, cluster.get(t["ic"], {})
        row = {"task": t["id"], "ic": t["ic"], "miner": t["miner"]["name"], "status": ledger.task_account(t["id"])["status"],
               "audited": "audit" in t, "audit_ok": t.get("audit", {}).get("ok"), "result_hash": t["hash"]}
        if res.get("status") == "ok":
            row.update(CL=res["CL"], CD=res["CD"], n_step=res["n_step"], CL_cluster_area=res["CL"] * scale,
                       CD_cluster_area=res["CD"] * scale)
            if "CL" in ref:
                row.update(ref_CL=ref["CL"], ref_CD=ref["CD"], dCL=res["CL"] * scale - ref["CL"],
                           dCD=res["CD"] * scale - ref["CD"])
        rows.append(row)
    snap = ledger.snapshot()
    return {"cluster": ledger.cluster, "program_id": ledger.program_id, "config": getattr(ledger, "meta", {}).get("config"),
            "case": os.path.basename(os.path.normpath(case_dir)), "pipeline_hash": binary,
            "audit_seed": audit_seed, "audit_commitment": commitment, "area_scale": scale,
            "tolerances": {"CL": CL_TOL, "CD": CD_TOL}, "compute_seconds": compute_seconds,
            "paid": paid, "refund": refund, "tasks": rows,
            "miners": {m["name"]: {k: snap["miners"].get(m["pubkey"], {}).get(k) for k in
                                   ("stake", "earned", "slashed", "caught", "status")} for m in miners}}


def print_report(r):
    print(f"\n=== SuperWing job on {r['cluster']} ({r['case']}) ===")
    print(f"{'task':<9} {'cond':>4} {'miner':<12} {'CL':>9} {'cluster':>9} {'dCL':>9} {'CD':>9} {'cluster':>9} "
          f"{'dCD,cnt':>8}  audit     status")
    for t in r["tasks"]:
        audit = "-" if not t["audited"] else "ok" if t["audit_ok"] else "FRAUD"
        line = f"{t['task']:<9} {t['ic']:>4} {t['miner']:<12} "
        if "dCL" in t:
            line += (f"{t['CL_cluster_area']:>9.5f} {t['ref_CL']:>9.5f} {t['dCL']:>+9.5f} "
                     f"{t['CD_cluster_area']:>9.6f} {t['ref_CD']:>9.6f} {t['dCD'] * 1e4:>+8.2f}  ")
        else:
            line += " " * 66
        print(line + f"{audit:<9} {t['status']}")
    print("(CL and CD on the cluster's reference area; after a caught fake, the verifier's numbers)")
    for name, m in r["miners"].items():
        print(f"  {name:<12} earned {m['earned']}  stake {m['stake']}  slashed {m['slashed']}  {m['status']}")
    print(f"Paid {r['paid']}, refunded {r['refund']}. Audit seed: {r['audit_seed']}")
    if r["cluster"] != "mock":
        print(f"Network: https://explorer.solana.com/address/{r['config']}?cluster={r['cluster']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--case", required=True, help="case directory with input.json (and reference.json)")
    ap.add_argument("--conditions", required=True, help="comma-separated condition indices, e.g. 1,3,5")
    ap.add_argument("--adflow-options", default='{"useANKSolver": false, "nCycles": 1500}',
                    help="solver options, part of every task's definition")
    ap.add_argument("--ledger", default="mock", choices=["mock", "devnet"])
    ap.add_argument("--local", type=int, default=0, help="miners in this process")
    ap.add_argument("--lazy", type=int, default=0, help="how many of the local miners fake their results")
    ap.add_argument("--remote", type=int, default=0, help="miner nodes to wait for (miner_node.py --superwing)")
    ap.add_argument("--np", type=int, default=None, help="MPI processes per computation (default: all CPUs)")
    ap.add_argument("--reward", type=int, default=100, help="tokens per task")
    ap.add_argument("--audit-rate", type=float, default=0.0, help="extra audit probability per task")
    ap.add_argument("--window", type=int, default=10, help="challenge window, seconds (devnet)")
    ap.add_argument("--remote-timeout", type=int, default=8 * 3600)
    args = ap.parse_args()
    sys.stdout.reconfigure(line_buffering=True)

    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "network_logs",
                           f"superwing-{time.strftime('%Y%m%d-%H%M%S')}")
    os.makedirs(log_dir, exist_ok=True)
    opts = {"challenge_window": args.window} if args.ledger == "devnet" else {}
    ledger = create_ledger(args.ledger, log_dir=log_dir, **opts)
    try:
        run_job(ledger, os.path.abspath(args.case), [int(x) for x in args.conditions.split(",")],
                json.loads(args.adflow_options), local=args.local, lazy=args.lazy, remote=args.remote,
                n_proc=args.np, reward=args.reward, audit_rate=args.audit_rate, remote_timeout=args.remote_timeout,
                log_dir=log_dir)
    finally:
        ledger.close()
    print(f"Logs: {log_dir}")


if __name__ == "__main__":
    main()

"""Devnet helper: deployment config, local wallets, balances, funding.

    python orchestrator_py/devnet.py setup <PROGRAM_ID>   # once, after deploying in Solana Playground
    python orchestrator_py/devnet.py status               # addresses and balances of the local wallets
    python orchestrator_py/devnet.py airdrop [SOL]        # ask the devnet faucet for SOL (rate-limited)
    python orchestrator_py/devnet.py fund <PUBKEY> <SOL>  # send SOL from the client wallet (e.g. to a remote miner)

Wallets are keypair files in .keys/ (gitignored): client, verifier, miner-N, node-<name>.
"""
import argparse
import glob
import json
import os
import sys

from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer

from network.solana_ledger import DEPLOYMENT, KEYS_DIR, SolanaWallet, load_deployment, load_keypair
from network.solana_rpc import DEVNET, RpcClient, RpcError

FAUCET = "https://faucet.solana.com"


def cmd_setup(args):
    rpc = RpcClient(args.rpc)
    got = rpc.call("getAccountInfo", [args.program_id, {"encoding": "base64"}])["value"]
    if not got or not got.get("executable"):
        sys.exit(f"{args.program_id} is not a deployed program on {args.rpc}")
    os.makedirs(os.path.dirname(DEPLOYMENT), exist_ok=True)
    with open(DEPLOYMENT, "w") as f:
        json.dump({"cluster": "devnet", "rpc": args.rpc, "program_id": args.program_id}, f, indent=2)
    client = load_keypair("client")
    print(f"Saved {DEPLOYMENT}")
    print(f"Client wallet: {client.pubkey()}  balance {rpc.balance(client.pubkey()) / 1e9:.3f} SOL")
    print(f"Fund it with ~1 SOL: python orchestrator_py/devnet.py airdrop  (or {FAUCET})")


def cmd_status(args):
    dep = load_deployment()
    rpc = RpcClient(dep["rpc"])
    print(f"Program {dep['program_id']} on {dep['cluster']}")
    for path in sorted(glob.glob(os.path.join(KEYS_DIR, "*.json"))):
        label = os.path.splitext(os.path.basename(path))[0]
        pk = load_keypair(label, create=False).pubkey()
        print(f"  {label:<12} {pk}  {rpc.balance(pk) / 1e9:.4f} SOL")


def cmd_airdrop(args):
    dep = load_deployment()
    rpc = RpcClient(dep["rpc"])
    pk = load_keypair("client").pubkey()
    try:
        sig = rpc.airdrop(pk, int(args.sol * 1e9))
        rpc.wait([sig])
        print(f"Airdropped {args.sol} SOL to {pk}; balance {rpc.balance(pk) / 1e9:.3f} SOL")
    except RpcError as e:
        print(f"The RPC faucet refused ({e}).\nUse the web faucet instead: {FAUCET}  address: {pk}")


def cmd_fund(args):
    dep = load_deployment()
    rpc = RpcClient(dep["rpc"])
    client = SolanaWallet(load_keypair("client"), "client")
    from solders.message import Message
    from solders.transaction import Transaction
    ix = transfer(TransferParams(from_pubkey=client.keypair.pubkey(), to_pubkey=Pubkey.from_string(args.pubkey),
                                 lamports=int(args.sol * 1e9)))
    bh = rpc.latest_blockhash()
    tx = Transaction([client.keypair], Message.new_with_blockhash([ix], client.keypair.pubkey(), bh), bh)
    rpc.wait([rpc.send(bytes(tx))])
    print(f"Sent {args.sol} SOL to {args.pubkey}; its balance is {rpc.balance(args.pubkey) / 1e9:.4f} SOL")


def main():
    ap = argparse.ArgumentParser(description="DeCFD devnet helper")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("setup", help="save the deployed program id")
    p.add_argument("program_id")
    p.add_argument("--rpc", default=DEVNET)
    p.set_defaults(fn=cmd_setup)
    sub.add_parser("status", help="wallet addresses and balances").set_defaults(fn=cmd_status)
    p = sub.add_parser("airdrop", help="request devnet SOL for the client wallet")
    p.add_argument("sol", nargs="?", type=float, default=1.0)
    p.set_defaults(fn=cmd_airdrop)
    p = sub.add_parser("fund", help="send SOL from the client wallet")
    p.add_argument("pubkey")
    p.add_argument("sol", type=float)
    p.set_defaults(fn=cmd_fund)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()

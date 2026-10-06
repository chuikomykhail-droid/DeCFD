"""Build a self-contained miner package for Windows machines without Python.

    python orchestrator_py/package_miner.py --machines 4 --fund 0.05

Creates one devnet wallet per machine (keys node-laptop1..N in .keys/), funds each from the
client wallet, freezes miner_node.py with PyInstaller and assembles a folder + zip:

    decfd-miner/
      start.bat          double-click, type the machine number
      miner.exe + _internal/
      worker.exe, vcomp140.dll   the solver and its OpenMP runtime
      deployment.json    program id, RPC, coordinator address
      selftest.json      reference hashes from this machine
      keys/              node-laptop1..N.json (devnet only!)
"""
import argparse
import glob
import json
import os
import shutil
import subprocess
import sys

from solders.message import Message
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import Transaction

from network.solana_ledger import DEPLOYMENT, SolanaWallet, load_deployment, load_keypair
from network.solana_rpc import RpcClient
from worker_runner import WORKER_PATH

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
HERE = os.path.dirname(os.path.abspath(__file__))
VS_REDIST = r"D:\Soft\Visual Studio\VC\Redist\MSVC"
CONDA_DLLS = ["libssl-3-x64.dll", "libcrypto-3-x64.dll", "ffi.dll", "ffi-8.dll", "libbz2.dll", "liblzma.dll",
              "libexpat.dll"]

START_BAT = r"""@echo off
cd /d "%~dp0"
title DeCFD miner
echo DeCFD miner node
set /p N=Machine number (1-{n}):
miner.exe --name laptop%N%
"""

README = """DeCFD miner node
================

1. Copy this folder to the machine (any location, e.g. the desktop).
2. Double-click start.bat and type this machine's number (1-{n}).
   Windows may say "Windows protected your PC": click "More info" -> "Run anyway".
3. The miner checks that this computer reproduces the reference results bit for bit,
   then waits for the coordinator to start a run and joins it on its own.
4. Leave it running. Close the window to stop.

The keys in keys/ are devnet test wallets, worthless outside Solana devnet.
"""


def find_vcomp():
    hits = [p for p in glob.glob(os.path.join(VS_REDIST, "*", "x64", "*OpenMP*", "vcomp140.dll"))]
    if not hits:
        sys.exit("vcomp140.dll not found under the Visual Studio redist folder")
    return sorted(hits)[-1]


def fund(rpc, client, pubkey, sol):
    have = rpc.balance(pubkey)
    want = int(sol * 1e9)
    if have >= want:
        return have
    ix = transfer(TransferParams(from_pubkey=client.keypair.pubkey(), to_pubkey=Pubkey.from_string(pubkey),
                                 lamports=want - have))
    bh = rpc.latest_blockhash()
    tx = Transaction([client.keypair], Message.new_with_blockhash([ix], client.keypair.pubkey(), bh), bh)
    rpc.wait([rpc.send(bytes(tx))])
    return rpc.balance(pubkey)


def main():
    ap = argparse.ArgumentParser(description="Build the DeCFD miner package")
    ap.add_argument("--machines", type=int, default=4)
    ap.add_argument("--fund", type=float, default=0.05, help="SOL per machine wallet")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(ROOT), "decfd-miner"))
    args = ap.parse_args()

    dep = load_deployment()
    rpc = RpcClient(dep["rpc"])
    client = SolanaWallet(load_keypair("client", create=False), "client")
    selftest = os.path.join(os.path.dirname(DEPLOYMENT), "selftest.json")
    if not os.path.exists(selftest):
        sys.exit("solana/selftest.json missing: run `python orchestrator_py/miner_node.py --make-selftest`")

    print("Wallets:")
    names = [f"laptop{i}" for i in range(1, args.machines + 1)]
    for name in names:
        kp = load_keypair(f"node-{name}")
        bal = fund(rpc, client, str(kp.pubkey()), args.fund)
        print(f"  {name}: {kp.pubkey()}  {bal / 1e9:.4f} SOL")

    print("Freezing miner_node.py with PyInstaller...")
    build = os.path.join(os.path.dirname(ROOT), "decfd-miner-build")
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--console",
                    "--name", "miner", "--paths", HERE, "--distpath", os.path.join(build, "dist"),
                    "--workpath", os.path.join(build, "work"), "--specpath", build,
                    os.path.join(HERE, "miner_node.py")], check=True, capture_output=True)

    if os.path.isdir(args.out):
        shutil.rmtree(args.out)
    shutil.copytree(os.path.join(build, "dist", "miner"), args.out)
    # conda keeps the DLLs of _ssl, _ctypes, _bz2, _lzma, pyexpat in Library/bin, which PyInstaller
    # misses (they resolve through PATH on the build machine): without them HTTPS fails on a clean PC
    conda_bin = os.path.join(sys.prefix, "Library", "bin")
    for dll in CONDA_DLLS:
        if os.path.exists(os.path.join(conda_bin, dll)):
            shutil.copy2(os.path.join(conda_bin, dll), os.path.join(args.out, "_internal"))
    shutil.copy2(WORKER_PATH, os.path.join(args.out, "worker.exe"))
    shutil.copy2(find_vcomp(), os.path.join(args.out, "vcomp140.dll"))
    shutil.copy2(selftest, os.path.join(args.out, "selftest.json"))
    with open(os.path.join(args.out, "deployment.json"), "w") as f:
        json.dump({**dep, "coordinator": client.pubkey}, f, indent=2)
    os.makedirs(os.path.join(args.out, "keys"))
    for name in names:
        shutil.copy2(os.path.join(ROOT, ".keys", f"node-{name}.json"), os.path.join(args.out, "keys"))
    with open(os.path.join(args.out, "start.bat"), "w", newline="\r\n") as f:
        f.write(START_BAT.format(n=args.machines))
    with open(os.path.join(args.out, "README.txt"), "w", newline="\r\n") as f:
        f.write(README.format(n=args.machines))

    archive = shutil.make_archive(args.out, "zip", os.path.dirname(args.out), os.path.basename(args.out))
    size = sum(os.path.getsize(os.path.join(d, x)) for d, _, fs in os.walk(args.out) for x in fs)
    print(f"Package: {args.out} ({size / 1e6:.0f} MB)\nZip: {archive} ({os.path.getsize(archive) / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()

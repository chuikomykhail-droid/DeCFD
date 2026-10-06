"""SolanaLedger's transaction plumbing against a fake RPC: the failures seen on the public
devnet endpoint (a blockhash unknown to the simulating node, a dropped transaction)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "orchestrator_py"))

from solders.hash import Hash  # noqa: E402
from solders.keypair import Keypair  # noqa: E402
from solders.system_program import TransferParams, transfer  # noqa: E402

from network.solana_ledger import SolanaLedger, SolanaWallet  # noqa: E402
from network.solana_rpc import RpcError  # noqa: E402


class FakeRpc:
    def __init__(self, unknown_blockhash=0, drop=0):
        self.unknown_blockhash = unknown_blockhash   # first N sends fail with BlockhashNotFound
        self.drop = drop                             # first N accepted transactions never land
        self.sent, self.landed, self.expired = [], set(), set()

    def latest_blockhash(self):
        return Hash.new_unique()

    def send(self, tx_bytes):
        if self.unknown_blockhash:
            self.unknown_blockhash -= 1
            raise RpcError("sendTransaction: {'message': 'Transaction simulation failed: Blockhash not found', "
                           "'err': 'BlockhashNotFound'}")
        sig = f"sig{len(self.sent)}"
        self.sent.append(sig)
        if self.drop:
            self.drop -= 1
        else:
            self.landed.add(sig)
        return sig

    def statuses(self, sigs):
        return [{"confirmationStatus": "confirmed", "err": None} if s in self.landed else None for s in sigs]

    def wait(self, sigs, timeout=90):
        missing = [s for s in sigs if s not in self.landed]
        if missing:
            raise RpcError(f"{len(missing)} transaction(s) not confirmed after {timeout}s, e.g. {missing[0]}")

    def call(self, method, params=None):
        assert method == "isBlockhashValid"
        return {"value": False}   # the dropped transaction's blockhash has expired


def ledger_with(rpc):
    led = SolanaLedger.__new__(SolanaLedger)
    led.rpc, led._bh, led._bh_time, led._pending = rpc, None, 0.0, []
    return led


def some_ix(payer):
    return transfer(TransferParams(from_pubkey=payer.keypair.pubkey(), to_pubkey=Keypair().pubkey(), lamports=1))


class SendTest(unittest.TestCase):
    def setUp(self):
        self.payer = SolanaWallet(Keypair(), "payer")

    def test_unknown_blockhash_is_resigned(self):
        rpc = FakeRpc(unknown_blockhash=2)
        led = ledger_with(rpc)
        sig = led._send([some_ix(self.payer)], [], self.payer)
        led._sync()
        self.assertEqual(rpc.sent, [sig])          # sent once, after two rejected simulations
        self.assertEqual(led._pending, [])

    def test_dropped_transaction_is_sent_again_once(self):
        rpc = FakeRpc(drop=1)
        led = ledger_with(rpc)
        for _ in range(3):
            led._send([some_ix(self.payer)], [], self.payer)
        led._sync()
        self.assertEqual(len(rpc.sent), 4)          # three, plus the dropped one again
        self.assertEqual(len(rpc.landed), 3)        # each instruction executed exactly once
        self.assertEqual(led._pending, [])

    def test_other_errors_still_raise(self):
        rpc = FakeRpc()
        rpc.send = lambda tx: (_ for _ in ()).throw(RpcError("sendTransaction: custom program error: 0x1775"))
        with self.assertRaises(Exception):
            ledger_with(rpc)._send([some_ix(self.payer)], [], self.payer)


if __name__ == "__main__":
    unittest.main()

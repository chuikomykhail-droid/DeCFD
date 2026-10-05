"""The mock program is the spec for the Solana contract, so its rules are tested directly."""
import json
import os
import random
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "orchestrator_py"))

from network.ledger import LedgerError  # noqa: E402
from network.mock_ledger import MockSolanaProgram  # noqa: E402
from network.wallet import MockWallet  # noqa: E402

GOOD, BAD = "a" * 64, "b" * 64


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        rng = random.Random(1)
        self.client, self.verifier, self.alice, self.bob = (
            MockWallet(rng, name) for name in ("client", "verifier", "alice", "bob"))
        self.ledger = MockSolanaProgram(log_dir=self.tmp.name)
        self.ledger.initialize(self.client, self.verifier.pubkey, min_stake=50,
                               slash_bps=5000, verifier_share_bps=5000)
        self.ledger.airdrop(self.client.pubkey, 1000)
        for m in (self.alice, self.bob):
            self.ledger.airdrop(m.pubkey, 100)
            self.ledger.register_miner(m, m.label, 100)
        self.ledger.create_job(self.client, "job", budget=100, reward=10, binary_hash="c" * 64)

    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()

    def submit(self, task_id, miner, result=GOOD):
        self.ledger.create_task(self.client, "job", task_id, "p" * 64, epoch=0)
        self.ledger.submit_result(miner, task_id, result)

    def assertConserved(self):
        self.assertEqual(self.ledger.total_supply_held(), self.ledger.supply)

    def test_honest_task_is_paid_and_budget_refunded(self):
        self.submit("t1", self.alice)
        self.assertTrue(self.ledger.resolve_challenge(self.verifier, "t1", GOOD))
        self.assertEqual(self.ledger.settle_task(self.client, "t1"), 10)
        self.assertEqual(self.ledger.balance(self.alice.pubkey), 10)
        self.assertEqual(self.ledger.close_job(self.client, "job"), 90)
        self.assertEqual(self.ledger.balance(self.client.pubkey), 990)
        self.assertConserved()

    def test_unaudited_task_is_paid_too(self):
        self.submit("t1", self.alice)
        self.assertEqual(self.ledger.settle_task(self.client, "t1"), 10)
        self.assertConserved()

    def test_fraud_is_slashed_and_reward_released(self):
        self.submit("t1", self.bob, result=BAD)
        self.assertFalse(self.ledger.resolve_challenge(self.verifier, "t1", GOOD))
        bob = self.ledger.miner_account(self.bob.pubkey)
        self.assertEqual((bob["stake"], bob["caught"], bob["status"]), (50, 1, "active"))
        self.assertEqual(self.ledger.balance(self.verifier.pubkey), 25)   # 50% of the 50 slashed
        self.assertEqual(self.ledger.treasury, 25)
        self.assertEqual(self.ledger.settle_task(self.client, "t1"), 0)  # rejected: no payout
        self.assertEqual(self.ledger.close_job(self.client, "job"), 100)  # reward went back to the job
        self.assertConserved()

    def test_second_catch_bans_the_miner(self):
        self.submit("t1", self.bob, result=BAD)
        self.submit("t2", self.bob, result=BAD)
        self.ledger.resolve_challenge(self.verifier, "t1", GOOD)
        self.ledger.resolve_challenge(self.verifier, "t2", GOOD)
        bob = self.ledger.miner_account(self.bob.pubkey)
        self.assertEqual((bob["stake"], bob["status"]), (25, "banned"))
        self.ledger.create_task(self.client, "job", "t3", "p" * 64, epoch=1)
        with self.assertRaises(LedgerError):
            self.ledger.submit_result(self.bob, "t3", GOOD)
        self.assertConserved()

    def test_only_authorized_signers(self):
        self.submit("t1", self.alice)
        with self.assertRaises(LedgerError):
            self.ledger.resolve_challenge(self.alice, "t1", GOOD)          # not the verifier
        with self.assertRaises(LedgerError):
            self.ledger.create_task(self.alice, "job", "t2", "p" * 64, 0)  # not the job's client
        with self.assertRaises(LedgerError):
            self.ledger.close_job(self.alice, "job")

    def test_state_machine(self):
        self.ledger.create_task(self.client, "job", "t1", "p" * 64, epoch=0)
        with self.assertRaises(LedgerError):
            self.ledger.settle_task(self.client, "t1")                       # nothing submitted yet
        self.ledger.submit_result(self.alice, "t1", GOOD)
        with self.assertRaises(LedgerError):
            self.ledger.submit_result(self.bob, "t1", GOOD)                  # already taken
        with self.assertRaises(LedgerError):
            self.ledger.close_job(self.client, "job")                        # unsettled task
        self.ledger.settle_task(self.client, "t1")
        with self.assertRaises(LedgerError):
            self.ledger.resolve_challenge(self.verifier, "t1", GOOD)         # already paid

    def test_budget_limits_the_number_of_tasks(self):
        for i in range(10):
            self.ledger.create_task(self.client, "job", f"t{i}", "p" * 64, epoch=0)
        with self.assertRaises(LedgerError):
            self.ledger.create_task(self.client, "job", "t10", "p" * 64, epoch=0)

    def test_event_log(self):
        self.submit("t1", self.alice)
        self.ledger.settle_task(self.client, "t1")
        self.ledger.close()
        with open(os.path.join(self.tmp.name, "ledger_tx.jsonl"), encoding="utf-8") as f:
            events = [json.loads(line) for line in f]
        self.assertEqual([e["ix"] for e in events][-3:], ["create_task", "submit_result", "settle_task"])
        self.assertEqual([e["slot"] for e in events], list(range(1, len(events) + 1)))
        self.assertTrue(all(e["sig"] and e["signer"] for e in events))


if __name__ == "__main__":
    unittest.main()

"""End to end: tasks go through miners, a lazy miner gets caught, the books balance."""
import os
import random
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "orchestrator_py"))

from app import Shape  # noqa: E402
from network import ComputeNetwork  # noqa: E402
from worker_runner import WORKER_PATH  # noqa: E402


@unittest.skipUnless(os.path.exists(WORKER_PATH), "worker binary not built")
class NetworkTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def make_network(self, **kw):
        return ComputeNetwork(n_miners=3, n_cheaters=1, sim_args=["--steps", "200", "--avg", "50"],
                              log_dir=self.tmp.name, verbose=False, **kw)

    def shapes(self, n, seed=0):
        rng = random.Random(seed)
        return [Shape([rng.uniform(5, 15) for _ in range(5)], rng.uniform(0, 8), rng.uniform(0, 3))
                for _ in range(n)]

    def test_full_audit_catches_every_fake(self):
        net = self.make_network(cheat_prob=1.0, verify_rate=1.0)
        net.open_job(budget=100)
        batch = self.shapes(6)
        net.evaluate_batch(batch, gen=0)
        lazy = next(m for m in net.miners if not m.honest)

        self.assertTrue(lazy.faked_tasks)
        self.assertEqual(net.stats["caught"], len(lazy.faked_tasks))
        self.assertTrue(all(s.verified for s in batch))
        self.assertTrue(all(s.corrected for s in batch if s.miner is lazy))
        self.assertEqual(net.ledger.miner_account(lazy.pubkey)["status"], "banned")

        paid = net.finalize_epoch(0)
        honest_tasks = sum(1 for s in batch if s.miner is not lazy)
        self.assertEqual(paid, 10 * honest_tasks)
        self.assertEqual(net.close_job(), 100 - paid)
        self.assertEqual(net.ledger.total_supply_held(), net.ledger.supply)
        net.ledger.close()

    def test_banned_miner_gets_no_more_tasks(self):
        net = self.make_network(cheat_prob=1.0, verify_rate=1.0)
        net.open_job(budget=200)
        net.evaluate_batch(self.shapes(6), gen=0)
        net.finalize_epoch(0)
        second = self.shapes(4, seed=1)
        net.evaluate_batch(second, gen=1)
        self.assertTrue(all(s.miner.honest for s in second))
        net.ledger.close()


if __name__ == "__main__":
    unittest.main()

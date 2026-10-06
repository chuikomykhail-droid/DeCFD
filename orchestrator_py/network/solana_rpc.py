"""Minimal synchronous Solana JSON-RPC client (stdlib HTTP + solders types).

Only what the ledger needs: blockhash, send, statuses, accounts, program accounts, balance,
airdrop. Retries on rate limits (HTTP 429) and transient server errors.
"""
import base64
import json
import time
import urllib.error
import urllib.request

from solders.hash import Hash

DEVNET = "https://api.devnet.solana.com"


class RpcError(RuntimeError):
    pass


class RpcClient:
    def __init__(self, url=DEVNET, retries=6):
        self.url = url
        self.retries = retries
        self._id = 0

    def call(self, method, params=None):
        self._id += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or []}).encode()
        delay = 0.5
        for attempt in range(self.retries):
            req = urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    reply = json.loads(r.read())
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < self.retries - 1:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise RpcError(f"{method}: HTTP {e.code} {e.read()[:200]!r}") from e
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt < self.retries - 1:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise RpcError(f"{method}: {e}") from e
            if "error" in reply:
                raise RpcError(f"{method}: {reply['error']}")
            return reply["result"]
        raise RpcError(f"{method}: no reply")

    # ------------------------------------------------------------------ helpers
    def latest_blockhash(self):
        return Hash.from_string(self.call("getLatestBlockhash", [{"commitment": "confirmed"}])["value"]["blockhash"])

    def send(self, tx_bytes):
        """Send a signed transaction; preflight simulation catches program errors early."""
        return self.call("sendTransaction", [base64.b64encode(tx_bytes).decode(),
                                             {"encoding": "base64", "preflightCommitment": "confirmed"}])

    def statuses(self, signatures):
        out = []
        for i in range(0, len(signatures), 256):
            chunk = signatures[i:i + 256]
            out += self.call("getSignatureStatuses", [chunk, {"searchTransactionHistory": False}])["value"]
        return out

    def account(self, pubkey):
        """(data bytes, lamports) or None."""
        v = self.call("getAccountInfo", [str(pubkey), {"encoding": "base64", "commitment": "confirmed"}])["value"]
        return None if v is None else (base64.b64decode(v["data"][0]), v["lamports"])

    def accounts(self, pubkeys):
        out = []
        keys = [str(k) for k in pubkeys]
        for i in range(0, len(keys), 100):
            vals = self.call("getMultipleAccounts", [keys[i:i + 100],
                                                     {"encoding": "base64", "commitment": "confirmed"}])["value"]
            out += [None if v is None else (base64.b64decode(v["data"][0]), v["lamports"]) for v in vals]
        return out

    def program_accounts(self, program_id, filters):
        """[(pubkey str, data bytes)] for a program, with memcmp/dataSize filters."""
        res = self.call("getProgramAccounts", [str(program_id), {"encoding": "base64", "commitment": "confirmed",
                                                                 "filters": filters}])
        return [(a["pubkey"], base64.b64decode(a["account"]["data"][0])) for a in res]

    def balance(self, pubkey):
        return self.call("getBalance", [str(pubkey), {"commitment": "confirmed"}])["value"]

    def airdrop(self, pubkey, lamports):
        return self.call("requestAirdrop", [str(pubkey), lamports])

    def wait(self, signatures, timeout=90):
        """Block until every signature is confirmed. Returns {sig: status}; raises on a failed tx."""
        pending = list(signatures)
        done = {}
        t0 = time.time()
        while pending:
            for sig, st in zip(pending, self.statuses(pending)):
                if st and st.get("confirmationStatus") in ("confirmed", "finalized"):
                    done[sig] = st
                    if st.get("err"):
                        raise RpcError(f"transaction {sig} failed: {st['err']}")
            pending = [s for s in pending if s not in done]
            if pending:
                if time.time() - t0 > timeout:
                    raise RpcError(f"{len(pending)} transaction(s) not confirmed after {timeout}s, e.g. {pending[0]}")
                time.sleep(0.4)
        return done

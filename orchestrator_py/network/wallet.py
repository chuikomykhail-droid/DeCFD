"""Wallets: the identities that sign ledger instructions.

A wallet only needs two members: `pubkey` (base58, Solana style) and `sign(message)`.
The mock wallet derives its public key from a random secret and "signs" with HMAC-SHA256.
A devnet backend would wrap a real ed25519 keypair (solders.keypair.Keypair) instead.
"""
import hashlib
import hmac

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58encode(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    s = ""
    while n:
        n, r = divmod(n, 58)
        s = _B58[r] + s
    pad = len(b) - len(b.lstrip(b"\0"))
    return "1" * pad + s


def short(pk: str) -> str:
    return f"{pk[:4]}..{pk[-4:]}"


class MockWallet:
    def __init__(self, rng, label=""):
        self._secret = bytes(rng.getrandbits(8) for _ in range(32))
        self.pubkey = b58encode(hashlib.sha256(self._secret).digest())
        self.label = label

    def sign(self, message: bytes) -> str:
        """64-byte signature, base58-encoded like a Solana transaction signature."""
        mac = hmac.new(self._secret, message, hashlib.sha512).digest()
        return b58encode(mac)

    def __repr__(self):
        return f"MockWallet({self.label or short(self.pubkey)})"

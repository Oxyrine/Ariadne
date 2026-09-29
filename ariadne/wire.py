"""Frozen wire protocol shared by every lane: identities, hashing, commitment, EIP-712.

Anything here changing is a WIRE_PROTOCOL.md version bump.
"""
import hashlib
import hmac
import json
import re
from datetime import date, datetime, timezone

from eth_utils import keccak

# Synthetic demo secret. In production this key is held by the consortium, never published.
CONSORTIUM_SECRET = b"ariadne-synthetic-consortium-secret-v1"

STATES = ["NONE", "REGISTERED", "FINANCED", "POOLED", "SETTLED", "DEFAULTED"]
OPS = ["ADD", "REMOVE", "SUBSTITUTE_OUT", "SUBSTITUTE_IN", "SETTLED", "DEFAULTED"]
OP = {name: i for i, name in enumerate(OPS)}
DAY = 86400
ZERO32 = b"\x00" * 32


# ------------------------------------------------------------------ identities

def _squash(s: str) -> str:
    return re.sub(r"\s+", "", str(s)).upper()


def normalise_invoice_no(s: str) -> str:
    """Case, spacing and leading zeros in numeric segments do not change identity."""
    return re.sub(r"(?<![0-9])0+(?=[0-9])", "", _squash(s))


def unit_id(platform: str, unit_no: str) -> bytes:
    return keccak(f"{_squash(platform)}:{_squash(unit_no)}".encode())


def receivable_key(seller_gstin, buyer_gstin, invoice_no, invoice_date, amount_paise) -> bytes:
    """Canonical cross-platform fingerprint of the economic invoice, keyed with the consortium secret."""
    msg = "|".join([_squash(seller_gstin), _squash(buyer_gstin), normalise_invoice_no(invoice_no),
                    to_date(invoice_date).isoformat(), str(int(amount_paise))])
    return hmac.new(CONSORTIUM_SECRET, b"receivable|" + msg.encode(), hashlib.sha256).digest()


def party_key(gstin: str) -> bytes:
    """Keyed hash of a GSTIN, so identifiers cannot be brute-forced from chain data."""
    return hmac.new(CONSORTIUM_SECRET, b"party|" + _squash(gstin).encode(), hashlib.sha256).digest()


def to_date(d) -> date:
    return d if isinstance(d, date) else date.fromisoformat(str(d))


def epoch(d) -> int:
    d = to_date(d)
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


def hx(b: bytes) -> str:
    return "0x" + bytes(b).hex()


def unhx(s: str) -> bytes:
    return bytes.fromhex(s[2:] if s.startswith("0x") else s)


# ------------------------------------------------------------------- hashing

def canonical(obj) -> bytes:
    """RFC 8785 JSON canonicalisation for the value types the protocol allows.

    ponytail: JCS subset. Keys are ASCII and floats are banned, so sorted-key compact
    json.dumps is byte-identical to JCS here. Use a full JCS library if floats are ever allowed.
    """
    def walk(v):
        if isinstance(v, float):
            raise ValueError("floats are not allowed in hashed documents; use paise or bps integers")
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(obj)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def content_hash(obj) -> bytes:
    return keccak(canonical(obj))


def rules_hash(ruleset: dict) -> bytes:
    return content_hash({k: v for k, v in ruleset.items() if k not in ("signedBy", "signature")})


def next_commitment(prev: bytes, op: int, uid: bytes) -> bytes:
    """C_n = keccak256(C_{n-1} || uint8(op) || unitId), identical to the contract."""
    return keccak(prev + bytes([op]) + uid)


# ------------------------------------------------------------------- EIP-712

def attestation_typed(chain_id, ledger, pool_id, uid, rkey, rhash, version) -> dict:
    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"}, {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"}, {"name": "verifyingContract", "type": "address"},
            ],
            "Attestation": [
                {"name": "poolId", "type": "bytes32"}, {"name": "unitId", "type": "bytes32"},
                {"name": "receivableKey", "type": "bytes32"}, {"name": "rulesHash", "type": "bytes32"},
                {"name": "manifestVersion", "type": "uint32"},
            ],
        },
        "primaryType": "Attestation",
        "domain": {"name": "Ariadne", "version": "1", "chainId": chain_id, "verifyingContract": ledger},
        "message": {"poolId": pool_id, "unitId": uid, "receivableKey": rkey,
                    "rulesHash": rhash, "manifestVersion": version},
    }


def ruleset_typed(ruleset: dict) -> dict:
    """Human sign-off over a rule set. Chain-independent so the file can be signed before deployment."""
    return {
        "types": {
            "EIP712Domain": [{"name": "name", "type": "string"}, {"name": "version", "type": "string"}],
            "RuleSet": [
                {"name": "ruleSetId", "type": "string"}, {"name": "version", "type": "uint32"},
                {"name": "rulesHash", "type": "bytes32"},
            ],
        },
        "primaryType": "RuleSet",
        "domain": {"name": "Ariadne Rules", "version": "1"},
        "message": {"ruleSetId": ruleset["ruleSetId"], "version": ruleset["version"],
                    "rulesHash": rules_hash(ruleset)},
    }


def pool_id(name: str) -> bytes:
    return keccak(f"pool:{name}".encode())

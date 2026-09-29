"""Local Anvil chain, contract deployment and a thin transaction wrapper with revert decoding."""
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from eth_abi import decode
from eth_account import Account
from eth_utils import keccak
from web3 import Web3

ROOT = Path(__file__).resolve().parent.parent
MNEMONIC = "test test test test test test test test test test test junk"  # Anvil's public dev mnemonic
NAMES = ["admin", "P1", "P2", "P3", "A", "B", "C", "engine", "rules"]
FOUNDRY = Path.home() / ".foundry" / "bin"
Account.enable_unaudited_hdwallet_features()


def _tool(name):
    return shutil.which(name) or str(FOUNDRY / name)


def keys():
    return {n: Account.from_mnemonic(MNEMONIC, account_path=f"m/44'/60'/0'/0/{i}").key.hex()
            for i, n in enumerate(NAMES)}


def start_anvil(anchor_ts: int, port: int = 8545):
    p = subprocess.Popen([_tool("anvil"), "--port", str(port), "--timestamp", str(anchor_ts), "--silent"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    w3 = Web3(Web3.HTTPProvider(f"http://127.0.0.1:{port}"))
    for _ in range(50):
        if w3.is_connected():
            return p
        time.sleep(0.2)
    p.kill()
    raise RuntimeError("anvil did not start")


def build_contracts():
    subprocess.run([_tool("forge"), "build", "--root", str(ROOT)], check=True, capture_output=True)
    art = json.loads((ROOT / "out/AriadneLedger.sol/AriadneLedger.json").read_text())
    return art["abi"], art["bytecode"]["object"]


class Revert(Exception):
    def __init__(self, name, args):
        super().__init__(f"{name}{tuple(args)}")
        self.name, self.args_ = name, args


class Chain:
    def __init__(self, rpc, ledger=None, abi=None):
        self.w3 = Web3(Web3.HTTPProvider(rpc))
        self.rpc = rpc
        self.abi = abi or json.loads((ROOT / "out/AriadneLedger.sol/AriadneLedger.json").read_text())["abi"]
        self.acct = {n: a.address for n, a in
                     ((n, Account.from_mnemonic(MNEMONIC, account_path=f"m/44'/60'/0'/0/{i}"))
                      for i, n in enumerate(NAMES))}
        self.key = keys()
        self.names = {v: k for k, v in self.acct.items()}
        self.errors = {}
        for e in (x for x in self.abi if x["type"] == "error"):
            sig = f"{e['name']}({','.join(i['type'] for i in e['inputs'])})"
            self.errors[keccak(sig.encode())[:4].hex()] = (e["name"], [i["type"] for i in e["inputs"]])
        self.contract = self.w3.eth.contract(address=ledger, abi=self.abi) if ledger else None

    @property
    def chain_id(self):
        return self.w3.eth.chain_id

    def deploy(self):
        abi, bytecode = build_contracts()
        c = self.w3.eth.contract(abi=abi, bytecode=bytecode)
        r = self.w3.eth.wait_for_transaction_receipt(
            c.constructor(self.acct["engine"]).transact({"from": self.acct["admin"]}))
        self.contract = self.w3.eth.contract(address=r.contractAddress, abi=abi)
        for p in ("P1", "P2", "P3"):
            self.tx(self.contract.functions.setRegistrar(self.acct[p], True), "admin")
        return self.contract.address

    def decode_revert(self, exc):
        m = re.search(r"0x([0-9a-fA-F]{8,})", " ".join([str(exc)] + [str(a) for a in exc.args]
                                                        + [str(getattr(exc, "data", ""))]))
        if m:
            data = m.group(1).lower()
            if data[:8] in self.errors:
                name, types = self.errors[data[:8]]
                args = decode(types, bytes.fromhex(data[8:])) if types else ()
                return Revert(name, [("0x" + a.hex()) if isinstance(a, bytes) else a for a in args])
        return None

    def tx(self, fn, who):
        try:
            h = fn.transact({"from": self.acct[who]})
        except Exception as e:  # web3 surfaces a custom error as a ContractCustomError / logic error
            r = self.decode_revert(e)
            raise r if r else e
        return self.w3.eth.wait_for_transaction_receipt(h)

    def ts(self):
        return self.w3.eth.get_block("latest").timestamp

    def warp(self, seconds):
        self.w3.provider.make_request("evm_increaseTime", [int(seconds)])
        self.w3.provider.make_request("evm_mine", [])

    def unit(self, uid):
        u = self.contract.functions.units(uid).call()
        f = ["receivableKey", "registrar", "owner", "state", "currentPoolId", "invoiceDate", "dueDate",
             "amountPaise", "buyerKey", "sellerKey", "transferCount"]
        return dict(zip(f, u))

    def snapshot(self, uids=(), pools=()):
        """Everything an attack could move: for the 'nothing changed' proof."""
        return {"block": self.w3.eth.block_number,
                "units": {("0x" + u.hex())[:10]: {k: v for k, v in self.unit(u).items()
                                                    if k in ("owner", "state", "currentPoolId")} for u in uids},
                "pools": {("0x" + p.hex())[:10]: list(self.contract.functions.pools(p).call())[4:7] for p in pools}}

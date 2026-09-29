# Ariadne

**Trace every receivable. Pin every rule. Prove every pool.**

Ariadne is an independent pool-integrity layer for TReDS receivables, built for Bit N Build '26 (PSN024, Open FinTech & Inclusion). It lets a trustee or investor verify that every receivable in a pool is owned by the pool's seller, sits in no other pool, and still satisfies the pool's signed rules after every re-discount, settlement and substitution.

> If the invoice isn't yours, it can't go in the pool.

**All data in this repo is synthetic.** GSTINs are well-formed but fictional. Every number below came from running the system.

## What it claims, and what it does not

An originator cannot place a receivable into a pool unless it is the receivable's current owner, the receivable is in no other live pool, and an authorised eligibility attestation accompanies it. Any investor can recompute every attestation from the ledger and the signed rules and detect one that should not have been issued, independently of the originator and Ariadne's backend once registrar data has entered the ledger.

It does **not** claim that invoices reflect real goods, that it detects fraud, or that it replaces CERSAI, MonetaGo or the TReDS platforms. Loop and group findings are evidence for a human, never verdicts. The ledger prevents ownership and double-pooling violations; for eligibility it requires an attestation and makes wrong ones detectable, not impossible (a stolen engine key can attest a bad unit; the verifier then flags it).

## Run it

Requires Python 3.11+, [Foundry](https://getfoundry.sh) (`forge`, `anvil`), and `pip install -r requirements.txt`.

```bash
git submodule update --init          # forge-std
forge build                           # the verifier reads the ABI from out/
forge test                            # contract attack tests + invariants
python -m pytest tests -q             # engine, evidence, wire, and the full end-to-end demo
python -m ariadne demo --seed A       # the whole story against a local Anvil chain
python -m ariadne demo --seed A --keep   # leave the chain up for live attacks
python -m ariadne metrics             # detection metrics on seeds A/B/C + loop benchmark
```

With a chain running: `ariadne attack stale-owner|duplicate|double-pool|bad-swap [--force]`, `ariadne lifecycle`,
`ariadne verify runs/A/pool.json --rpc URL --report report.html`, `ariadne trace runs/A/pool.json P1:FU-000183`.

## What the demo shows (seed A)

| Step | Result |
| --- | --- |
| Hero invoice FU-000183 (₹4.85 lakh) | Registered, financed by A, re-discounted to B, pooled: every step an on-chain event |
| B pools a unit it re-discounted to C | Reverts `NotOwner`, state unchanged |
| Second platform registers the same invoice, reformatted | Reverts `DuplicateReceivable`, state unchanged |
| B adds a unit already live in Pool 1 to Pool 2 | Reverts `AlreadyEncumbered`, state unchanged |
| Build pool from 186 units | 149 pass unit rules; fabricated ring held for review and excluded; four-GSTIN group resolved and trimmed to 8.9%; 111 members sealed |
| `ariadne verify` | `VERIFIED`: commitment replay (111 events), ownership 111/111, unique 111/111, attestations 111/111 agree |
| 20 settlements, 1 good substitution (manifest v2), 1 default | Still `VERIFIED` (134 events replayed) |
| Swap that breaks the 10% cap | Engine refuses (group at 1,597 bps). Forced with the engine key, the contract accepts it and the verifier turns red, naming unit, rule and block |

## Architecture

| Layer | Where | Decides |
| --- | --- | --- |
| 1. Lineage ledger | `contracts/src/AriadneLedger.sol` | Ownership, one live pool per `receivableKey`, membership commitment (computed on-chain) |
| 2. Eligibility engine | `ariadne/engine.py` | Pure functions over a signed rule set; the verifier imports the same code |
| 3. Evidence scoring | `ariadne/evidence.py` | Nothing. Tarjan SCC + bounded cycle search with a feature breakdown; union-find buyer groups |
| 4. Prospectus compiler | not built | Optional in the prototype; a hand-signed rule file takes its place |
| 5. Investor verifier | `ariadne/verifier.py`, `report.py` | Nothing. Replays chain events and recomputes every check |

Supporting: `wire.py` (identities, hashing, EIP-712), `generate.py` (seeded data), `originator.py` (replay, pool build, attacks, lifecycle), `chain.py` (Anvil + revert decoding), `metrics.py`. Frozen interface and deviations from the spec: [WIRE_PROTOCOL.md](WIRE_PROTOCOL.md).

## Tests

- Foundry: 12 attack and unit tests, plus invariant tests for spec invariants 1-7 (stateful fuzzing with hostile calls). A planted mutation (`livePoolOf` not cleared) is caught by the invariants.
- pytest (25): one test per rule kind, fingerprint normalisation, JCS behaviour, evidence scoring on seeds A and C, generator determinism, and the full demo end to end on a temporary Anvil chain.

## Detection metrics (synthetic, measured, cut-off 700)

| Metric | Seed A (demo) | Seed B (unseen) | Seed C (adversarial) |
| --- | --- | --- | --- |
| Fabricated-loop recall | 5/5 | 5/5 | 0/5 |
| Legitimate-loop false positives | 0/4 | 0/4 | 0/4 |
| Entity-resolution precision (pairs) | 16/16 | 16/16 | n/a (no merges) |
| Entity-resolution recall (pairs) | 16/16 | 16/16 | 0/6 |

Seed C is built to beat the heuristics: an asymmetric, slow, old-entity ring, and a group linked only by a shared address (a weak signal that must not auto-merge). It misses both, which is the point. Seeds A and B come from the same generator, so their similar results show the pipeline is not tuned to one draw, not that it generalises to real data. The score weights are hand-set constants; the cut-off is a signed policy choice.

Loop enumeration is the hotspot: on a 30-node strongly connected component it goes from 0.5 ms at 60 edges to 24 ms at 154 edges, where the 2,000-cycle cap starts to truncate. We bound cycle length at 6 and report the benchmark rather than claim linear scaling.

## Known limits (deliberate)

- Synthetic data only. No real CERSAI, GSTN or MonetaGo integration. The fingerprint is our own keyed hash; production would consume the registry layer's, including partial matching.
- The consortium secret is synthetic and public in `ariadne/wire.py`. A real deployment holds it privately and restricts chain reads.
- A registrar that lies about its own units is trusted; damage is contained to that platform's units.
- The verifier's attestation check re-runs the engine on each entry. The contract already rejects signatures that are not from the engine key, so the verifier does not decode calldata.
- The prospectus compiler (AI layer), the polished UI and testnet deployment are not built.

## Honesty notes

No code is carried over from any earlier project. Patterns are reimplemented and credited: **Circe** (SCC, depth-limited cycle search, discrimination features), **Lumine** (entity resolution across identifiers), **Aether** (eligibility schedules as code), **ManifestGuard** (contract enforces the boundary; verifier trusts only chain data), **Rebound** (typed boundary). Commit history reflects when work was done.

Spec: Ariadne v1.2, Bit N Build '26.

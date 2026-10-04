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
python -m ariadne serve --open        # the web UI (start here): http://127.0.0.1:8000
python -m ariadne demo --seed A       # the same story as a terminal run
python -m ariadne export              # record a live run into site/ (the hosted read-only copy)
python -m ariadne demo --seed A --keep   # leave the chain up for live attacks
python -m ariadne metrics             # detection metrics on seeds A/B/C + loop benchmark
```

With a chain running: `ariadne attack stale-owner|duplicate|double-pool|bad-swap [--force]`, `ariadne lifecycle`,
`ariadne verify runs/A/pool.json --rpc URL --report report.html`, `ariadne trace runs/A/pool.json P1:FU-000183`.

## The web UI

A read-only recording of a full run is published from `site/` as a static site on Vercel, at `https://ariadne-delta.vercel.app`. To update it, run `python -m ariadne export`, then redeploy `site/`. It carries a banner saying it is a recording, has a selector for three recorded states (sealed, after the lifecycle, after the forced swap), and re-hashes the membership events in your browser to check the commitment.

`python -m ariadne serve --open` starts a local server (stdlib only, no build step). Choose **a pool already built** or **build it yourself**. It boots a local chain, deploys the contract and replays the units, showing each step.

Two modes share one design system, switchable in the header, with a light and a dark theme:

- **Story** is the guided five-act demo: the problem, one invoice's journey (an ownership graph drawn from real events), the attacks, an honest pool, the trustee's check, rules from prose, and a closing line whose numbers are computed from your run. Arrow keys move between acts.
- **Console** is the full workbench:

| Page | What it shows |
| --- | --- |
| Pool | Verified / not verified banner, the ten checks, buyer and seller concentration against the signed caps, the on-chain commitment, recent membership events |
| Journey | Any unit's history read from chain events, starting with the hero invoice |
| Attacks | Fire the three attacks; each shows the contract's revert and proof that chain state did not change |
| Life | Settle, substitute, default, with re-verification after each; then the swap the engine refuses and the forced swap the verifier catches |
| Build | The six-step wizard: propose, pin the signed rules, evaluate every unit with reasons, review held loops, decide candidate merges, trim and seal |
| Evidence | Loop diagrams and scores with feature breakdown, resolved buyer groups and the links behind them, candidate merges |
| Units | Every factoring unit, searchable and filterable |
| Compiler | Paste prospectus prose; a deterministic parser proposes rules with their source words, a person approves and signs |
| Metrics | Guarantee metrics (should be zero), planted scenarios caught, detection metrics on seeds A, B, C, the loop benchmark |
| Context | Where Ariadne sits next to CERSAI, MonetaGo and TReDS, the seven failures, architecture, the trust model |
| Q&A | The sixteen judge questions, answered |
| Report | The one-page trustee report |

Every button sends a real transaction or runs the real verifier. Reset stops the chain and clears the session.

**Rules.** The demo pool enforces all nine rule kinds from the spec: state, tenor, days to maturity, transfer count, buyer rating, evidence score, buyer-group concentration, seller concentration and minimum pool size (a closing condition, checked when the pool is sealed).

**The compiler has no AI.** `ariadne/compiler.py` is a closed-grammar parser standing in for the spec's language-model layer. It keeps the guarantees that matter: every rule carries its verbatim source words, values are bounds-checked, anything unmapped is listed as unsupported rather than dropped, and nothing takes effect without a person's signature.

## What the demo shows (seed A)

| Step | Result |
| --- | --- |
| Hero invoice FU-000183 (₹4.85 lakh) | Registered, financed by A, re-discounted to B, pooled: every step an on-chain event |
| B pools a unit it re-discounted to C | Reverts `NotOwner`, state unchanged |
| Second platform registers the same invoice, reformatted | Reverts `DuplicateReceivable`, state unchanged |
| B adds a unit already live in Pool 1 to Pool 2 | Reverts `AlreadyEncumbered`, state unchanged |
| Build pool from 213 units | 168 pass the nine rules; fabricated ring held for review and excluded; the four-GSTIN group and the twin-firm candidate resolved; concentration trimmed under the caps; 122 members sealed |
| `ariadne verify` | `VERIFIED`: commitment replay (122 events), ownership 122/122, unique 122/122, attestations 122/122 agree |
| 18 settlements, 1 good substitution (manifest v2), 1 default | Still `VERIFIED` |
| Swap that breaks the 10% cap | Engine refuses (buyer group at 2,148 bps against a 1,000 cap, and the seller cap too). Forced with the engine key, the contract accepts it and the verifier turns red, naming unit, rule and block |

## Architecture

| Layer | Where | Decides |
| --- | --- | --- |
| 1. Lineage ledger | `contracts/src/AriadneLedger.sol` | Ownership, one live pool per `receivableKey`, membership commitment (computed on-chain) |
| 2. Eligibility engine | `ariadne/engine.py` | Pure functions over a signed rule set; the verifier imports the same code |
| 3. Evidence scoring | `ariadne/evidence.py` | Nothing. Tarjan SCC + bounded cycle search with a feature breakdown; union-find buyer groups |
| 4. Prospectus compiler | `ariadne/compiler.py` | Nothing. A deterministic parser (no AI in this build) proposes rules traced to source words for a person to sign |
| 5. Investor verifier | `ariadne/verifier.py`, `report.py` | Nothing. Replays chain events and recomputes every check |

Supporting: `wire.py` (identities, hashing, EIP-712), `generate.py` (seeded data), `originator.py` (replay, pool build, attacks, lifecycle), `chain.py` (Anvil + revert decoding), `metrics.py`. Frozen interface and deviations from the spec: [WIRE_PROTOCOL.md](WIRE_PROTOCOL.md).

## Tests

- Foundry: 12 attack and unit tests, plus invariant tests for spec invariants 1-7 (stateful fuzzing with hostile calls). A planted mutation (`livePoolOf` not cleared) is caught by the invariants.
- pytest (40+): one test per rule kind, the buyer-rating rule, fingerprint normalisation, JCS behaviour, evidence scoring on seeds A and C, entity-resolution candidates, the prospectus compiler (fixture mapping, source-word enforcement, bounds), the web server's routes, and the full demo end to end on a temporary Anvil chain. CI runs `forge test` and `pytest` on every push.

## Detection metrics (synthetic, measured, cut-off 700)

| Metric | Seed A (demo) | Seed B (unseen) | Seed C (adversarial) |
| --- | --- | --- | --- |
| Fabricated-loop recall | 5/5 | 5/5 | 0/5 |
| Legitimate-loop false positives | 0/4 | 0/4 | 0/4 |
| Entity-resolution precision (pairs) | 16/16 | 16/16 | n/a (no merges) |
| Entity-resolution recall (pairs), strong signals only | 16/17 | 16/17 | 0/7 |
| Recall after the reviewer confirms candidate merges | 17/17 | 17/17 | 1/7 |

Seed C is built to beat the heuristics: an asymmetric, slow, old-entity ring, and a group linked only by a shared address. A shared address is a medium signal, so it becomes a candidate merge that a person must confirm; without that decision it stays split. Seed C misses both, which is the point. Seeds A and B come from the same generator, so their similar results show the pipeline is not tuned to one draw, not that it generalises to real data. The score weights are hand-set constants; the cut-off is a signed policy choice.

Loop enumeration is the hotspot: on a 30-node strongly connected component it goes from 0.5 ms at 60 edges to 24 ms at 154 edges, where the 2,000-cycle cap starts to truncate. We bound cycle length at 6 and report the benchmark rather than claim linear scaling.

## Known limits (deliberate)

- Synthetic data only. No real CERSAI, GSTN or MonetaGo integration. The fingerprint is our own keyed hash; production would consume the registry layer's, including partial matching.
- The consortium secret is synthetic and public in `ariadne/wire.py`. A real deployment holds it privately and restricts chain reads.
- A registrar that lies about its own units is trusted; damage is contained to that platform's units.
- The verifier's attestation check re-runs the engine on each entry. The contract already rejects signatures that are not from the engine key, so the verifier does not decode calldata.
- No language model is used anywhere. The compiler is a deterministic parser; the AI layer in the spec is described, not built.
- Not built: a public testnet deployment. The hosted copy is a recording, not a live chain: it replays a real run and recomputes the commitment in the visitor's browser, but cannot fire anything.
- Amendments and credit notes (a registrar-recorded change to the outstanding amount) are specified but not implemented; concentration uses face value.

## Honesty notes

No code is carried over from any earlier project. Patterns are reimplemented and credited: **Circe** (SCC, depth-limited cycle search, discrimination features), **Lumine** (entity resolution across identifiers), **Aether** (eligibility schedules as code), **ManifestGuard** (contract enforces the boundary; verifier trusts only chain data), **Rebound** (typed boundary). Commit history reflects when work was done.

Spec: Ariadne v1.2, Bit N Build '26.

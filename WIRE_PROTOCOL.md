# Ariadne wire protocol v1.0

Frozen interface between the ledger, engine, evidence, data and verifier lanes. A change here is a version bump.
Reference implementation: `contracts/src/AriadneLedger.sol`, `ariadne/wire.py`.

## 1. Contract (`AriadneLedger`)

States: `NONE, REGISTERED, FINANCED, POOLED, SETTLED, DEFAULTED`.
Ops: `ADD, REMOVE, SUBSTITUTE_OUT, SUBSTITUTE_IN, SETTLED, DEFAULTED` (uint8, in that order).

| Function | Caller | Requires |
| --- | --- | --- |
| `registerUnit(unitId, receivableKey, invoiceDate, dueDate, amountPaise, buyerKey, sellerKey)` | Registrar | state NONE; `activeUnitOf[receivableKey]` empty |
| `recordFinancing(unitId, financier)` | Unit's registrar | REGISTERED |
| `rediscount(unitId, to)` | Owner | FINANCED; not pooled; `to` is a different non-zero address |
| `createPool(poolId, rulesHash, groupMapHash, evidenceHash)` | Any | pool id unused |
| `addToPool(poolId, unitId, attestation)` | Originator | owner; not live in any pool; FINANCED; pool not sealed; valid attestation |
| `removeFromPool(poolId, unitId)` | Originator | not sealed |
| `sealPool(poolId)` | Originator | not sealed; at least one member |
| `substitute(poolId, outId, inId, attestation)` | Originator | sealed; attestation at `manifestVersion + 1` |
| `settle(unitId)` / `markDefault(unitId)` | Unit's registrar | FINANCED or POOLED; default only after due date |

Errors: `NotAdmin, NotRegistrar, NotUnitRegistrar(unitId), NotOwner(unitId, caller), NotOriginator(poolId, caller),
WrongState(unitId, expected, actual), DuplicateReceivable(receivableKey), AlreadyEncumbered(receivableKey, poolId),
PoolAlreadySealed(poolId), PoolNotSealed(poolId), PoolExists(poolId), UnknownPool(poolId), NotInPool(poolId, unitId),
EmptyPool(poolId), NotYetDue(unitId), ZeroAddress, BadRecipient(to), BadAttestation(unitId)`.

Events: `UnitRegistered, UnitFinanced, UnitTransferred(unitId, from, to, transferCount), PoolCreated(poolId, originator,
rulesHash, groupMapHash, evidenceHash), MembershipChanged(poolId, op, unitId, commitment, manifestVersion),
PoolSealed(poolId, commitment), UnitSettled, UnitDefaulted, RegistrarSet, EngineSignerSet`.

**Commitment:** `C_n = keccak256(C_{n-1} || uint8(op) || unitId)`, `C_0 = 0x00..00`, one step per `MembershipChanged`.

**Manifest version:** 0 before sealing, 1 at seal, +1 per `substitute`. Settlement and default remove a unit from live
membership (a commitment step) but do not change the manifest version.

**Deviations from spec v1.2** (all additive or renames): `invoiceDate` on `Unit` and `UnitRegistered` (the tenor rule needs
it); `evidenceHash` on `Pool` and `PoolCreated`; `PoolAlreadySealed` instead of `PoolSealed` (a Solidity keyword clashes
with the event name); `activeUnitOf[receivableKey]` is never released, so a registered receivable stays taken. Check order in
pooling is owner, then encumbrance, then state, so the demo attacks fail with the error that names the real reason.

## 2. Identities and hashing (`ariadne/wire.py`)

- `unitId = keccak256("{PLATFORM}:{UNITNO}")`, whitespace removed, upper-cased.
- `receivableKey = HMAC-SHA256(consortiumSecret, "receivable|" + seller|buyer|invoiceNo|invoiceDate|amountPaise)`.
  Fields are normalised first: upper-case, whitespace removed, leading zeros in numeric segments dropped. Amendments and
  credit notes do not change the key. Exact match only; partial matching is the registry layer's job.
- `partyKey = HMAC-SHA256(consortiumSecret, "party|" + GSTIN)`. The demo secret is synthetic and public in the repo.
- `poolId = keccak256("pool:" + name)`.
- Hashed documents use sorted-key compact JSON (a subset of RFC 8785 JCS). Floats are rejected: paise and basis points only.
- `rulesHash = keccak256(canonical(ruleSet minus signedBy, signature))`.

## 3. EIP-712

Attestation, domain `{name: "Ariadne", version: "1", chainId, verifyingContract}`:
`Attestation(bytes32 poolId, bytes32 unitId, bytes32 receivableKey, bytes32 rulesHash, uint32 manifestVersion)`.
Signatures are 65 bytes `r||s||v`, low-s, `v` in {27, 28}. The contract accepts only the engine key set by the admin.

Rule sign-off, chain-independent domain `{name: "Ariadne Rules", version: "1"}`:
`RuleSet(string ruleSetId, uint32 version, bytes32 rulesHash)`.

**What an attestation covers.** Before sealing, an addition is attested against the *proposed sealed pool* (concentration on a
half-built pool is meaningless). A substitution is attested against the pool after the swap.

## 4. Rules (closed vocabulary)

Implemented: `state_required, tenor_max_days, min_days_to_maturity, max_transfer_count, evidence_score_max` (unit) and
`buyer_group_concentration_max_bps, seller_concentration_max_bps, min_pool_size` (pool). `buyer_rating_min` is in the
vocabulary but has no data in the prototype and is refused. Unit rules are evaluated at the timestamp of the gating block.

## 5. Files

- `units.csv`: `platform, unit_no, invoice_no, seller_gstin, buyer_gstin, amount_paise, invoice_date, due_date, financier,
  rediscount_path, reserve`. All dates are relative to the anchor date in `meta.json`.
- `entities.csv`: `gstin, name, pan, registered, directors (|-separated), address, bank`.
- `truth.json`: planted scenarios and counts, used only by `metrics`.
- `pool.json`: `poolId, ledger, chainId, rules (signed), groupMap, evidence, synthetic`. Each of the three documents is
  checked against the hash pinned in `PoolCreated`.
- Evidence unit record: `unitId, label, evidenceScore, features, cycleId, reviewStatus (NONE | PENDING_REVIEW | CLEARED | EXCLUDED)`.
- Group map: `{groups: {partyKeyHex: groupId}, evidence: {groupId: {members, links}}}`; a buyer absent from the map is its own group.

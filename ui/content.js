"use strict";
/* Static content taken from the spec (v1.2). Kept apart from app.js so wording can be reviewed in one place. */

const GLOSS = {
  TReDS: "Trade Receivables Discounting System: RBI-regulated online platforms where MSME invoices are financed by banks and NBFCs.",
  CERSAI: "India's central registry where assignments of receivables are legally recorded.",
  "re-discount": "A financier sells an already-financed invoice to another lender before it matures.",
  bps: "Basis points: hundredths of a percent. 1,000 bps is 10%.",
  attestation: "The eligibility engine's signature that a unit passed the pool's rules. The contract checks the signature; the verifier re-checks the rules.",
  commitment: "A rolling hash the contract updates on every membership change, so the exact history can be replayed and checked.",
  originator: "Whoever assembles and sells the pool: here, a financier holding TReDS receivables.",
  trustee: "The party that oversees a pool on investors' behalf and checks it stays within its terms.",
  receivableKey: "A canonical fingerprint of the economic invoice, the same across platforms, so one invoice cannot be pooled twice under two unit ids.",
  manifest: "The numbered version of a sealed pool's membership. It goes up on every substitution.",
};
const gloss = (term, label) => `<abbr tabindex="0" data-tip="${esc(GLOSS[term])}">${esc(label || term)}</abbr>`;

const STATUS_QUO = [
  { sys: "CERSAI", does: "Legal registry of receivable assignments. TReDS must file every financing there.", doesnt: "Check pool composition, pool rules or substitutions." },
  { sys: "MonetaGo", does: "Fingerprints invoices, checks a shared registry for full or partial matches, offers an assignment registry and genuineness checks against GST data. Used by TReDS platforms since 2018.", doesnt: "Track which pool a receivable is in, or evaluate pools against their rules." },
  { sys: "TReDS platforms", does: "Onboarding, bidding, settlement, and now re-discounting within the platform.", doesnt: "Give investors an independent, replayable view of a pool across platforms." },
  { sys: "Ariadne", does: "The pool layer above all three: who owns each receivable, whether it is in another pool, whether it still meets the pool's signed rules, and what changed since issuance.", doesnt: "Replace any of them. It consumes their verified identity; it does not compete with it.", us: true },
];

const GAPS = [
  ["Same invoice financed twice", "Invoice financed on two platforms", "MonetaGo registry; CERSAI filing", "None. We consume a compatible fingerprint."],
  ["Stale ownership in a pool", "B pools a unit it already re-discounted to C", "Assignment in CERSAI and, where used, MonetaGo. No pool-time check that we can find.", "Contract refuses pooling by a non-owner."],
  ["Double pooling", "One receivable in Pool 1 and Pool 2", "Not a single-invoice financing event, so dedup does not see it.", "Contract enforces one live pool per receivable."],
  ["Disguised concentration", "One group across four GSTINs is 30% of a pool capped at 10%", "Manual analyst work.", "Entity resolution plus a deterministic cap."],
  ["Silent substitution", "Paid units swapped for ones that break the rules", "Trustee reports, after the fact.", "Every swap re-checked and logged on-chain."],
  ["Criteria drift", "The offering document and the servicer's spreadsheet disagree", "Nothing machine-checkable.", "Signed rule set pinned by hash; every rule traced to its source words."],
  ["Fabricated trading loops", "Related firms invoice in a circle", "Platform checks at onboarding (now lighter).", "Evidence for a human, never a verdict."],
];

const PROTECTS = [
  "Pooling a receivable the originator no longer owns: prevented by the contract.",
  "The same receivable in two live pools, even as different units on different platforms: prevented, using the canonical receivableKey.",
  "Pool membership that differs from what the originator reports: detected, because the contract computes the membership commitment itself.",
  "Substitutions that break the pinned rules: refused by the engine, and detected by the verifier if forced through.",
  "Concentration caps dodged by splitting a group across identifiers: detected once the group is resolved.",
  "A rule changed by software: impossible. The compiler only proposes, a human signs, and the engine enforces signed rules.",
];
const NOT_PROVEN = [
  "That an invoice reflects real goods. That stays with the platform's genuineness checks.",
  "A fabricated invoice that no evidence feature distinguishes from genuine trade.",
  "Buyer credit risk. A correctly pooled invoice can still default.",
  "A bad first attestation if the engine's key is stolen. The verifier detects it afterwards; it is not blocked at write time.",
  "Wrong data from a registrar at registration, or a registrar that falsely reports settlement for its own units.",
  "Tranching, pricing and credit enhancement.",
  "The legal effect of any assignment. CERSAI remains the legal record.",
];
const TRUST = [
  ["TReDS platform (registrar)", "Registering its own units, their fingerprint, first financing, and their settlement or default", "Anything about other platforms' units", "Its own units' data is wrong; other platforms are unaffected."],
  ["Fingerprint source", "A consistent key for the same economic invoice", "Anything else", "Two keys for one invoice defeats cross-platform single-pool. A named dependency."],
  ["Financiers", "Signing transfers of units they own", "Reporting pool contents", "Cannot move others' units; the contract refuses."],
  ["Ledger contract", "Enforcing ownership, one live pool and the membership commitment", "Judging trade or eligibility", "Covered by invariant tests; a bug here is critical."],
  ["Eligibility engine and its key", "Applying signed rules deterministically", "Writing rules", "A wrong attestation lands on-chain but the verifier flags it with unit, rule and block."],
  ["Entity resolution", "Proposing buyer groups with evidence", "Final grouping on weak signals", "A false merge causes a false cap failure; weak merges need human confirmation."],
  ["Rule compiler", "Proposing rules traced to source words", "Legal interpretation, final rules, any decision", "A mis-mapped rule is caught at human review or not at all; review is mandatory."],
  ["Verifier", "Nothing. It recomputes from chain data and signed rules.", "-", "Anyone can run a second copy."],
];
const ONCHAIN = [
  ["unitId, receivableKey, owner, state, currentPoolId, registrar", "On-chain", "What the contract enforces"],
  ["Face value, due date", "On-chain", "Needed for tenor and concentration replay"],
  ["Buyer and seller identifiers", "On-chain as keyed hashes only", "Grouping replay without exposing GSTINs"],
  ["Rules hash, pool membership commitment, attestations", "On-chain", "Pin what was promised and what was checked"],
  ["Full signed rule set", "Off-chain file, hash on-chain", "Readable by humans; integrity from the hash"],
  ["Buyer-group map and evidence set", "Off-chain files, hashes on-chain", "Needed by the verifier; contain identifiers"],
  ["Invoice documents, GSTINs, director and bank data", "Off-chain only", "Commercially sensitive; never on any chain"],
];
const LAYERS = [
  ["1. Lineage ledger", "Smart contract: ownership, one live pool per receivable, membership commitment", true],
  ["2. Eligibility engine", "Pure functions over the signed rules; the verifier runs the same code"],
  ["3. Evidence scoring", "Loops and buyer groups, as evidence for a human"],
  ["4. Rule compiler", "Prospectus prose to proposed rules, traced to source words"],
  ["5. Investor verifier", "Recomputes everything from chain events; trusts nothing else"],
];

const QA = [
  ["Doesn't CERSAI already do this?", "CERSAI is the legal registry of assignments, and we rely on it. It records that an assignment happened; as far as we can find, it does not check pool composition, pool rules or substitutions. Ariadne sits above it. We verify this before relying on it."],
  ["Isn't this MonetaGo?", "MonetaGo validates invoices, records assignments and stops the same invoice being financed twice, and has served TReDS since 2018. Ariadne works after that: which pool a receivable is in, whether it is still owned by the seller of the pool, and whether the pool keeps its rules after issuance. We would consume its verified identity, not compete with it. Why would it cooperate? Verifiable pools grow the securitisation market, which grows demand for the registries underneath."],
  ["Why blockchain and not a database?", "A consortium database could run these rules. We use a ledger because ownership and pool history must be append-only and jointly auditable after investors rely on them, with no single participant able to rewrite it. Production would be a permissioned consortium ledger. If one trusted operator is acceptable, a database is cheaper. The chain is not our novelty; the pool layer is. MonetaGo's newer registries use confidential computing, which suits a registry where banks must not see each other's data. Pool history is meant to be shared, and the same rules could run on either."],
  ["What stops the same invoice appearing on two platforms as different units?", "Each unit carries a canonical receivableKey fingerprint. Registration of a second live unit with the same key reverts, and single-pool is enforced on the key, not the unit."],
  ["What if the engine's signing key is stolen?", "A bad unit could enter the pool. The verifier re-runs every rule and flags that attestation with unit, rule and block. That is detection, not prevention, and our claim says so. The Pool life page shows exactly this."],
  ["What if a registrar lies?", "A registrar can only touch its own platform's units, so the damage is contained to them. Registration data is trusted; that is a stated assumption."],
  ["Where is the AI?", "In the spec, an AI compiler turns legal eligibility prose into rules traced to their source words, and a human signs them. It is deliberately kept away from money and eligibility decisions, and the product works without it. In this build the compiler is a deterministic parser: no language model is used, and the Compiler page says so."],
  ["Securitisation of TReDS receivables isn't live yet. Isn't this early?", "Re-discounting is live now under the June 2026 directions, so ownership lineage matters today. Securitisation was announced in Budget 2026-27 and its detailed framework is still forming; Ariadne is ready for it."],
  ["How is this financial inclusion?", "MSMEs get paid early only while financiers keep bidding. Verifiable pools make investors more willing to fund securitisation, which lets financiers recycle capital into new bids. We claim to strengthen the first link, not to fix MSME credit."],
  ["Is the data real?", "No. It is synthetic, with three seeds: demo, unseen and adversarial, all reproducible from the generator. A pilot would need anonymised data from one TReDS platform."],
  ["Is your loop score a fraud detector?", "No. It is a deterministic evidence scorer with hand-set weights, shown with its feature breakdown, and every flagged unit goes to a human. The Metrics page shows on the adversarial seed where it misses."],
  ["Couldn't entity resolution wrongly merge two firms?", "Yes. Only strong signals auto-merge, a shared address only creates a candidate a human must confirm, every group shows its links, and a reviewer can reject a merge before the map is frozen."],
  ["Does it scale?", "The engine is linear in pool size. Loop enumeration is the hotspot in dense graphs, so we bound cycle length and publish a benchmark of runtime against density rather than claim linear scaling. See the Metrics page."],
  ["Did you reuse old code?", "No. The rules forbid pre-written code at the final, and we wrote this prototype fresh too. We reuse patterns from earlier projects and name them in the README."],
  ["Isn't invoice data sensitive?", "GSTINs appear only as keyed hashes with a consortium-held secret. Documents and identities stay off-chain, and production reads would be restricted to consortium members and investors."],
  ["Does this handle tranching and pricing?", "No. Those are out of scope. We verify what is in the pool, not how it is priced."],
];

const OPS = [
  ["Confirm current owner of each unit", "Request and cross-check originator records", "One verifier check"],
  ["Confirm no unit is in another pool", "Not practically checkable across originators", "One verifier check"],
  ["Check concentration by real group", "Manual group research per buyer", "Computed from the frozen group map"],
  ["Detect a rule-breaking substitution", "Next periodic servicer report", "The substitution event itself"],
];

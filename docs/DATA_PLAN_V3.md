# SatyaLens text classifier — data acquisition plan v3

Status 2026-10-24: approved with conditions. The binding specification is now
`docs/protocol_v3.json` (SHA-256 in `docs/protocol_v3.sha256`); where this document
and the protocol differ, the protocol wins. Rule-engine fixes in §5 are done.
Nothing has been trained or split under v3 yet.
`data/real_world_test_v1.csv` is **retired from model selection**: it was used to
judge v1 and candidate v2 and must not influence any further decision. The
deployed v1 model stays advisory-only; candidate v2 is **not** promoted.
Preserved hashes: `reports/preserved_artifacts_manifest.json`.

## 1. Sources (verified 2026-10-24)

| Source | Licence (verified where) | Real? | Labels | Role in v3 |
|---|---|---|---|---|
| **IMC25 smishing dataset** — Agarwal, Papasavva, Suarez-Tangil, Vasek, "Fishing for Smishing", IMC 2025, DOI 10.1145/3730567.3764431; github.com/reportsmishing/Smishing-Dataset-IMC25 @ `a6175560` | **CC BY 4.0** (repo LICENSE.txt + README; paper Appendix B) | **Real** user-reported smishing from 5 public forums, pseudo-anonymised, ethics-approved | All rows are reported smishing; `scam_type` ∈ banking, delivery, government, telecom, others, spam, wrong number… | **Primary real suspicious source.** 33,869 rows (24,510 distinct); 7,434 India-linked rows (3,779 distinct skeletons; 6,378 banking; SBI/Paytm/HDFC) |
| Ridham115 indian-scam-sms-synthetic-audited @ `45e92c38` | CC BY 4.0 (card) | LLM-generated | rubric-based scam/safe | Synthetic supplement only (gpt-oss rows); never in the test set |
| MultiWOZ 2.2 @ `fe0c8e65` | MIT (LICENSE file) | Real human-written dialogue | benign by construction | Ordinary-conversation legitimate text; train and test use **different dialogue files** |
| SatyaLens synthetic v1 | project | synthetic | template intent | Supplement only; never in the test set |
| **Consented donations** (proposed, see §3) | consent form; project use | **Real** genuine SMS | owner-verified genuine | Only route found to real genuine Indian bank/UPI/OTP alerts |

Examined and rejected: ScamShield (sidzzz07) and ScamShield-IN (haxrits, "openrail") —
provenance undocumented; Bengali SMS smishing — sources undocumented;
bolewara Hinglish scam — English part derived from UCI; UCI/NUS/"Super SMS" — share
sources with the retired v1 test; SpamHunter, Smishtank, Reddit-scam (arXiv 2605.16656),
operator-report study (arXiv 2508.05276) — no dataset licence/release found;
MIMICS-3500 — on request only; gandharvbakshi OTP dataset — gated.

### IMC25 handling rules
- **Label mapping:** `banking, delivery, government, telecom, others` → suspicious.
  `spam` (marketing/investment-group invites) and `wrong number` → **excluded as
  ambiguous**, consistent with the v1 policy that "spam" ≠ fraud.
- **Placeholder shortcut:** the text contains `<URL>`, `<NAMED_ENTITY>`,
  `<DATE_TIME>`, `<PHONE_NUMBER>`, `<LOCATION>`, … only on the suspicious side. Left as-is,
  a model would learn "placeholder ⇒ scam". Plan: run **one shared anonymiser over every
  source** (legitimate included) that maps URLs, phone numbers, dates/times and ≥6-digit
  runs to the same neutral placeholders, and normalise IMC25's own placeholders to that
  scheme. Add a test that placeholder tokens alone cannot separate the classes.
- **PII:** 265 rows still contain ≥10-digit runs → masked by the shared anonymiser.
- **Overlap with retired v1:** 0 exact, 199 skeleton matches → excluded from v3 entirely.
- **Attribution:** CC BY 4.0 — cite the IMC 2025 paper; record in provenance.

## 2. Blocker: real genuine messages

No openly licensed corpus of **real** genuine Indian bank / UPI / OTP SMS exists among
the sources checked (privacy makes such data rare). Without it, the v3 legitimate class
is: MultiWOZ (real, but dialogue not SMS) + synthetic genuine-alert counterparts.
A model trained that way could still learn "SMS-style alert ⇒ suspicious". **We will not
manufacture "real" messages.** Options:

1. **Consented donation (recommended).** Team members (and other consenting adults)
   export genuine messages from their own phones — OTP alerts, debit/credit alerts,
   UPI confirmations, delivery notices, bills — with written consent. A local script
   masks names, account/phone/OTP digits, UPI handles and URLs before anything is saved;
   the donor confirms each message is genuine (received from their own bank/merchant).
   Group = donor × sender. Requires your decision and the donors' action.
2. Proceed without real genuine SMS and state that the test cannot measure false
   positives on genuine Indian alerts (only on conversation and synthetic alerts).

## 3. Proposed v3 datasets (after approval)

- **Pool v3 (train + validation):** IMC25 (mapped, anonymised, deduped) + MultiWOZ file
  `dialogues_001.json` + R115 gpt-oss + SYN v1 (+ donations if approved).
- **Groups:** IMC25 campaign clusters = connected components of messages whose skeleton
  token Jaccard ≥ 0.6 (blocked by named entity / first tokens), so a campaign's
  variants never straddle a split; R115 = label:topic:language; SYN = template family;
  MultiWOZ = dialogue id; donations = donor × sender.
- **Dedup:** normalised-text duplicates collapsed; identical text with conflicting labels
  excluded; every exclusion logged (exclusion files gitignored).

## 4. How the new frozen test (v3) stays independent

1. **Protocol before data:** `docs/protocol_v3.json` fixes training sources, anonymiser,
   grouping, candidate configs, selection rule (grouped-CV macro-F1 on the pool only)
   and acceptance criteria. Its SHA-256 is recorded **before** the test set exists.
2. **Assignment by hash, not by model:** each group's test membership is
   `sha256(group_id + protocol_hash) < 15 %`, computed once. No model output influences
   which groups are held out.
3. **Separate real sources at the test boundary:** test legitimate conversation comes
   from a **different MultiWOZ file** (e.g. `dialogues_017.json`), never sampled for
   training; synthetic sources (SYN, R115) are **excluded from the test set**, so the
   test is real-only.
4. **Leakage checks after assignment:** exact, skeleton and Jaccard ≥ 0.8 checks between
   test and pool; any hit moves the whole cluster out of training (never out of test
   silently); report counts.
5. **Sealing:** the test CSV's SHA-256 goes into `reports/frozen_test_v3_manifest.json`;
   the file is gitignored and not opened by selection scripts (tests assert this, as
   for v2). Final evaluation runs **once** against pre-registered criteria; results are
   written to a new report directory; failures are reported, not tuned away.
6. **Retired v1** is reported only as a historical benchmark after final selection.

## 5. Rule engine (tested separately — no change made yet)

Tests in `tests/test_rule_engine_behaviour.py` document, on the challenge suite:
ordinary conversation, payment confirmations, delivery notices and ordinary financial
messages → **0 false flags**; genuine OTP warnings → **5/6 falsely flagged**
(`CREDENTIAL_REQUEST`, plus `URGENCY_PRESSURE` on "expires in 5 minutes");
scams → 4/16 flagged. Newly found defect: `GIFT_CARD_PAYMENT` misses **plurals**
("gift cards", "Google Play cards"). All three defects are strict-xfail tests.
Proposed fixes (await approval): pluralise card patterns; require a request verb
("share/send/tell/give … OTP") rather than any "OTP" mention, with an exception for
delivery phrasing ("your OTP is", "do not share").

## 6. Unchanged guarantees
- ML output stays advisory; it must not alone produce a definitive high-risk decision.
- Person 2's decision engine is not modified.
- Generated datasets, exclusion logs with unmasked text, raw downloads and model
  artifacts are gitignored; provenance/attribution JSON and scripts are committed.

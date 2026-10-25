# Donated messages — data handling, deletion and withdrawal

| Item | Rule |
|---|---|
| Who may donate | Consenting adults (18+), signed CONSENT-v1 |
| What | Genuine messages personally received from institutions only; no personal chats; max 150 per donor |
| Columns collected | `text`, `category`, `sender_type` — nothing else (no sender numbers, timestamps, names) |
| Masking | `satyalens_analysis/anonymise.py` (anon-v1) before saving; donor redacts residual names/places |
| Review | Every row needs `approve`, `approve-checked` (warnings verified as false alarms) or `reject`; unresolved warnings are dropped |
| Raw text | Stays on the donor's machine outside the repo; the tool refuses repo paths; donor deletes it after finalising |
| Storage | `data/donations/donation_<donor>.csv` + `consent_ledger.csv` (both gitignored, project machine only) |
| Signed forms | Kept offline by the project lead; never in the repo |
| Use | Training pool v3 and frozen test v3 (per `docs/protocol_v3.json`); aggregate reporting only |
| Retention | Until **2026-12-31** (the date on CONSENT-v1); then delete `data/donations/`, the consent ledger, derived pools/test sets and models trained on them |
| Commit policy | Never commit raw messages, review files, donation files, the ledger, or exclusion files with unmasked text |

## Withdrawal procedure
1. Donor tells the project lead their donor code (or name → lead looks up the code offline).
2. `python scripts/donate_messages.py withdraw --donor-id <code>` — deletes the donation
   file and marks the ledger `withdrawn`.
3. Rebuild `data/training_pool_v3.csv`; retrain any candidate trained on it.
4. Frozen test v3: before final evaluation → rebuild and reseal (new manifest);
   after final evaluation → delete the rows, keep the published aggregates, annotate the
   report "a donor withdrew on <date>".
5. Delete local copies, review files and backups containing the donor's rows.

## Minimum coverage (from the protocol)
If too few verified genuine messages are collected, the final report must say that
false-positive performance on genuine Indian financial alerts is **not validated**;
no substitute or synthetic data may be presented as real.

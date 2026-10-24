# Protocol v4 — proposal record

**Approved 2026-10-24 and hashed:** `docs/protocol_v4.json`, SHA-256
`f32adc7668ca9f4d44bee2a3a358c4191ba369e32824421de69f77c9c54c0a49`
(`docs/protocol_v4.sha256`). Decisions Q1–Q5 and grouping policy G4 are recorded in the
approved file; grouping diagnostics in `reports/grouping_v4/diagnostics.json`; counts-only
dry run in `reports/build_v4/build_report_dry_run.json`. No pool, test set or model has
been built under v4. The text below is the original review proposal, kept for the record.

Draft: `docs/protocol_v4_DRAFT.json`. Protocol v3 (`e97171da…9e7e`) is unchanged.

## Proposed changes and reasons

| # | Change | Reason | Expected impact (counts measured 2026-10-24, before dedup/leakage) |
|---|---|---|---|
| C1 | IMC25 `hey mum/dad` → suspicious | Family-impersonation money requests are fraud in SatyaLens's scope; v3 excluded them only because the mapping listed five categories | +77 English rows (raw); S1 also reported on this subset |
| C2 | Mendeley as supplementary **train-only** source; Latin-script rows; scam→suspicious, ham→legitimate | Adds real (author-claimed) scam and non-scam text from a different region; CC BY 4.0 verified | ≈412 rows (247 scam, 165 ham) after retired/challenge overlap (5) and dedup (5); further cross-source dedup/leakage expected (6 IMC25 near-overlaps). **Does not** add verified genuine bank/UPI alerts; never in test or coverage |
| C3 | anon-v2 for every source, train/test/inference | anon-v1 leaves social handles and e-mail fragments (41 handles in IMC25) | 97 IMC25 rows change (53 handletoken, 45 emailtoken); Mendeley 0, SYN 0; residual '@' = 139 standalone, 19 prices/rates, 11 other |
| C4 | Exact Jaccard ≥ 0.6 join for clustering | v3's rarest-3 blocking missed near-duplicate pairs; the leakage step then removed 16 train groups / 2,117 rows in the v3 dry run | Fewer leakage removals; possibly larger groups (see Q4) |
| C5 | New salt, v4 file names | A new protocol must not reuse v3's assignment or files | Different test membership than the v3 dry run |
| C6 | Extra reporting | Visibility of the new sources/subset | None on criteria |

Unchanged: sources DONATION/MWOZ/R115/SYN, 20 % test allocation, coverage targets,
candidates, selection rule, acceptance thresholds, sealing and one-shot evaluation.

## Not solved by v4
- **Genuine Indian bank/UPI alerts**: still only from consented donations (0 so far).
  L1 remains NOT EVALUABLE until coverage is met.
- Mendeley ham is not verified genuine financial alerts and is Bangladesh-centred.

## Open questions for approval
Q1 include `hey mum/dad`? · Q2 Mendeley Latin-only or include Bengali? · Q3 Mendeley ham
as legitimate training rows? · Q4 rule for giant template clusters? · Q5 keep v3
thresholds? (details in the draft JSON)

## Already prepared (no protocol executed)
- `anonymise_v2()` in `satyalens_analysis/anonymise.py` with
  `tests/test_anonymiser_versions.py`; anon-v1 output frozen by golden tests; anon-v2 is
  not used by any pipeline.

## To do after approval (before any v4 data is built)
Hash v4; implement the code changes listed in `required_code_changes_before_execution`
with tests; then run the v4 build dry run (counts only) and report.

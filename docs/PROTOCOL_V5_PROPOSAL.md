# Protocol v5.0 — proposal record and FINALIZED decisions

**FINALIZED and hashed 2026-10-24:** `docs/protocol_v5.json`, SHA-256
`3889099d1da7fa6cdc987685a2dacda4592508339bd8cedb91a99fa29f25f3ee` (`docs/protocol_v5.sha256`).
Supersedes protocol v4 (`docs/protocol_v4.json`, SHA-256
`f32adc7668ca9f4d44bee2a3a358c4191ba369e32824421de69f77c9c54c0a49`), which was never executed.
v3 and v4 files, scripts, reports and hashes are unchanged (pinned in `tests/test_protocol_v5.py`).
No pool, test set, manifest, evaluation log, selection report or model exists under v5.

## Finalized decisions (as approved)

| # | Decision | Where in protocol v5 |
|---|---|---|
| D1 | Data construction identical to v4: salt `satyalens-protocol-v4-2026-10-24`, fraction 0.20, sources, anon-v2, build order, G4. The v5 counts-only dry run equals the reviewed v4 dry run (SHA-256 `99b29db0629873f56b3a454afcd5c46f30fbacd1732bc417e488b49763778a15`) except the protocol label, protocol hash and build timestamp. | `decisions.Q9`; `test_assignment`, `grouping`, `sources`, `build_order` unchanged |
| D2 | v5 file names for every new artifact; v3/v4 files untouched (see naming map). | `decisions.Q10`; `sealing`, `model_selection`, `commit_policy` |
| P1 | Inconclusive predictions count as 'legitimate' in every selection metric (pooled and per-fold macro-F1, legitimate FPR, DONATION FPR, per-source): a miss for suspicious rows, not a false positive for legitimate rows. | `model_selection.scoring`; `decisions.Q6` |
| P2 | Selection majority-class baseline from each training fold; always-legitimate fixed; baselines are never selection criteria. B1 majority class from the sealed manifest's `pool_counts`, never from test labels. | `model_selection.reported_baselines`, `baselines_role`; `acceptance_criteria.B1`; `decisions.Q7` |
| P3 | Same inconclusive convention for every acceptance quantity; exact (unrounded) threshold comparison; `acceptance_v5.json` authoritative, `evaluation.json` descriptive only. | `acceptance_criteria.conventions`; `decisions.Q8` |
| P4 | (= P3 wording, merged into `conventions`.) | `acceptance_criteria.conventions` |
| P5 | Manifest records `pool_counts` (and `pool_csv_sha256`). | `sealing.manifest` |
| P6 | Test-majority macro-F1 reported only as a labelled RETROSPECTIVE REFERENCE; never gating, never used for selection or tuning. | `acceptance_criteria.reported_not_gating` |
| R1 | `SATYALENS_SEALED_MANIFEST` adds one manifest to the default v3/v4/v5 manifests; it never disables them. Implemented in `text_classifier._sealed_manifest_paths`. | `sealing.guard`; `required_code_changes` |
| R2 | Manifest committed at sealing; evaluation log + metrics-only reports committed immediately after the single final evaluation; predictions.csv never committed. Residual risk: local deletion of the log before that commit cannot be prevented locally. | `sealing.commit_record`, `sealing.residual_risk`; `commit_policy` |
| R3 | `.gitignore` protects v4/v5 pool and frozen test files and evaluation predictions.csv. | `required_code_changes`; `commit_policy.never_commit` |

Unchanged: coverage requirement (L1 NOT EVALUABLE until >= 150 donated test messages from
>= 5 donors with >= 3 categories of >= 25), thresholds S1/S2/L1/L2/M1, B1 "strictly greater",
verdict wording, candidates, CV, selection rule, one-shot rule. Donations to date: 0 messages,
0 donors -> L1 NOT EVALUABLE.

## Naming map (D2)

| Role | v4 (unchanged) | v5 |
|---|---|---|
| Protocol | `docs/protocol_v4.json` / `.sha256` | `docs/protocol_v5.json` / `.sha256` |
| Builder | `scripts/build_pool_and_test_v4.py` | `scripts/build_pool_and_test_v5.py` (calls the v4 step functions) |
| Selection / acceptance | `scripts/select_text_model_v4.py`, `scripts/acceptance_v4.py` | `scripts/select_text_model_v5.py`, `scripts/acceptance_v5.py` |
| Pool / test | (never written) | `data/training_pool_v5.csv`, `data/frozen_test_v5.csv` (gitignored) |
| Manifest / log | (never written) | `reports/frozen_test_v5_manifest.json`, `reports/frozen_test_v5_evaluations.jsonl` |
| Evaluation output | — | `reports/frozen_test_v5_evaluation/` (`acceptance_v5.json/.md`; predictions.csv gitignored) |
| Build / selection reports | `reports/build_v4/build_report_dry_run.json` | `reports/build_v5/`, `reports/model_selection_v5/` |
| Candidate model | — | `satyalens_analysis/models/candidates/v5/` |
| Tests | `test_build_v4.py`, `test_selection_acceptance_v4.py` | `test_build_v5.py`, `test_selection_acceptance_v5.py`, `test_protocol_v5.py` |

---

## Original review proposal (kept for the record; superseded by the table above)

Items 1-5 below are the proposal as reviewed. P6 was proposed afterwards in the review
response with the wording now in `acceptance_criteria.reported_not_gating`.

### Why

| # | v4 text | Gap |
|---|---|---|
| P1 | `acceptance_criteria.conventions`: "Inconclusive suspicious rows count as MISSES; inconclusive legitimate rows count as NOT false positives." | Stated for acceptance only. `model_selection` is silent, so selection could score inconclusive rows differently (the shared v2 helper drops them, which lets a candidate gain macro-F1 by abstaining). |
| P2 | `B1`: "… strictly greater than both the rule-engine baseline and the majority-class baseline on the same test" | Does not say where the majority class comes from. Choosing it from the test labels would use test labels to define a gating comparator. |
| P3 | (none) | Thresholds are not said to apply to exact or rounded values; `evaluation.json` (text_classifier) excludes inconclusive rows, so it can disagree with the criteria. |

### Proposed changes (as reviewed)

1. **New** `model_selection.scoring`: "Inconclusive predictions (no in-vocabulary feature) are
   scored as 'legitimate' in every selection quantity — pooled and per-fold macro-F1, legitimate
   FPR, DONATION FPR and per-source metrics: a MISS for suspicious rows, NOT a false positive for
   legitimate rows. Inconclusive counts are reported. This is the same convention as
   acceptance_criteria.conventions."
2. **Replace** `model_selection.reported_baselines` with: ["majority class of each training fold
   (per fold; no held-out label chooses the class)", "always-legitimate (fixed)", "rule engine
   (any context flag -> suspicious)", "deployed v1 artifact", "candidate v2 artifact"] —
   "Baselines are reported only and are never selection criteria."
3. **Replace** `acceptance_criteria.B1` with: "Macro-F1 on the full test strictly greater
   (unrounded) than both the rule-engine baseline and the majority-class baseline on the same
   test. The majority class is fixed BEFORE evaluation from the training-pool label counts
   recorded in the sealed manifest (`pool_counts`), never from test labels. A test-majority
   predictor may be reported only as a labelled retrospective reference and never gates the verdict."
4. **Append** to `acceptance_criteria.conventions`: "Thresholds are compared on exact values (k/n
   fractions, unrounded macro-F1); reported values are rounded to 4 dp. acceptance_v5.json is the
   authoritative criteria result; evaluation.json excludes inconclusive rows and is descriptive only."
5. **Append** to `sealing.manifest`: "… counts, and `pool_counts` (training label counts by source)."

### Expected impact (from the v4 dry run, counts only)

- P2: pool 10,048 suspicious / 1,203 legitimate, test 2,421 / 300. The training and test
  majorities are both `suspicious`, so the B1 comparator value would be identical today; the
  change fixes the definition in advance rather than the number.
- P1/P3: no effect on data construction; the dry-run counts stay valid with D1 as decided.

# Text classifier evaluation — real_world_test_v1.csv

- Evaluated: 2026-10-24T06:53:58+00:00 (evaluation only; model not refit, artifact unchanged)
- Model: `tfidf-logreg-v1-66bab05fd245` sha256 `66bab05fd2452b22…` trained on satyalens-synthetic-financial-messages v1 (synthetic)
- Dataset: real-world (non-synthetic); sha256 `f83b8c309d9ae508…`; 5293 rows (legitimate 4833, suspicious 460)
- Classified: 5085; inconclusive (no known vocabulary): 208 {'legitimate': 208, 'suspicious': 0}
- Environment: {'python': '3.11.9', 'scikit_learn': '1.9.1', 'joblib': '1.6.0', 'numpy': '2.4.6'}

| Metric (classified rows) | Model | Majority baseline |
|---|---|---|
| Accuracy | 0.3636 | 0.9095 |
| Precision — legitimate | 0.9212 | 0.9095 |
| Recall — legitimate | 0.3284 | 1.0 |
| F1 — legitimate | 0.4842 | 0.9526 |
| Precision — suspicious | 0.096 | 0.0 |
| Recall — suspicious | 0.7174 | 0.0 |
| F1 — suspicious | 0.1694 | 0.0 |
| Macro-avg F1 | 0.3268 | 0.4763 |
| Suspicious false-negative rate | 0.2826 | 1.0 |
| Legitimate false-positive rate | 0.6716 | 0.0 |

Confusion matrix (rows true, cols predicted; legitimate, suspicious): `[[1519, 3106], [130, 330]]`

One row per near-duplicate cluster (5061 rows): accuracy 0.3622, macro-F1 0.3241, suspicious FNR 0.2802, legitimate FPR 0.6718

## By `source`

| Value | n | legit | susp | inconclusive | accuracy | recall legit | recall susp |
|---|---|---|---|---|---|---|---|
| mishra_soni_2022_sms_phishing | 5293 | 4833 | 460 | 208 | 0.3636 | 0.3284 | 0.7174 |

## By `source_label`

| Value | n | legit | susp | inconclusive | accuracy | recall legit | recall susp |
|---|---|---|---|---|---|---|---|
| ham | 4833 | 4833 | 0 | 208 | 0.3284 | 0.3284 | — |
| smishing | 460 | 0 | 460 | 0 | 0.7174 | — | 0.7174 |

## By `heuristic_topic`

| Value | n | legit | susp | inconclusive | accuracy | recall legit | recall susp |
|---|---|---|---|---|---|---|---|
| financial | 318 | 169 | 149 | 0 | 0.4434 | 0.3136 | 0.5906 |
| other | 4975 | 4664 | 311 | 208 | 0.3583 | 0.329 | 0.7781 |

## Data checks
- Exact normalised duplicates remaining: 0 rows; near-duplicate clusters: 23 (50 rows)
- Training-overlap check performed: True; overlaps: 0

## Notes
- All metrics use the classified rows (coverage.classified). Rows with no in-vocabulary term are 'inconclusive' (the component returns no class) and are counted separately in coverage, not as correct or incorrect.
- Majority-class baseline always predicts the most frequent label among the classified evaluation rows; it is a reference point, not a deployable model.
- raw_score / predict_proba is uncalibrated and was not used for metrics beyond the default argmax decision; it is not a probability of fraud.
- Subgroups with fewer than 20 classified rows report counts only.

## Dataset limitations
- Domain mismatch: messages are largely UK/Singapore SMS from c. 2000s-2010s (a systematic review describes the dataset as a partial clone of the UCI SMS Spam Collection), not Indian financial communications like the SatyaLens use case.
- 'smishing' in the source includes premium-rate, prize and subscription scams, not only financial/bank fraud; 'ham' is mostly personal chat with few genuine bank alerts.
- No campaign/group identifiers in the source; near-duplicate clusters are derived from text.
- Labels were not independently verified; some label noise is likely (OCR-derived text).
- Encoding damage in the source (e.g. the pound sign appears as '?').
- No message categories in the source; heuristic_topic is keyword-based.

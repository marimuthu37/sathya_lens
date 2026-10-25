# Text classifier evaluation — real_world_test_v1.csv

- Evaluated: 2026-10-24T07:54:52+00:00 (evaluation only; model not refit, artifact unchanged)
- Model: `tfidf-logreg-v1-a0753aa43caf` sha256 `a0753aa43caf3c10…` trained on satyalens-training-pool v2 (partly synthetic)
- Dataset: real-world (non-synthetic); sha256 `f83b8c309d9ae508…`; 5293 rows (legitimate 4833, suspicious 460)
- Classified: 5290; inconclusive (no known vocabulary): 3 {'legitimate': 3, 'suspicious': 0}
- Environment: {'python': '3.11.9', 'scikit_learn': '1.9.1', 'joblib': '1.6.0', 'numpy': '2.4.6'}

| Metric (classified rows) | Model | Majority baseline |
|---|---|---|
| Accuracy | 0.9329 | 0.913 |
| Precision — legitimate | 0.9425 | 0.913 |
| Recall — legitimate | 0.9867 | 1.0 |
| F1 — legitimate | 0.9641 | 0.9545 |
| Precision — suspicious | 0.7253 | 0.0 |
| Recall — suspicious | 0.3674 | 0.0 |
| F1 — suspicious | 0.4877 | 0.0 |
| Macro-avg F1 | 0.7259 | 0.4773 |
| Suspicious false-negative rate | 0.6326 | 1.0 |
| Legitimate false-positive rate | 0.0133 | 0.0 |

Confusion matrix (rows true, cols predicted; legitimate, suspicious): `[[4766, 64], [291, 169]]`

One row per near-duplicate cluster (5263 rows): accuracy 0.935, macro-F1 0.7251, suspicious FNR 0.6333, legitimate FPR 0.0133

## By `source`

| Value | n | legit | susp | inconclusive | accuracy | recall legit | recall susp |
|---|---|---|---|---|---|---|---|
| mishra_soni_2022_sms_phishing | 5293 | 4833 | 460 | 3 | 0.9329 | 0.9867 | 0.3674 |

## By `source_label`

| Value | n | legit | susp | inconclusive | accuracy | recall legit | recall susp |
|---|---|---|---|---|---|---|---|
| ham | 4833 | 4833 | 0 | 3 | 0.9867 | 0.9867 | — |
| smishing | 460 | 0 | 460 | 0 | 0.3674 | — | 0.3674 |

## By `heuristic_topic`

| Value | n | legit | susp | inconclusive | accuracy | recall legit | recall susp |
|---|---|---|---|---|---|---|---|
| financial | 318 | 169 | 149 | 0 | 0.739 | 0.9704 | 0.4765 |
| other | 4975 | 4664 | 311 | 3 | 0.9453 | 0.9873 | 0.3151 |

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

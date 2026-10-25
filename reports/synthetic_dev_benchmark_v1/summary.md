# Text classifier evaluation — synthetic_dev_messages_v1.csv

- Evaluated: 2026-10-24T17:57:38+00:00 (evaluation only; model not refit, artifact unchanged)
- Model: `tfidf-logreg-v1-66bab05fd245` sha256 `66bab05fd2452b22…` trained on satyalens-synthetic-financial-messages v1 (synthetic)
- Dataset: SYNTHETIC; sha256 `f53e57d1b71d2baa…`; 168 rows (legitimate 78, suspicious 90)
- Classified: 168; inconclusive (no known vocabulary): 0 {'legitimate': 0, 'suspicious': 0}
- Environment: {'python': '3.11.9', 'scikit_learn': '1.9.1', 'joblib': '1.6.0', 'numpy': '2.4.6'}

| Metric (classified rows) | Model | Majority baseline |
|---|---|---|
| Accuracy | 0.9107 | 0.5357 |
| Precision — legitimate | 0.9565 | 0.0 |
| Recall — legitimate | 0.8462 | 0.0 |
| F1 — legitimate | 0.898 | 0.0 |
| Precision — suspicious | 0.8788 | 0.5357 |
| Recall — suspicious | 0.9667 | 1.0 |
| F1 — suspicious | 0.9206 | 0.6977 |
| Macro-avg F1 | 0.9093 | 0.3488 |
| Suspicious false-negative rate | 0.0333 | 0.0 |
| Legitimate false-positive rate | 0.1538 | 1.0 |

Confusion matrix (rows true, cols predicted; legitimate, suspicious): `[[66, 12], [3, 87]]`

## By `source`

| Value | n | legit | susp | inconclusive | accuracy | recall legit | recall susp |
|---|---|---|---|---|---|---|---|
| SYN_DEV | 168 | 78 | 90 | 0 | 0.9107 | 0.8462 | 0.9667 |

## By `category`

| Value | n | legit | susp | inconclusive | accuracy | recall legit | recall susp |
|---|---|---|---|---|---|---|---|
| delivery_parcel_scam | 12 | 0 | 12 | 0 | — | — | — |
| everyday_message | 18 | 18 | 0 | 0 | — | — | — |
| fake_payment_confirmation | 18 | 0 | 18 | 0 | — | — | — |
| fee_payment_request | 18 | 0 | 18 | 0 | — | — | — |
| impersonation_urgent_money | 18 | 0 | 18 | 0 | — | — | — |
| legit_bill_reminder | 12 | 12 | 0 | 0 | — | — | — |
| legit_delivery_update | 18 | 18 | 0 | 0 | — | — | — |
| legit_otp_delivery | 6 | 6 | 0 | 0 | — | — | — |
| legit_transaction_notification | 24 | 24 | 0 | 0 | 1.0 | 1.0 | — |
| suspicious_link_credential_request | 24 | 0 | 24 | 0 | 1.0 | — | 1.0 |

## Data checks
- Exact normalised duplicates remaining: 0 rows; near-duplicate clusters: 11 (29 rows)
- Training-overlap check performed: True; overlaps: 0

## Notes
- All metrics use the classified rows (coverage.classified). Rows with no in-vocabulary term are 'inconclusive' (the component returns no class) and are counted separately in coverage, not as correct or incorrect.
- Majority-class baseline always predicts the most frequent label among the classified evaluation rows; it is a reference point, not a deployable model.
- raw_score / predict_proba is uncalibrated and was not used for metrics beyond the default argmax decision; it is not a probability of fraud.
- Subgroups with fewer than 20 classified rows report counts only.

## Dataset limitations
- Synthetic, template-generated text written by the project author; narrow, author-biased style.
- Does NOT represent the distribution of Indian financial alerts or of real scam traffic.
- Labels reflect template intent, not verified real-world outcomes.
- Metrics on this data are synthetic-only and must not be used to claim real-world performance or to select a model against the frozen test set.
- Edge-case 'expected_text_classifier_status' values depend on the model vocabulary.

# Text classifier model selection v2 (validation only — frozen test NOT used)

- Pool `training_pool_v2.csv`: 1614 rows, 415 groups, {'suspicious': 499, 'legitimate': 1115}, by source {'MWOZ': {'legitimate': 595}, 'R115': {'suspicious': 357, 'legitimate': 343}, 'SYN': {'suspicious': 142, 'legitimate': 177}}
- CV: StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=42); challenge suite `challenge_suite_v1.csv` (42 rows, author-written)
- Rule: max pooled OOF macro-F1; ties within 0.005 -> lower legitimate FPR
- **Selected: `G_char_wb_3_5`**

## Pooled out-of-fold (grouped CV) — baselines and configs

| Model | macro-F1 | fold mean±std | P/R/F1 suspicious | P/R/F1 legitimate | legit FPR | susp FNR | CM [[TN,FP],[FN,TP]] | rows w/ known feature |
|---|---|---|---|---|---|---|---|---|
| baseline_majority | 0.4086 | — | 0.0/0.0/0.0 | 0.6908/1.0/0.8171 | 0.0 | 1.0 | [[1115, 0], [499, 0]] | — |
| baseline_rules | 0.6568 | — | 0.6691/0.3727/0.4788 | 0.7657/0.9175/0.8348 | 0.0825 | 0.6273 | [[1023, 92], [313, 186]] | — |
| A_v1_config | 0.8665 | 0.8664±0.0202 | 0.7576/0.9018/0.8234 | 0.952/0.8709/0.9096 | 0.1291 | 0.0982 | [[971, 144], [49, 450]] | 1.0 |
| B_stopwords | 0.8623 | 0.8622±0.0155 | 0.7525/0.8958/0.8179 | 0.9489/0.8679/0.9066 | 0.1321 | 0.1042 | [[966, 147], [52, 447]] | 0.9988 |
| C_stopwords_unigram | 0.8695 | 0.8697±0.0203 | 0.7901/0.8597/0.8234 | 0.9345/0.8976/0.9157 | 0.1024 | 0.1403 | [[999, 114], [70, 429]] | 0.9988 |
| D_stopwords_C0.3 | 0.8529 | 0.8529±0.022 | 0.734/0.8958/0.8069 | 0.9482/0.8544/0.8989 | 0.1456 | 0.1042 | [[951, 162], [52, 447]] | 0.9988 |
| E_stopwords_C3 | 0.8772 | 0.877±0.0145 | 0.7872/0.8898/0.8354 | 0.9475/0.8922/0.919 | 0.1078 | 0.1102 | [[993, 120], [55, 444]] | 0.9988 |
| F_maxdf0.3 | 0.8668 | 0.8665±0.0208 | 0.7615/0.8958/0.8232 | 0.9494/0.8744/0.9104 | 0.1256 | 0.1042 | [[975, 140], [52, 447]] | 1.0 |
| G_char_wb_3_5 | 0.89 | 0.89±0.0268 | 0.8101/0.8978/0.8517 | 0.9519/0.9058/0.9283 | 0.0942 | 0.1022 | [[1010, 105], [51, 448]] | 1.0 |

Current deployed artifact on unseen pool rows (R115 + MWOZ): macro-F1 0.5783, legit FPR 0.5366, susp recall 0.9038, CM [[431, 499], [33, 310]]; by source {"MWOZ": {"n": 595, "accuracy": 0.3916}, "R115": {"n": 700, "accuracy": 0.7493}}

## Challenge suite — accuracy by category (author-written, intent-labelled)

| Model | overall acc | legit FPR | susp recall | delivery_notice | financial_scam | genuine_otp_warning | ordinary_conversation | payment_confirmation |
|---|---|---|---|---|---|---|---|---|
| baseline_majority | 0.619 | 0.0 | 0.0 | 1.0 | 0.0 | 1.0 | 1.0 | 1.0 |
| baseline_rules | 0.5952 | 0.1923 | 0.25 | 1.0 | 0.25 | 0.1667 | 1.0 | 1.0 |
| current_artifact | 0.6905 | 0.4615 | 0.9375 | 0.4 | 0.9375 | 0.8333 | 0.2222 | 0.8333 |
| A_v1_config | 0.8571 | 0.0385 | 0.6875 | 1.0 | 0.6875 | 0.8333 | 1.0 | 1.0 |
| B_stopwords | 0.881 | 0.0769 | 0.8125 | 1.0 | 0.8125 | 0.8333 | 1.0 | 0.8333 |
| C_stopwords_unigram | 0.8571 | 0.0769 | 0.75 | 1.0 | 0.75 | 0.8333 | 1.0 | 0.8333 |
| D_stopwords_C0.3 | 0.8333 | 0.0769 | 0.6875 | 1.0 | 0.6875 | 0.8333 | 1.0 | 0.8333 |
| E_stopwords_C3 | 0.881 | 0.0769 | 0.8125 | 1.0 | 0.8125 | 1.0 | 0.8889 | 0.8333 |
| F_maxdf0.3 | 0.8333 | 0.0385 | 0.625 | 1.0 | 0.625 | 0.8333 | 1.0 | 1.0 |
| G_char_wb_3_5 | 0.9048 | 0.1154 | 0.9375 | 0.8 | 0.9375 | 0.8333 | 1.0 | 0.8333 |

## OOF accuracy by source (configs)

| Model | SYN | R115 | MWOZ |
|---|---|---|---|
| baseline_rules | 0.6959 | 0.5614 | 0.9983 |
| A_v1_config | 0.8401 | 0.7971 | 1.0 |
| B_stopwords | 0.8433 | 0.7871 | 1.0 |
| C_stopwords_unigram | 0.8433 | 0.8086 | 1.0 |
| D_stopwords_C0.3 | 0.8213 | 0.7757 | 1.0 |
| E_stopwords_C3 | 0.8621 | 0.8129 | 1.0 |
| F_maxdf0.3 | 0.837 | 0.8 | 1.0 |
| G_char_wb_3_5 | 0.8589 | 0.8414 | 1.0 |

Caveats: validation data is partly synthetic (SYN, R115) and MWOZ is booking dialogue, so these numbers are development evidence only. The challenge suite is small and written by the same author as SYN.

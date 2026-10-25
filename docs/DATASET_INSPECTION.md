# Team-supplied datasets — inspection record (2026-10-24, inspection only)

Files live in `dataset/` (gitignored, never modified). Re-run:
`python scripts/inspect_message_sources.py` and `python scripts/inspect_phish360.py`.
Counts-only reports: `reports/dataset_inspection/`. No training pool, test set or model
was created; protocol v3 (`e97171da…9e7e`) is unchanged.

## Inventory

| File | Size | SHA-256 | Identified as |
|---|---|---|---|
| `dataset/message_detection/final_dataset_output.csv` | 9,462,365 | `1bbd1e9e…a64` | IMC25 — **byte-identical to the protocol-v3 pinned file** |
| `dataset/message_detection/Financial scams detection dataset.csv` | 47,442 | `58ec802e…6ad` | Mendeley Data znsk27yk3h v1 = v2 (hash matches the Mendeley API) |
| `dataset/image_detection/phish360.zip` | 5,315,934,869 | `64f573e6…d5f` | Phish360 (10,748 samples; **AES-encrypted** zip) |

## Sources and licences

| Dataset | Source | Licence | Attribution |
|---|---|---|---|
| IMC25 | github.com/reportsmishing/Smishing-Dataset-IMC25 @ `a6175560` | CC BY 4.0 (verified) | Agarwal, Papasavva, Suarez-Tangil, Vasek, IMC 2025, doi:10.1145/3730567.3764431 |
| Mendeley | data.mendeley.com/datasets/znsk27yk3h/2 | CC BY 4.0 (verified via API) | Ahmed, A. R., Islam, G. F. (2025), Financial scams detection dataset, Mendeley Data V2, doi:10.17632/znsk27yk3h.2 |
| Phish360 | web.cs.hacettepe.edu.tr/~selman/phish360-dataset/ (Google-Form access) | **No formal licence** — download form: "free for academic purposes", citation required; page footer "All rights reserved"; commercial use/redistribution not addressed; the GitHub MIT licence covers analysis code only. See `docs/PHISH360_ACCESS.md` | Almakhamreh & Bozkir, CrossPhire, Applied Sciences 2026, 16(2):751, doi:10.3390/app16020751 (citation required by the form) |

## Message datasets (kept separate)

**IMC25** (protocol-v3 policy applied): 20,309 English suspicious rows (banking 10,495,
others 4,586, government 1,922, delivery 1,676, telecom 1,630); 7,639 India-linked.
Excluded: 11,174 non-English; ambiguous 'spam' 1,710, 'wrong number' 332,
**'hey mum/dad' 263**, empty 81. 191 rows with ≥10-digit runs (19 Indian mobiles,
68 0-prefixed, 1 +cc, 124 other ids); after anon-v1 **0** rows with ≥4-digit runs.
Duplicates: 6,454 normalised; template clusters 7,771 (largest 4,097 — chaining).
Overlap: retired v1 172 (1 exact, 6 skeleton, 165 Jaccard), training pool v2 34,
challenge suite 0, synthetic v1 0.

**Mendeley**: 523 rows — scam 310, ham 213; 101 Bengali-script, 422 Latin-only;
Bangladesh-centred (bKash/Nagad/Taka). Collected (per authors) via forms, handwritten
submissions and observation, with consent; authors' anonymisation incomplete
(69 rows still had ≥4-digit runs; 0 after anon-v1). Ham by keyword heuristic (not
ground truth): **56 notification-like**, 58 other financial, 99 non-financial.
Duplicates 5 normalised. Overlap: retired v1 **5**, training pool v2 10, IMC25 6.

## Phish360 (index only — content encrypted)

10,748 samples: trainval 3,060 phishing / 4,917 legitimate; test 1,272 / 1,499.
47 phishing tags (test phishing all tagged generic `phish`; trainval by brand, e.g.
facebook 219, paypal 211, chase 182). Each sample: label.txt, index.html,
screen_shoot.png, url.txt (120 use `URL/URL.txt`); 1 sample has 3 extra files.
CRC32+size duplicate candidates: screenshots 8 groups (3 span trainval/test),
HTML 136 (11 span splits), URL 103 (14 span splits); none span classes.
**Not assessed** (needs the password and an AES reader): label.txt agreement, PNG
validity/dimensions, near-duplicates, domain leakage, HTML/screenshot privacy.

## Findings that affect protocol v3 (no change made)

1. `hey mum/dad` (263 rows, family-impersonation fraud) is excluded because protocol v3
   lists only five mapped categories. Including it needs a new protocol version.
2. anon-v1 leaves 41 social-media-style `@handles` (227 rows contain '@'). Masking them
   changes the anonymiser → anon-v2 + new protocol version.
3. Mendeley is not a protocol-v3 source and cannot substitute for consented donations
   (criterion L1): its "ham" is mostly not financial alerts, it is Bangladesh-centred,
   partly handwritten/re-typed, and 5 rows overlap the retired benchmark.

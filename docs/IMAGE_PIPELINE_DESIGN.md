# Website-screenshot phishing classifier — preprocessing design (DESIGN ONLY)

Status 2026-10-24: separate workstream. No image protocol exists, nothing is trained,
and Phish360 is "free for academic purposes" per its download form, with no formal
licence and no commercial permission (route to clarify: `docs/PHISH360_ACCESS.md`). This task is **webpage** phishing detection; it
does not detect forged payment receipts, fake UPI screenshots or settlement — that
remains `payment_ocr.py` (OCR + expected-field reconciliation), which is unchanged.

## Preconditions before any build
1. Licence/terms for Phish360 confirmed in writing by the dataset authors (or the form's
   terms), recorded with attribution.
2. Archive password supplied by the team via `SATYALENS_PHISH360_PASSWORD`; an
   AES-capable reader (`pyzipper`, optional) approved as a dev dependency.
3. Full content inventory (`scripts/inspect_phish360.py`) run: label agreement, PNG
   validity/dimensions, dHash near-duplicates, domain overlap, privacy indicators.
4. A written, hashed image protocol (grouping, splits, candidates, thresholds, sealing),
   mirroring protocol v3's discipline.

## Proposed pipeline
| Step | Design |
|---|---|
| Read | Stream from the zip; never extract to the repo; verify zip SHA-256 |
| Validate | 4 files per sample (case-insensitive names); folder prefix P/L must agree with label.txt; mismatches excluded and logged by sample id |
| Decode | Pillow `verify()` + `load()`; reject corrupt/truncated, < 200 px, or decompression-bomb-sized images |
| Normalise | Convert to RGB; crop to the top viewport (e.g. 1366×768 — page top carries logo/login form); letterbox-resize to the model size (e.g. 224 or 384), keep aspect ratio; ImageNet mean/std |
| Map | Record = (sample id, split, label, brand tag, registrable domain, screenshot hash); URL/HTML kept only as hashed features unless a multimodal design is approved |
| Group | Union of registrable domain, exact screenshot SHA-256, dHash equality, HTML hash and URL; shared-hosting domains handled by hash links rather than domain alone |
| Split | Do not trust the shipped trainval/test split blindly: the index-only inventory shows 3 screenshot, 11 HTML and 14 URL duplicate *candidate* groups (same CRC32 and size; not yet confirmed from content) spanning it. Re-split by group (hash assignment, as protocol v3) or remove confirmed cross-split groups from training |
| Report | Counts only: class/brand distribution, dimensions, exclusions, duplicate and leakage counts; never URLs, domains or page text |
| Privacy | Manual spot-check of a random sample of screenshots for personal data before use; exclude any with visible personal data |

## Known representation gaps
- Brands are mostly global (PayPal, Chase, Office, Facebook); few Indian banks/UPI apps.
- Test-split phishing samples carry only a generic `phish` tag (no brand).
- Screenshot style is desktop browser, not mobile.

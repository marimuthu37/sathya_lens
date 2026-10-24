# Phish360 — read-only validation checklist (for the team to run locally)

**Status 2026-10-25.** The local archive `dataset/image_detection/phish360.zip` (5,315,934,869
bytes) matches the recorded SHA-256 `64f573e6e52a9deb486f9d07d87c56e245065545fa161e8db6f080732d5ddd5f`.
All 42,995 entries are AES-encrypted and no password is held, so **every finding to date is
index-only** (names, sizes, CRC32): 10,748 samples (trainval 4,917 legitimate / 3,060 phishing;
test 1,499 / 1,272), matching the download form. No content has been read. There is **no formal
licence**: the form says "free for academic purposes" and the project page says "All rights
reserved" (see `docs/PHISH360_ACCESS.md`).

**Preconditions (do not skip).** (1) Written terms from the dataset contact are recorded in
`docs/PHISH360_ACCESS.md`. (2) The password came through that official route. (3) Approval was
given to install the optional AES reader `pyzipper` (not a project dependency). Never bypass the
encryption, never extract or copy the archive into the repository, never commit content, and do
not train an image model until this checklist passes and a split is approved.

This workstream (webpage-screenshot phishing) is separate from payment-receipt OCR
(`payment_ocr.py`) and from the message classifier.

## Run (PowerShell; the password is not echoed or written to history)

```powershell
(Get-FileHash dataset\image_detection\phish360.zip -Algorithm SHA256).Hash.ToLower()   # must equal 64f573e6...5d5f
$env:SATYALENS_PHISH360_PASSWORD = [System.Net.NetworkCredential]::new('', (Read-Host 'Phish360 password' -AsSecureString)).Password
python scripts/inspect_phish360.py            # reads the zip in place; writes reports/dataset_inspection/phish360.json + phish360_issues.csv
Remove-Item Env:SATYALENS_PHISH360_PASSWORD
```

The script refuses to run if the archive hash differs. Its reports contain counts and sample IDs
only: no URLs, domains, page text or images.

## Checklist

| # | Check | How | Pass condition | Status today |
|---|---|---|---|---|
| 1 | Archive integrity | SHA-256 above; entry count | hash matches; 42,995 entries | **index: passed** |
| 2 | File counts | report `samples`, `by_split_and_class`, `file_completeness` | 10,748 samples; split/class counts as above; every sample has label, HTML, screenshot, URL | index: counts match; 1 sample has 3 extra files |
| 3 | Labels | `label_txt_values`, `label_mismatch` | every `label.txt` agrees with its class folder | not assessed (needs content) |
| 4 | Image decoding | `png.bad_header`, `png.decode_failed`, `png_smaller_than_200px` | 0 bad or undecodable PNGs; small images listed for review | not assessed |
| 5 | Exact duplicates | SHA-256 of each PNG and HTML file | duplicate groups listed; **none spanning classes**; cross-split groups recorded | index-only *candidates* (CRC32+size): HTML 136 groups (11 cross-split), PNG 8 (3 cross-split), URL 103. **Not confirmed** |
| 6 | Perceptual duplicates | 64-bit dHash equality in the script | near-duplicate groups listed; cross-split groups recorded | not assessed |
| 7 | URL/domain leakage | registrable-domain overlap across splits and classes | domains in both trainval and test listed; a grouped split proposed if overlap is material | not assessed |
| 8 | Privacy | HTML password fields with values; e-mail-like strings; **manual** review of a random screenshot sample for personal data | counts reported; flagged samples excluded or masked before any use | not assessed |
| 9 | Licensing | written reply recorded in `docs/PHISH360_ACCESS.md` | use, derived artefacts and citation terms confirmed in writing | **open**: academic-only form text, no licence |
| 10 | Reproducibility | record archive SHA-256, script commit, Python/PIL/pyzipper versions and the report's SHA-256 | a re-run gives an identical report | to do after first content run |

## After the run

Record results and open issues in `docs/PHISH360_ACCESS.md`, and ask for approval before any
split design or model training. Treat duplicate and leakage figures as preliminary until checks
5–7 have run on decrypted content.

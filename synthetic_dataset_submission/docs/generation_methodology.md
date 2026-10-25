# Generation methodology

Source of truth: `generate_dataset.py`. This document explains its rules.

## Randomness and determinism
- One integer seed (default `20261025`). Every record draws from its own
  `random.Random("<seed>|<split>|<category or template>|<index>")`, so adding or
  removing one category never changes the records of another.
- Template-to-split and layout-family-to-split assignments use their own seeded
  streams.
- No timestamps, absolute paths or environment-dependent values are written into
  records. The manifest records the Python and Pillow versions, because PNG bytes
  can differ across Pillow versions and font files.
- Output paths are resolved and rejected if they fall outside this folder.

## Messages
1. 14 categories, each with 3 hand-written templates (`MESSAGE_CATEGORIES`) and
   a documented label and rationale.
2. Slots (`{bank}`, `{amount}`, `{link}`, `{otp}`, …) are filled from fictional
   vocabularies and random digits.
3. Each category's 3 templates are shuffled with the seed and dealt one to each of
   train/dev/test. Template id = template family, so **no template appears in two
   splits**.
4. If a filled message duplicates an earlier one (normalised text), the slots are
   re-drawn (≤ 50 attempts, otherwise generation fails loudly).
5. Hard negatives are deliberate: `security_advisory` messages use "OTP",
   "blocked" and "fraudsters"; `otp_delivery` messages contain a real-looking
   code; `everyday_money_talk` mentions payments. `ambiguous` categories have no
   asserted correct answer.

## Payment screenshots
1. 24 template families = 4 layouts × 6 fonts, shuffled with the seed and dealt
   8 per split (plus one `unreadable` family per split). A family never appears in
   two splits.
2. Per record: random currency (EUR/GBP/INR), amount (1–60,000, 2 decimals),
   merchant, reference (`TID-…`, `TXN-…`, `UTR…`), payer bank, date; the amount
   is written with a symbol (€ £ ₹, if the font has the glyph) or an ISO code.
3. `expected_payment` starts equal to the rendered values; the category then
   applies one change:

| Category | Change | Label | Expected field status |
|---|---|---|---|
| consistent | none | consistent | all match |
| amount_format_only | expected amount written ungrouped / without trailing zeros (e.g. `1234.5` for `1,234.50`) | consistent | all match |
| reference_format_only | expected reference without `-` and lower-cased (or with a `-` added) | consistent | reference = format_difference |
| no_expected_values | no expectations sent | consistent | all not_supplied |
| amount_mismatch | ×10, ÷10 or +1.00 | discrepancy | amount = mismatch |
| currency_mismatch | another of EUR/GBP/INR/USD | discrepancy | currency = mismatch |
| recipient_mismatch | another merchant | discrepancy | recipient = mismatch |
| reference_mismatch | two digits changed | discrepancy | reference = mismatch |
| placeholder_recipient | rendered recipient "Merchant Name"/"Payee Name"/"Recipient Name"; only amount and currency expected | discrepancy | recipient/reference not_supplied |
| missing_recipient | recipient line not drawn; expectation still supplied | insufficient_evidence | recipient = not_compared |
| degraded_blur | Gaussian blur σ ∈ {2.5, 3.0, 3.5} | insufficient_evidence | all not_compared |
| degraded_lowres | downscaled ×0.20–0.25 | insufficient_evidence | all not_compared |
| unreadable | blank (random shade/size) or random noise | insufficient_evidence | amount/currency not_compared |

4. Images are rendered with Pillow (`ImageDraw.text`) at 720–900 px wide, saved
   as optimised PNG with **opaque names** (first 20 hex chars of
   SHA-256(seed | scenario id)), so no category or label is visible to the
   application.

## Validation and leakage controls
`validate_dataset.py` (exit 1 on any critical failure) checks the schema; the
modality rules; that category → label equals the documented rule; that record
ids, scenario ids, texts and image bytes are unique; image hashes; that no
template family and no message *skeleton* (digits, links and named entities
masked) is shared between splits; that no label/category word occurs in any
input; and that the manifest matches the data. It also reports overlap with the
deployed text classifier's training file
(`../data/synthetic_financial_messages_v1.csv`): **0 exact, 0 skeleton** for the
shipped dataset.

## Changes made during development
- Templates that had only one varying slot could not produce 8 unique variants;
  the duplicate guard stopped generation, and a second slot (card ending, date,
  time) was added to those templates.
- Noise images were reduced to ~240×360 (random noise does not compress), and
  blank images given a random shade/size so none are byte-identical across splits.
- `amount_format_only` could, for amounts below 1,000 with two non-zero
  decimals, write the expected amount identically to the screenshot. The
  generator now adds 1,000 in that case. The shipped (default-seed) dataset was
  verified byte-identical before and after this fix (no record was affected), so
  the evaluation results still correspond to it.

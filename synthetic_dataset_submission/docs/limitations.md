# Limitations

## Of the data
- **Synthetic only.** Every message and screenshot was written or rendered by
  this project. Nothing here shows how SatyaLens performs on real messages or
  real banking-app screenshots, and no result may be quoted as real-world
  accuracy.
- **Generator / author bias.** All splits share one author's phrasing,
  vocabulary and slot fillers. Disjoint templates prevent copying, but not
  shared style; a system tuned to this author would look better here than in
  reality.
- **Small effective sample for messages.** 12 scored templates per split (one per
  category); variants of one template almost always get the same decision, so
  per-split message metrics have far less precision than n = 96 suggests. Dev
  and test differ mostly because they contain different templates.
- **Labels are rules, not verified outcomes.** "Suspicious" means the template
  was written to contain scam tactics; "legitimate" means it was written as a
  benign notice. Ambiguous cases are excluded from scoring because no label
  would be defensible.
- **Screenshots are clean renders.** No real app chrome, icons, notches,
  anti-aliasing artefacts from phone cameras, JPEG compression, Hindi/regional
  scripts, dark mode, or tampering. Degradation is limited to Gaussian blur,
  downscaling, blank and noise images.
- **Not independent of SatyaLens development (screenshots).** SatyaLens's
  extractor was previously debugged on a similar synthetic generator
  (`../reports/payment_pipeline_eval_v1`, rows/receipt/stacked layouts). Only the
  `card` layout is new to it. Screenshot results are therefore optimistic.
- **Fonts and rendering** come from Windows system fonts; byte-identical
  regeneration requires the same fonts and Pillow version.
- English only, Indian/European currency context, fictional brands only.

## Of what SatyaLens can be evaluated on
| Capability | Status here |
|---|---|
| Message text (rules + ML classifier) | evaluated (synthetic) |
| Payment screenshot OCR + expected-value comparison | evaluated (synthetic) |
| Audio deepfake detection | **not evaluable** — unavailable in SatyaLens; no records |
| Webpage / phishing-page detection | **not evaluable** — not implemented; no records |
| Payment settlement | **not determinable** from a screenshot by any method |
| Real-world accuracy, any modality | **not evaluable** with synthetic data |

## Observed weaknesses (reported, not fixed — the application was not modified)
- `fake_payment_claim` messages ("I sent you money by mistake, return it…")
  were never flagged (0/8 dev, 0/8 test).
- Hard negatives trigger false alarms: `security_advisory` (8/8 dev via the
  threat rule), `everyday_money_talk` (8/8 dev via the ML classifier),
  `delivery_update` (8/8 test via the ML classifier).
- `₹` is not in Tesseract's English model: amounts are dropped or the symbol is
  read as a leading digit (`₹19,998.66` → `719,998.66`), causing the only
  screenshot false negative and false mismatch on the test split.
- Ambiguous payment requests are always flagged (16/16), ambiguous account
  notices never (0/16) — both defensible, neither verified.

# Phish360 — official access route and licence clarification (action for the team)

Status 2026-10-24: archive AES-encrypted (all 42,995 entries); no password held;
licence not cleared for SatyaLens use. **Do not** bypass the encryption, install an AES
reader, or train on this data until both items below are resolved and approved.

## What the official sources say
| Source | Statement |
|---|---|
| Download form (Google Form linked from the project page) | "free for academic purposes"; "By downloading this data, you automatically accept to cite our paper"; contact **selman@cs.hacettepe.edu.tr**; lists 10,748 samples — trainval 7,977 / test 2,771; 6,416 legitimate / 4,332 phishing (matches our copy's index exactly) |
| Project page https://web.cs.hacettepe.edu.tr/~selman/phish360-dataset/ | No licence; footer "All rights reserved"; download via the form |
| GitHub almakhamreh/Multimodal-Phishing-Benchmarks | MIT licence for the analysis code only — not the data |
| Paper (Applied Sciences 2026, 16(2):751, doi:10.3390/app16020751) | Publisher page returned HTTP 403 to automated access; data-availability statement not yet read — check manually |

Neither the form nor the page mentions a password, commercial use or redistribution.

## Route
1. The team member who downloaded the zip checks the confirmation page / e-mail shown
   after submitting the form for a password.
2. If none: e-mail the dataset contact (draft below) from the team's own address.
3. Record the written reply (date, sender, exact terms) in `docs/PHISH360_ACCESS.md`;
   store the password only in the `SATYALENS_PHISH360_PASSWORD` environment variable,
   never in files or the repository.
4. Then ask for approval to add `pyzipper` as an optional dev dependency and run the
   full content inventory (`scripts/inspect_phish360.py`).

## Draft e-mail (not sent)
> Subject: Phish360 dataset — archive password and terms of use for a student hackathon project
>
> Dear Dr Bozkir,
>
> Our student team downloaded Phish360 through your form for SatyaLens, a financial-scam
> detection project for the RAKSHAM AI Cybersecurity Hackathon. The archive we received is
> password-protected. Could you please share the password, and clarify in writing:
> 1. whether use in a hackathon prototype counts as "academic purposes";
> 2. whether any commercial use, or demonstration to sponsors/judges, is permitted;
> 3. whether derived artefacts (trained models, aggregate statistics) may be shared;
> 4. how you would like the dataset and the CrossPhire paper to be cited.
>
> We will not redistribute the data and will cite Almakhamreh & Bozkir (2026),
> Applied Sciences 16(2):751.
>
> Kind regards, [name, team, institution]

## Until resolved
- Licence status: **academic use only per the download form; commercial use not granted.**
- Website-screenshot detection stays a separate, unbuilt workstream
  (`docs/IMAGE_PIPELINE_DESIGN.md`); it is unrelated to payment-receipt verification
  (`payment_ocr.py`) and to the message classifier.

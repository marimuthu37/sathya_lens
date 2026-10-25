# SatyaLens — Donor guide (genuine messages)

Thank you for helping. This takes about 20–30 minutes. Everything runs on **your own
computer**; nothing is uploaded by the tool. Questions or withdrawal: the project lead
named on the consent form.

## 1. Consent (before anything else)
Read and sign `CONSENT_FORM.md` (CONSENT-v1). You must be 18+. Participation is
voluntary. The signed form stays offline with the project lead — never in the repository.
Data is kept until **2026-12-31**, then deleted.

## 2. Get your donor code
```
python scripts/donate_messages.py new-donor-id
```
Write the code (e.g. `d3f9a1c07`) on your consent form. Your name is never stored with
your messages.

## 3. Choose messages — source confirmation
Donate only messages that you **personally received from an institution** and that you
are **sure are genuine**: you recognise the sender as your own bank, payment app,
merchant, courier, utility, insurer or a government service, and the message matches a
real transaction or event you know about.

| Include | Do not include |
|---|---|
| OTP messages, debit/credit alerts, UPI confirmations, card alerts, delivery notices, bill/due reminders, bank service notices | Messages from friends/family or any person · anything you are unsure about · suspected scams · screenshots · sender numbers or timestamps |

Aim for variety (different senders and categories); at most 150 messages.

## 4. Prepare the input file — OUTSIDE the project folder
Create a CSV somewhere like `C:\Users\<you>\Documents\satyalens_raw.csv` with exactly
these columns (see `donation_input_template.csv`):

`text,category,sender_type`

- **category**: `otp_alert`, `account_debit_credit_alert`, `upi_payment_confirmation`,
  `card_transaction_alert`, `delivery_notice`, `bill_or_due_reminder`,
  `bank_service_notice`, `other_institutional_financial`
- **sender_type**: `bank`, `payment_app`, `merchant_or_ecommerce`, `utility_or_telecom`,
  `courier`, `government`, `insurer_or_investment`, `other_institution`

The tool refuses input files inside the repository and any extra columns.

## 5. Mask locally
```
python scripts/donate_messages.py mask --input C:\Users\<you>\Documents\satyalens_raw.csv --donor-id <code>
```
This writes `satyalens_raw.review.csv` next to your file. OTPs, account digits, phone
numbers, UPI IDs, e-mail addresses, links and dates are already replaced with tokens
such as `numtoken` and `urltoken`. Your original text is **not** copied into it.

## 6. Review every row
Open the review file and compare each row with your phone:
- `decision`: `approve`, `approve-checked` (you checked the warnings and they are false
  alarms, e.g. a shop name), or `reject`.
- `redact`: words to remove, separated by `;` — your name, family names, your
  locality, anything that could identify you.
- You may also delete text directly in `masked_text`.

If in doubt, `reject`. Rows with unresolved warnings marked only `approve` are dropped.

## 7. Finalise
```
python scripts/donate_messages.py finalize --review C:\Users\<you>\Documents\satyalens_raw.review.csv --donor-id <code> --consent-version CONSENT-v1 --consent-confirmed
```
Only approved, masked rows are saved (in the project's `data/donations/`, which is never
committed).

## 8. Secure deletion
Delete **both** `satyalens_raw.csv` and `satyalens_raw.review.csv`, and empty the recycle
bin. Do not keep copies in e-mail, chat or cloud folders.

## 9. Withdrawal (any time)
Tell the project lead your donor code (or name). They run:
```
python scripts/donate_messages.py withdraw --donor-id <code>
```
Your messages are deleted and anything built from them is rebuilt. If a final evaluation
already used them, published aggregate numbers stay, but your messages are deleted and
the report is annotated.

## For the project lead — checking progress
```
python scripts/donation_progress.py
```
Prints counts by donor code and category only — never message text. See
`DATA_HANDLING.md` for storage and retention rules.

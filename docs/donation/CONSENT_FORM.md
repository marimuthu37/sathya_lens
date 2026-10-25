# SatyaLens — Consent to donate genuine messages (CONSENT-v1)

*Template. The project lead completes the bracketed fields before use. Signed forms
are kept offline by the project lead and are never stored in the code repository.*

**Project:** SatyaLens, financial-scam message detection (RAKSHAM AI Cybersecurity Hackathon)
**Project lead / contact for questions and withdrawal:** [name, e-mail]
**Retention end date:** 2026-12-31

## What we are asking
We ask you to donate copies of **genuine** messages that **you personally received
from institutions** — banks, payment apps, merchants, couriers, utilities, insurers or
government services — such as OTP messages, debit/credit alerts, UPI confirmations,
delivery notices and bill reminders. They help us check that our detector does **not**
wrongly flag real messages as scams.

## What we do NOT want
- Messages from friends, family or any private individual (they contain other people's data).
- Messages you are unsure are genuine.
- Sender phone numbers, timestamps, screenshots, or your contact list.
- More than 150 messages.

## How your data is protected
1. You copy the text into a file **on your own computer, outside the project repository**.
2. A local script (`scripts/donate_messages.py`) masks OTPs, account digits, phone
   numbers, UPI IDs, e-mail addresses, links and dates **before anything is saved**.
3. **You review every masked message** and delete anything else that could identify you
   (for example your name or a place). Only messages you mark as approved are kept.
4. Approved messages are stored only as masked text with a category, a sender type and
   a random donor code (e.g. `d3f9a1c07`). Your name is not linked to the data in the
   project files; only the project lead's offline consent record links code and name.
5. The data is used only to train and test the SatyaLens detector, is not shared outside
   the project team, is never committed to the code repository, and is deleted on the
   retention end date.
6. Results are reported only as aggregate numbers. Individual messages are not published.

## Voluntary participation and withdrawal
Participation is voluntary. You must be 18 or older. You may withdraw at any time by
telling the project lead your donor code (or your name); your messages are then deleted
(`scripts/donate_messages.py withdraw`). If an evaluation has already been completed with
your data, the published aggregate numbers cannot be un-published, but your messages will
be deleted and the report annotated that a donor withdrew.

## Declaration
- [ ] I am 18 or older.
- [ ] I will donate only genuine messages I personally received from institutions.
- [ ] I have reviewed every masked message before approving it.
- [ ] I understand how my data is stored, used, retained and deleted, and how to withdraw.

Donor code: ____________  Name: ____________________  Signature: ____________  Date: ________

Project lead signature: ____________  Date: ________

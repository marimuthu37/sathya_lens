"""
generate_synthetic_dataset.py — SatyaLens synthetic financial-message dataset
=============================================================================

Generates ``data/synthetic_financial_messages_v1.csv`` and its provenance
sidecar ``data/synthetic_financial_messages_v1.provenance.json``.

WHAT THIS DATA IS
-----------------
Every message is SYNTHETIC: produced by filling hand-written templates
(written for this project) with randomly chosen slot values.  No real
messages, customer records or personal information are used.  Bank names,
domains (``.example``), phone numbers and UPI handles are fictional.

It exists for pipeline development and functional testing ONLY.  Metrics
obtained on it say nothing about real-world scam-detection performance.

Label definitions
-----------------
suspicious  — The template was written to exhibit a known financial
              social-engineering pattern (credential/OTP harvesting, fake
              KYC, impersonation, advance fee, refund/overpayment, job/task
              scam, threat, payee-change fraud, remote access, etc.).
legitimate  — The template was written to resemble routine financial
              communication (transaction alerts, OTP delivery that warns not
              to share, invoices, reminders, personal payments, bank
              security advisories).

Groups
------
Each template family (one scam campaign or one kind of legitimate message)
is a ``group``.  All variants of a group stay on the same side of a
train/test split, so held-out evaluation measures generalisation to unseen
template families rather than memorisation of slot-filled near-duplicates.

Usage
-----
    python scripts/generate_synthetic_dataset.py            # writes data/...
    python scripts/generate_synthetic_dataset.py --out DIR  # custom directory
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
from pathlib import Path
from typing import Dict, List, Tuple

DATASET_NAME = "satyalens-synthetic-financial-messages"
DATASET_VERSION = "1"
SEED = 20261024
FILLS_PER_TEMPLATE = 3

# ---------------------------------------------------------------------------
# Slot values (fictional entities; shared by BOTH classes so that names of
# banks, people or merchants carry no label signal by construction)
# ---------------------------------------------------------------------------

SLOTS: Dict[str, List[str]] = {
    "bank": ["Bharat National Bank", "Sahyadri Bank", "Coastal Co-op Bank",
             "Unity Bank", "Deccan Finance Bank", "Ganga Gramin Bank"],
    "bank_short": ["BNB", "SHYB", "CCB", "UNTY", "DFB", "GGB"],
    "name": ["Rahul", "Priya", "Anil", "Meena", "Suresh", "Fatima", "John",
             "Kavya", "Arjun", "Lakshmi"],
    "amt": ["499", "1,250", "2,000", "4,999", "7,500", "12,500", "25,000",
            "49,999", "75,000", "1,20,000"],
    "acct": ["XX1234", "XX5521", "XX9087", "XX3310", "XX7764"],
    "otp": ["482913", "105377", "662048", "390151", "774820"],
    "mins": ["10", "15", "30", "45"],
    "hours": ["2", "6", "12", "24"],
    "date": ["12-Oct-26", "03/11/2026", "21-Sep-26", "07/10/2026", "28-Aug-26"],
    "time": ["09:14", "13:42", "18:05", "21:37"],
    "txn": ["629104837251", "411820935566", "735019284460", "508812374901"],
    "merchant": ["FreshMart", "CityPower Electricity", "QuickCab", "BookNest",
                 "MetroGas", "HomeFibre Broadband"],
    "upi": ["rahul.k@unitybank", "shop.fresh@bnb", "meena22@ccb",
            "anil.pay@dfb", "kavya.s@ggb"],
    "phone": ["90000 11111", "80000 22222", "70000 33333", "1800 000 0000"],
    "official_site": ["www.bharatbank.example", "www.sahyadribank.example",
                      "www.unitybank.example"],
    "scam_link": ["bnb-kyc-verify.example/u", "secure-update-bank.example/login",
                  "refund-claim-now.example/r", "bit-ly.example/kyc8",
                  "rewards-redeem.example/pts"],
    "company": ["Orbit Tech Pvt Ltd", "Nila Textiles", "Greenleaf Traders",
                "Skyline Logistics"],
    "inv": ["INV-2026-0412", "INV-2026-0877", "INV-2026-1093", "INV-2026-1550"],
}

# ---------------------------------------------------------------------------
# Templates: group_id -> (label, [templates])
# ---------------------------------------------------------------------------

TEMPLATES: Dict[str, Tuple[str, List[str]]] = {
    # ------------------------------ suspicious ------------------------------
    "S01_kyc_suspension": ("suspicious", [
        "Dear customer, your {bank} account will be suspended today as your KYC is incomplete. Update now at {scam_link}",
        "{bank_short} ALERT: KYC pending. Your account will be blocked within {hours} hours. Click {scam_link} to verify PAN details.",
        "Your {bank} netbanking is temporarily restricted. Complete e-KYC immediately using {scam_link} to avoid permanent closure.",
    ]),
    "S02_account_blocked_call": ("suspicious", [
        "Your {bank} debit card has been blocked due to suspicious activity. Call our officer on {phone} immediately to reactivate.",
        "ATTENTION: {bank} account {acct} frozen. To unfreeze, call {phone} and verify your card details right now.",
    ]),
    "S03_share_otp_cancel": ("suspicious", [
        "Sir, a payment of Rs {amt} is being processed from your account by mistake. Please tell me the OTP you just received so I can cancel it.",
        "This is {bank} fraud desk. To stop the unauthorised transaction of Rs {amt}, share the OTP sent to your mobile with me now.",
        "Hello {name}, I sent you a code by mistake, please forward the 6 digit OTP to this number, it is urgent.",
    ]),
    "S04_electricity_disconnection": ("suspicious", [
        "Dear consumer, your electricity power will be disconnected tonight at 9.30 pm because your previous month bill was not updated. Please contact electricity officer {phone} immediately.",
        "{merchant}: Your connection will be cut today due to pending bill. Pay Rs {amt} at {scam_link} or call {phone} within {mins} minutes.",
    ]),
    "S05_lottery_fee": ("suspicious", [
        "Congratulations! Your mobile number has won Rs 25 lakh in the {bank} lucky draw. Pay processing fee of Rs {amt} to claim your prize.",
        "You are selected winner of KBC lottery. To release prize money transfer Rs {amt} registration charge to {upi}.",
    ]),
    "S06_job_task_deposit": ("suspicious", [
        "Earn Rs 3000 daily from home! Just like YouTube videos. Pay a refundable joining deposit of Rs {amt} to start. Limited seats.",
        "Hi {name}, part time job offer: complete simple tasks and earn commission. First recharge your task wallet with Rs {amt} to unlock high paying tasks.",
        "HR from {company} here. You are shortlisted for online work. Security deposit of Rs {amt} required before onboarding, fully refundable.",
    ]),
    "S07_wrong_upi_transfer": ("suspicious", [
        "Hello, I transferred Rs {amt} to your UPI by mistake. Please send it back to {upi}, it was for my mother's hospital bill.",
        "Bhai galti se aapke number pe {amt} chala gaya, please wapas bhej do {upi} pe, bahut zaruri hai.",
    ]),
    "S08_overpayment_refund": ("suspicious", [
        "We accidentally paid you Rs {amt} extra for the order. Kindly send back the excess amount to our new account today.",
        "Our accounts team made an accidental transfer of Rs {amt} to you. Refund the difference to {upi} to avoid legal issues.",
    ]),
    "S09_customs_parcel": ("suspicious", [
        "Your international parcel is held at customs. Pay clearance charge of Rs {amt} at {scam_link} or it will be returned.",
        "Courier notice: package with your name contains restricted items. Pay penalty Rs {amt} to customs officer via {upi} to avoid case.",
    ]),
    "S10_digital_arrest": ("suspicious", [
        "This is CBI cyber cell. A parcel with drugs was booked on your Aadhaar. Stay on video call, do not inform anyone, and transfer Rs {amt} for verification or you will be arrested.",
        "Mumbai police: an FIR is registered against your number for money laundering. Pay security amount Rs {amt} immediately or police will come to arrest you today.",
    ]),
    "S11_tax_refund_link": ("suspicious", [
        "Income Tax Dept: You are eligible for a refund of Rs {amt}. Verify your account details at {scam_link} to receive it.",
        "ITR refund of Rs {amt} approved. Your account number is incorrect, please update it here {scam_link} before {date}.",
    ]),
    "S12_ceo_gift_cards": ("suspicious", [
        "Hi {name}, I'm in a meeting and can't talk. Need you to buy 5 Amazon gift cards of Rs {amt} each for a client. Keep this between us, send me the codes.",
        "Are you at your desk? I need a quick favour, purchase Google Play cards worth Rs {amt} and scratch the codes and send photos. Don't tell anyone in the office yet.",
    ]),
    "S13_crypto_doubling": ("suspicious", [
        "Invest Rs {amt} in our crypto trading plan and get double returns in 7 days. Send USDT to the wallet address shared on Telegram.",
        "Guaranteed 40% weekly profit with bitcoin mining. Minimum deposit Rs {amt}. Join now, offer ends tonight.",
    ]),
    "S14_remote_access_refund": ("suspicious", [
        "To process your refund of Rs {amt}, please install AnyDesk and share the 9 digit code with our support executive.",
        "Customer care here. Download the screen sharing app from the link and open your banking app so we can credit your cashback of Rs {amt}.",
    ]),
    "S15_instant_loan_fee": ("suspicious", [
        "Congratulations {name}! Your personal loan of Rs 5,00,000 is pre approved. Pay file charge Rs {amt} to {upi} to get disbursal in 10 minutes.",
        "Instant loan without documents. Pay GST and insurance fee of Rs {amt} first, loan amount will be credited today.",
    ]),
    "S16_reward_points_card": ("suspicious", [
        "Your {bank} credit card reward points worth Rs {amt} expire today. Redeem now at {scam_link} by entering your card number, expiry and CVV.",
        "Dear cardholder, {amt} reward points are about to lapse. Claim cash value at {scam_link}.",
    ]),
    "S17_family_new_number": ("suspicious", [
        "Hi mum, this is my new number, my phone broke. Can you send Rs {amt} to {upi} for an urgent bill? I'll explain later.",
        "Papa it's {name}, I lost my phone. Please transfer {amt} to my friend's account, very urgent, don't call the old number.",
    ]),
    "S18_vendor_account_change": ("suspicious", [
        "Dear Accounts, please note our bank details have changed. Kindly pay invoice {inv} to our new account mentioned below, effective immediately.",
        "From {company}: due to an audit our old account is on hold. Use this account for the pending payment of Rs {amt} and keep this confidential until the audit ends.",
    ]),
    "S19_sim_deactivation": ("suspicious", [
        "Your SIM card will be deactivated in {hours} hours as KYC is not verified. Call {phone} now to avoid service closure.",
        "Dear user, your mobile number will be blocked today due to e-KYC failure. Share the code sent to you with our executive to continue services.",
    ]),
    "S20_qr_receive_money": ("suspicious", [
        "I am sending you Rs {amt} advance for the sofa. Scan this QR code and enter your UPI PIN to receive the money.",
        "To receive your cashback of Rs {amt}, accept the collect request and enter your PIN.",
    ]),
    "S21_insurance_bonus_release": ("suspicious", [
        "Your old insurance policy bonus of Rs {amt} is pending release. Pay a one time processing charge to the officer to receive full amount.",
        "IRDA notice: unclaimed policy maturity amount found in your name. Deposit TDS of Rs {amt} to release funds.",
    ]),
    "S22_stock_tips_group": ("suspicious", [
        "Join our VIP stock tips group, 300% returns guaranteed. Pay membership Rs {amt} to {upi} and get insider calls.",
        "Our analyst recommends this IPO allotment through a special account. Transfer Rs {amt} today to secure guaranteed listing gains.",
    ]),
    "S23_fake_customer_care_pin": ("suspicious", [
        "Thank you for contacting {merchant} support. To get your refund, open the payment app and enter your UPI PIN when prompted.",
        "Refund team here. Your amount of Rs {amt} is stuck. Share your card number and the OTP you receive so we can reverse it.",
    ]),
    "S24_romance_travel_money": ("suspicious", [
        "My love, I am stuck at the airport customs with gifts for you. Please send Rs {amt} for the clearance, I will repay as soon as I land.",
        "Dear, I am an army doctor on deployment and cannot access my account. Kindly send Rs {amt} via gift card so I can book my flight to meet you.",
    ]),
    "S25_army_buyer_advance": ("suspicious", [
        "I am army officer posted at the base, want to buy your bike. First you send Rs 1 to verify, then I will send full Rs {amt}.",
        "Sir I will pay you Rs {amt} for the room advance through army account, please pay the transfer charge first to {upi}.",
    ]),
    "S26_telecom_deactivation": ("suspicious", [
        "TRAI: your number will be disconnected in 2 hours due to illegal activity. Press 9 to speak to an officer.",
        "Your mobile services will stop today. Pay pending charge of Rs {amt} at {scam_link} immediately.",
    ]),
    "S27_fake_donation": ("suspicious", [
        "Flood relief: children need food urgently. Donate any amount to {upi}. Every rupee counts, act now.",
        "Help a cancer patient today. Surgery in 3 hours, send Rs {amt} to this personal account, God bless you.",
    ]),
    "S28_scholarship_fee": ("suspicious", [
        "Your child is selected for a government scholarship of Rs 48,000. Pay verification fee of Rs {amt} to {upi} to receive it.",
        "Education dept: scholarship amount pending for your ward. Share the OTP sent to your phone to credit Rs {amt}.",
    ]),
    "S29_pension_otp": ("suspicious", [
        "Pension office: your life certificate is expiring. Tell the OTP you receive to update it, otherwise pension will be stopped.",
        "Dear pensioner, your Aadhaar linked benefit of Rs {amt} is on hold. Verify by sharing the code sent to your mobile.",
    ]),
    "S30_fake_challan": ("suspicious", [
        "Traffic e-challan of Rs {amt} pending on your vehicle. Pay immediately at {scam_link} to avoid court summons.",
        "FASTag blocked due to unpaid toll. Recharge at {scam_link} within {mins} minutes or penalty will be applied.",
    ]),
    "S31_hinglish_otp_threat": ("suspicious", [
        "Aapka {bank} account aaj band ho jayega. Abhi OTP bhejo warna paisa wapas nahi milega.",
        "Bank se bol raha hoon, aapka ATM block ho gaya hai. Card ke peeche wala number aur OTP batao.",
    ]),
    "S32_identity_confirm_card": ("suspicious", [
        "We noticed an unusual login on your {bank} profile. Confirm your identity by replying with your full card number and expiry date.",
        "{bank_short} Security: reply with your netbanking user ID and password to confirm you are the account holder.",
    ]),
    "S33_hotel_rating_task": ("suspicious", [
        "Rate hotels on our app and earn Rs 150 per review. To withdraw earnings, complete a prepaid merchant task of Rs {amt}.",
        "Your task commission of Rs {amt} is frozen. Pay the unlocking fee to withdraw, otherwise all earnings will be lost.",
    ]),
    "S34_inheritance": ("suspicious", [
        "I am a lawyer representing a late client who shares your surname. An inheritance of USD 2.5 million awaits you. Send Rs {amt} for documentation.",
        "Unclaimed foreign fund in your name. Pay the clearance certificate fee of Rs {amt} to release the transfer.",
    ]),
    "S35_cod_refund_link": ("suspicious", [
        "Your cash on delivery order was cancelled. Click {scam_link} and enter your UPI PIN to get Rs {amt} refunded.",
        "Delivery failed. Pay Rs 5 re-delivery charge at {scam_link} using your debit card details.",
    ]),
    "S36_electricity_meter_app": ("suspicious", [
        "Your smart meter update is pending. Install the meter app from the link sent by the lineman and enter your card details to avoid disconnection.",
        "Officer from {merchant} here. Download our support app and grant all permissions so I can update your bill payment.",
    ]),

    # ------------------------------ legitimate ------------------------------
    "L01_otp_do_not_share": ("legitimate", [
        "{otp} is your OTP for transaction of Rs {amt} at {merchant} on your {bank} card {acct}. Valid for {mins} mins. Do not share it with anyone.",
        "{otp} is the One Time Password for your {bank} netbanking login. {bank} never asks for your OTP. Do not share it.",
        "Use OTP {otp} to complete your UPI registration. Never share this code with anyone, including bank staff. -{bank_short}",
    ]),
    "L02_debit_alert": ("legitimate", [
        "Rs {amt} debited from A/c {acct} on {date} to {merchant}. Avl bal Rs 18,240. Not you? Call {bank} on {phone}. -{bank_short}",
        "Your {bank} a/c {acct} is debited for Rs {amt} on {date} by UPI ref {txn}. If not done by you, report via the official app.",
    ]),
    "L03_salary_credit": ("legitimate", [
        "Rs {amt} credited to your A/c {acct} on {date} by SALARY {company}. Avl bal Rs 62,410. -{bank_short}",
        "Dear customer, your account {acct} has been credited with Rs {amt} on {date} (NEFT from {company}).",
    ]),
    "L04_upi_success": ("legitimate", [
        "Paid Rs {amt} to {merchant} via UPI. UPI Ref No {txn}. Thank you for paying with {bank}.",
        "You have successfully paid Rs {amt} to {upi} on {date} at {time}. Ref {txn}.",
    ]),
    "L05_vendor_invoice": ("legitimate", [
        "Hi {name}, please find attached invoice {inv} for the October consignment. Amount Rs {amt}, payable to our usual account within 30 days.",
        "Dear team, invoice {inv} from {company} for Rs {amt} has been uploaded to the vendor portal. Payment terms are net 45 as per contract.",
    ]),
    "L06_card_statement": ("legitimate", [
        "Your {bank} credit card statement for {acct} is generated. Total due Rs {amt}, minimum due Rs 1,250, payment due date {date}.",
        "Statement alert: {bank} card {acct} total amount due Rs {amt}. Pay via the {bank} app or {official_site} by {date}.",
    ]),
    "L07_emi_reminder": ("legitimate", [
        "Reminder: EMI of Rs {amt} for loan a/c {acct} will be auto-debited on {date}. Please keep sufficient balance. -{bank_short}",
        "Your home loan EMI of Rs {amt} is due on {date}. Ignore if already paid.",
    ]),
    "L08_split_dinner": ("legitimate", [
        "Hey {name}, dinner came to Rs {amt} for the four of us, your share is a quarter. UPI me whenever, no rush.",
        "Thanks for last night! I paid the bill, can you send your part to {upi} sometime this week?",
    ]),
    "L09_rent_reminder": ("legitimate", [
        "Hi {name}, gentle reminder that rent of Rs {amt} for this month is due on the 5th. Same account as usual. Thanks!",
        "Hello, this is your landlord. Please transfer the November rent to the usual account by {date}.",
    ]),
    "L10_electricity_bill_official": ("legitimate", [
        "{merchant}: Bill of Rs {amt} generated for consumer no 4410382. Due date {date}. Pay via the official app or website.",
        "Dear consumer, your electricity bill for October is Rs {amt}. Pay before {date} to avoid late fee. Ignore if paid.",
    ]),
    "L11_security_advisory": ("legitimate", [
        "{bank} will NEVER ask for your OTP, PIN, CVV or password over call, SMS or email. Do not share them with anyone. Report fraud at {phone}.",
        "Beware of fraudsters posing as bank officials asking you to install screen sharing apps or share OTP. {bank} never makes such requests.",
        "Stay safe: do not click on links in unknown messages promising refunds or rewards. Use only {official_site} for {bank} services.",
    ]),
    "L12_merchant_refund": ("legitimate", [
        "Refund of Rs {amt} for your {merchant} order has been processed to your original payment method. It may take 5-7 working days.",
        "Your return is complete. Rs {amt} refunded to your {bank} card {acct}. Ref {txn}.",
    ]),
    "L13_fd_maturity": ("legitimate", [
        "Your fixed deposit {acct} of Rs {amt} matures on {date}. It will be auto-renewed for the same tenure unless you instruct otherwise.",
        "FD {acct} matured and Rs {amt} credited to your savings account on {date}. -{bank_short}",
    ]),
    "L14_insurance_premium": ("legitimate", [
        "Premium of Rs {amt} for your health policy is due on {date}. Renew via our app or website to stay covered.",
        "Thank you! We have received your premium of Rs {amt}. Your policy is renewed till next year.",
    ]),
    "L15_payslip_hr": ("legitimate", [
        "Hi {name}, your payslip for October is available on the HR portal. Reach out to payroll for any discrepancy.",
        "Payroll update: salaries for this month will be credited on {date}. Tax declarations are due next week.",
    ]),
    "L16_sip_confirmation": ("legitimate", [
        "Your SIP of Rs {amt} in the Flexi Cap Fund has been processed on {date}. Units will be allotted at the applicable NAV.",
        "SIP instalment of Rs {amt} debited from a/c {acct} on {date}. Statement will be emailed to you.",
    ]),
    "L17_itr_acknowledgement": ("legitimate", [
        "Your income tax return for AY 2026-27 has been filed successfully. Acknowledgement no 3410927785. Please e-verify within 30 days on the official portal.",
        "Refund of Rs {amt} for AY 2025-26 has been credited to your pre-validated bank account {acct}.",
    ]),
    "L18_login_alert": ("legitimate", [
        "New login to {bank} mobile banking on a Samsung device at {time} on {date}. If this was you, no action needed. If not, call the number on the back of your card.",
        "You logged in to netbanking at {time}. If you did not do this, block access from the official app immediately. -{bank_short}",
    ]),
    "L19_cheque_cleared": ("legitimate", [
        "Cheque no 004512 for Rs {amt} deposited in your A/c {acct} has been cleared on {date}.",
        "Your cheque of Rs {amt} issued to {company} has been paid on {date}. -{bank_short}",
    ]),
    "L20_family_sent_money": ("legitimate", [
        "Beta, sent you Rs {amt} for your books and hostel fees. Let me know once you get it.",
        "{name}, I've transferred the money for Diwali shopping to your account. Call me in the evening.",
    ]),
    "L21_order_confirmation": ("legitimate", [
        "Your order from {merchant} for Rs {amt} is confirmed and will be delivered by {date}. Paid via UPI.",
        "Order shipped! Your package with {merchant} items is out for delivery today. No payment due.",
    ]),
    "L22_loan_emi_paid": ("legitimate", [
        "Received EMI of Rs {amt} for loan {acct} on {date}. Outstanding principal is Rs 3,42,100. Thank you.",
        "Your car loan instalment of Rs {amt} was successfully debited. Next EMI date {date}.",
    ]),
    "L23_wallet_topup": ("legitimate", [
        "Rs {amt} added to your wallet successfully on {date}. Wallet balance Rs 1,840.",
        "Wallet recharge of Rs {amt} via {bank} card {acct} is successful. Txn id {txn}.",
    ]),
    "L24_colleague_reimbursement": ("legitimate", [
        "Hi {name}, finance approved your travel claim of Rs {amt}; it will be paid with this month's salary.",
        "I've reimbursed you Rs {amt} for the team lunch, check your account. Thanks for arranging it!",
    ]),
    "L25_kyc_branch_official": ("legitimate", [
        "Dear customer, your periodic KYC update is due. Please visit your nearest {bank} branch or update through the official app. We never ask for OTP or PIN.",
        "KYC reminder from {bank}: submit updated address proof at any branch before {date}. Ignore if already done. We will not call you for this.",
    ]),
    "L26_hinglish_personal": ("legitimate", [
        "Bhai {amt} bhej diya, check kar le. Kal milte hain.",
        "Mummy ne bola tha fees ke liye paise bhejne, maine {amt} transfer kar diye hain.",
    ]),
    "L27_txn_declined": ("legitimate", [
        "Your transaction of Rs {amt} at {merchant} on card {acct} was declined due to insufficient balance.",
        "UPI payment of Rs {amt} to {upi} failed. Any amount debited will be reversed within 48 hours.",
    ]),
    "L28_standing_instruction": ("legitimate", [
        "Standing instruction executed: Rs {amt} transferred from {acct} to your RD account on {date}.",
        "Auto-pay of Rs {amt} to {merchant} completed as per your mandate. Ref {txn}.",
    ]),
    "L29_maintenance_notice": ("legitimate", [
        "{bank} netbanking will be unavailable from 2 AM to 4 AM on {date} due to scheduled maintenance. Regret the inconvenience.",
        "Scheduled upgrade: UPI services may be intermittent tonight between 1 AM and 3 AM. -{bank_short}",
    ]),
    "L30_gas_booking": ("legitimate", [
        "{merchant}: Your LPG cylinder booking is confirmed. Amount Rs {amt} payable on delivery or online via the official app.",
        "Your gas cylinder will be delivered by {date}. Booking ref 77810.",
    ]),
    "L31_limit_increase": ("legitimate", [
        "Good news! Your {bank} credit card {acct} limit has been increased. No action is needed from your side.",
        "Your card {acct} is now enabled for contactless payments up to Rs 5,000. Manage limits in the {bank} app.",
    ]),
    "L32_interest_credit": ("legitimate", [
        "Interest of Rs {amt} credited to your savings A/c {acct} for the quarter ending {date}. -{bank_short}",
        "Quarterly interest has been credited to your account. View details in your passbook.",
    ]),
    "L33_society_maintenance": ("legitimate", [
        "Dear residents, society maintenance of Rs {amt} for Q3 is due by {date}. Pay to the society account or at the office.",
        "Reminder from the RWA: maintenance dues can be paid via the community app. Receipts will be emailed.",
    ]),
    "L34_client_payment_received": ("legitimate", [
        "Hi {name}, we've released payment of Rs {amt} against invoice {inv}. UTR {txn}. Please confirm receipt.",
        "Payment for invoice {inv} has been processed today through NEFT to your registered account.",
    ]),
    "L35_pension_credit": ("legitimate", [
        "Pension for October Rs {amt} credited to your A/c {acct} on {date}. -{bank_short}",
        "Dear pensioner, your life certificate has been accepted. Pension will continue without interruption.",
    ]),
    "L36_autopay_scheduled": ("legitimate", [
        "Autopay scheduled: Rs {amt} to {merchant} will be debited on {date}. Cancel anytime from the app.",
        "Your broadband bill of Rs {amt} will be paid automatically on {date} via your saved mandate.",
    ]),
    "L37_internal_urgent_approval": ("legitimate", [
        "Hi team, urgent: please approve purchase order PO-5521 in the finance portal before end of day so the vendor payment goes out on time.",
        "Reminder: quarter close is today, submit all pending expense claims in the portal by 6 PM.",
    ]),
}

_SLOT_RE = re.compile(r"\{(\w+)\}")


def _normalise(text: str) -> str:
    """Same normalisation the training code uses for de-duplication."""
    text = text.lower()
    text = re.sub(r"\d+(?:[.,]\d+)*", "0", text)
    return re.sub(r"\s+", " ", text).strip()


def generate(seed: int = SEED) -> List[Dict[str, str]]:
    rng = random.Random(seed)
    rows: List[Dict[str, str]] = []
    seen = set()
    for group, (label, templates) in TEMPLATES.items():
        for t_idx, template in enumerate(templates):
            for _ in range(FILLS_PER_TEMPLATE):
                text = _SLOT_RE.sub(lambda m: rng.choice(SLOTS[m.group(1)]), template)
                key = _normalise(text)
                if key in seen:
                    continue
                seen.add(key)
                rows.append({
                    "id": f"{group}-{t_idx}-{len(rows):04d}",
                    "text": text,
                    "label": label,
                    "group": group,
                })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "data"))
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"synthetic_financial_messages_v{DATASET_VERSION}.csv"

    rows = generate(args.seed)
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["id", "text", "label", "group"])
        writer.writeheader()
        writer.writerows(rows)

    counts: Dict[str, int] = {}
    for r in rows:
        counts[r["label"]] = counts.get(r["label"], 0) + 1
    groups = {r["group"] for r in rows}

    provenance = {
        "name": DATASET_NAME,
        "version": DATASET_VERSION,
        "synthetic": True,
        "source": "Hand-written templates in scripts/generate_synthetic_dataset.py, "
                  "slot-filled with fictional values. No real messages or personal data.",
        "licence": "Created for this project; no third-party data. Distributed under the "
                   "repository's licence (pyproject.toml declares MIT).",
        "generator_seed": args.seed,
        "rows": len(rows),
        "groups": len(groups),
        "label_counts": counts,
        "label_definitions": {
            "suspicious": "Template written to exhibit a known financial social-engineering pattern.",
            "legitimate": "Template written to resemble routine financial communication.",
        },
        "intended_use": "Pipeline development and functional testing ONLY.",
        "limitations": [
            "Synthetic, template-generated text; vocabulary and style are narrow and author-biased.",
            "Labels reflect the template author's intent, not verified real-world outcomes.",
            "Metrics on this data do NOT indicate real-world scam-detection performance.",
            "English and a little romanised Hindi only.",
        ],
        "csv_sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest(),
    }
    prov_path = csv_path.with_suffix(".provenance.json")
    prov_path.write_text(json.dumps(provenance, indent=2), encoding="utf-8")

    print(f"Wrote {len(rows)} rows ({counts}) across {len(groups)} groups -> {csv_path}")
    print(f"Provenance -> {prov_path}")


if __name__ == "__main__":
    main()

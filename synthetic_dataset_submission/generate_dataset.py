"""
generate_dataset.py — reproducible synthetic dataset for SatyaLens.

    python generate_dataset.py                       # default seed 20261025
    python generate_dataset.py --seed 7 --out dataset_seed7
    python generate_dataset.py --no-images           # text records only

Two SUPPORTED SatyaLens workflows are covered:

  payment_screenshot  PNG screenshots rendered with Pillow from fictional payment
                      details, plus optional user-supplied "expected payment
                      details" (the same input the SatyaLens UI collects).
  message_text        Short financial messages (suspicious, legitimate look-alikes
                      and ambiguous cases).

Audio and webpage/phishing-page detection are NOT implemented in SatyaLens, so no
records are generated for them (see docs/limitations.md).

Record layout (one JSON object per line in dataset/synthetic_dataset.jsonl):

  input         what the application is given — nothing else may be sent to it
  ground_truth  labels, rationale and rendered values — evaluation only
  meta          split, category, template family/id, scenario id, render params

Determinism: every random choice comes from random.Random seeded with a string
derived from (seed, split, category, index), so the same seed and configuration
always produce byte-identical records, labels and PNGs (for a fixed Pillow
version and font files). No timestamps are written.

All output is written inside this folder; other paths are refused.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA_VERSION = "1.0.0"
DEFAULT_SEED = 20261025
SPLITS = ("train", "dev", "test")
FONT_DIR = Path("C:/Windows/Fonts")


@dataclass
class Config:
    seed: int = DEFAULT_SEED
    images: bool = True
    # Images per image-category per split (train, dev, test).
    images_per_category: dict = field(default_factory=lambda: {"train": 4, "dev": 4, "test": 6})
    # Variants per message template per split; each template lives in ONE split.
    texts_per_template: dict = field(default_factory=lambda: {"train": 8, "dev": 8, "test": 8})


# ═══════════════════════════════════════════════════════════════════════════════
# Fictional vocabulary (no real people, banks, accounts or URLs)
# ═══════════════════════════════════════════════════════════════════════════════

BANKS = ["Kaveri Demo Bank", "Northstar Sample Bank", "Indus Example Bank", "Coral Test Bank",
         "Meridian Mock Bank", "Saffron Placeholder Bank"]
PEOPLE = ["Riya Mehta", "Arjun Rao", "Neha Iyer", "Kabir Sen", "Ananya Das", "Vikram Nair",
          "Priya Kulkarni", "Rohan Bhat"]
MERCHANTS = ["Riya Demo Stores", "Kabir Textiles", "Nova Book House", "Green Leaf Cafe",
             "Asha Medicals", "Orbit Electronics", "Sunrise Bakery", "Blue Harbor Travels",
             "Meadow Pet Care", "Indigo Print Works", "Lotus Hardware Mart", "Tulsi Grocers",
             "Peak Fitness Club", "Saffron Kitchen", "Maple Toy Shop", "Quartz Car Rentals"]
PLACEHOLDER_RECIPIENTS = ["Merchant Name", "Payee Name", "Recipient Name"]
BRANDS = ["ShopKart Demo", "StreamBox Sample", "QuickRide Example", "FoodHub Mock"]
COURIERS = ["SwiftShip Demo", "ParcelPoint Sample", "BlueDart Example"]
UTILITIES = ["CityPower Demo", "AquaLine Sample", "GasGrid Example"]
AGENCIES = ["Cyber Cell (fictional)", "Customs Department (fictional)", "Telecom Authority (fictional)"]
LINK_HOSTS = ["secure-kyc-update.example", "bank-verify-now.example", "reward-claim.example",
              "account-restore.example"]
CURRENCY_SYMBOL = {"EUR": "€", "GBP": "£", "INR": "₹"}


# ═══════════════════════════════════════════════════════════════════════════════
# Message templates — 3 per category; each template is one template family and is
# assigned to exactly one split, so no template text appears in two splits.
# ═══════════════════════════════════════════════════════════════════════════════

MESSAGE_CATEGORIES = {
    # ── suspicious ────────────────────────────────────────────────────────────
    "account_block_urgency": ("suspicious", "Threatens imminent blocking and demands immediate action or card details.", [
        "Dear customer, your {bank} account will be blocked today due to pending verification. Call {phone} immediately to avoid suspension.",
        "ALERT: {bank} has frozen your account ending {last4}. Reactivate within {hours} hours by verifying your details at {link}",
        "Your {bank} debit card ending {last4} is restricted from today. To restore access urgently, reply with your card number and expiry date.",
    ]),
    "otp_request": ("suspicious", "Asks the recipient to disclose a one-time code.", [
        "Hello, this is {person} from {bank} customer care. Please share the OTP sent to your phone to stop an unauthorised debit of Rs {amount}.",
        "To receive your refund of Rs {amount}, tell us the 6 digit code you just received. Our agent will call you in 5 minutes.",
        "{bank}: your reward points worth Rs {amount} expire tonight. Send the OTP you received to {phone} to redeem them now.",
    ]),
    "kyc_link": ("suspicious", "Pressures the recipient to 'update' identity details through a link.", [
        "{bank} KYC update pending. Your account will be suspended in 24 hours. Complete KYC here: {link}",
        "Dear user, your PAN is not linked with {bank}. Update now at {link} or your net banking will stop working.",
        "Final reminder from {bank}: re-verify your Aadhaar details today using {link} to keep your account active.",
    ]),
    "fake_payment_claim": ("suspicious", "Claims a payment was made and asks for money or approval in return.", [
        "Hi, I sent Rs {amount} to your number by mistake. Please return it to {upi} quickly, it was for my mother's hospital bill.",
        "Payment of Rs {amount} has been credited to your account. To complete the transfer, approve the collect request from {upi} now.",
        "Sir I have paid Rs {amount} for the item, screenshot attached. Please release the parcel today, I am in a hurry.",
    ]),
    "fee_or_prize_demand": ("suspicious", "Demands an upfront fee for a prize/job, or threatens disconnection unless paid to an individual.", [
        "Congratulations! Your number won Rs {prize} in the {brand} lucky draw. Pay a processing fee of Rs {fee} at {link} to claim.",
        "You have been selected for a work-from-home job paying Rs {prize} per week. Pay Rs {fee} registration to {upi} to start today.",
        "Your electricity connection will be disconnected tonight at 9:30 pm due to an unpaid bill. Pay Rs {fee} immediately to officer {person} at {phone}.",
    ]),
    "impersonation_money": ("suspicious", "Impersonates family, an employer or an authority to request money or secrecy.", [
        "Hi Mum, I dropped my phone and this is my new number. Can you send Rs {amount} to {upi} today? I'll explain later.",
        "This is {person}, your manager. I'm in a meeting, buy gift cards worth Rs {amount} for a client and send me the codes. Keep this between us.",
        "Hello, I am calling from the {agency} office. A case is registered against your Aadhaar. Transfer Rs {amount} as security deposit to avoid arrest.",
    ]),
    # ── legitimate (several are hard negatives that share alarming words) ─────
    "otp_delivery": ("legitimate", "Delivers a code to the account holder and warns not to share it; asks for nothing.", [
        "{otp} is your OTP for the {bank} transaction of Rs {amount}. It is valid for 10 minutes. Do not share it with anyone.",
        "Your one-time password for logging in to {brand} is {otp}. {bank} staff will never ask you for this code.",
        "Use {otp} to verify your phone number on {brand}. If you did not request this, you can ignore this message.",
    ]),
    "transaction_alert": ("legitimate", "Informational debit/credit notification with no request.", [
        "Rs {amount} debited from your {bank} account XX{last4} on {date} towards {merchant}. Available balance Rs {balance}.",
        "{bank}: Rs {amount} credited to A/c XX{last4} on {date} by NEFT from {person}. Ref {ref}.",
        "You paid Rs {amount} to {merchant} using UPI on {date}. UPI Ref No {upiref}. - {bank}",
    ]),
    "bill_reminder": ("legitimate", "Due-date reminder that points to the official app or counter, no link or individual.", [
        "Your {utility} bill of Rs {amount} for {month} is due on {date}. You can pay it in the {utility} app or at any service centre.",
        "Reminder: your {brand} subscription of Rs {amount} renews on {date}. Manage it anytime in your account settings.",
        "{bank} credit card statement generated. Total due Rs {amount}, minimum due Rs {fee}, payment due date {date}.",
    ]),
    "delivery_update": ("legitimate", "Delivery status with no payment or credential request.", [
        "Your {courier} parcel {awb} is out for delivery today. The delivery partner may call you before arriving.",
        "{courier}: your order {awb} has been delivered on {date}. Thank you for shopping with us.",
        "Your {brand} order will arrive by {date}. Track it in the app under My Orders.",
    ]),
    "security_advisory": ("legitimate", "Hard negative: mentions OTPs, blocking or fraud, but only to warn or confirm a user action.", [
        "{bank} security notice {date}: we never ask for your OTP, PIN or password by call, SMS or email. If anyone asks, do not share and report it to us.",
        "Security tip from {bank} ({date}): fraudsters may say your account will be blocked. Do not click unknown links; use only the official {bank} app.",
        "Your {bank} debit card ending {last4} was blocked at your request on {date}. A replacement card will be delivered in 7 days.",
    ]),
    "everyday_money_talk": ("legitimate", "Ordinary conversation between acquaintances that mentions money.", [
        "Hey, I'll send you Rs {amount} for the dinner tonight. Thanks for booking the table!",
        "Mum, I paid the school fee of Rs {amount} online today. The receipt is in my email.",
        "Can you remind me to pay {person} back the Rs {amount} I owe for the train tickets?",
    ]),
    # ── ambiguous: correct outcome is human review / insufficient evidence ────
    "unverified_payment_request": ("ambiguous", "A plausible payment request whose legitimacy cannot be judged from the text alone.", [
        "Hi, this is {person} from the guest house. Please pay the balance of Rs {amount} today to confirm your booking.",
        "Your order of Rs {amount} is on hold. Please complete the payment to continue.",
        "Please transfer Rs {amount} for the society maintenance by {date}. Account details as shared earlier.",
    ]),
    "unclear_account_notice": ("ambiguous", "An account notice that could be genuine or a lure; it asks for no credential.", [
        "Your {bank} account details were updated on {date}. If you did not make this change, please contact your branch.",
        "We noticed a login to your {brand} account from a new device at {time} on {date}. If this was you, no action is needed.",
        "{bank}: a request to change your registered mobile number was received on {date}. Visit your branch if you did not request this.",
    ]),
}

# ═══════════════════════════════════════════════════════════════════════════════
# Payment-screenshot categories
# ═══════════════════════════════════════════════════════════════════════════════
# label: discrepancy          → SatyaLens should recommend review (Verify First)
#        consistent           → nothing to flag (the correct decision is NOT
#                               Verify First; it must also not claim genuineness)
#        insufficient_evidence→ the screenshot cannot be checked reliably; the
#                               correct outcome is withheld comparisons /
#                               Insufficient Evidence, never Low Concern

IMAGE_CATEGORIES = {
    "consistent":            ("consistent", "All four supplied expectations equal the rendered values."),
    "amount_format_only":    ("consistent", "Expected amount written without grouping/decimals (1234.5 vs 1,234.50); same value."),
    "reference_format_only": ("consistent", "Expected reference differs only in separators/case."),
    "no_expected_values":    ("consistent", "No expectations supplied; the screenshot itself is unremarkable."),
    "amount_mismatch":       ("discrepancy", "Expected amount differs from the rendered amount."),
    "currency_mismatch":     ("discrepancy", "Expected currency differs from the rendered currency."),
    "recipient_mismatch":    ("discrepancy", "Expected recipient differs from the rendered recipient."),
    "reference_mismatch":    ("discrepancy", "Expected reference differs from the rendered one by two digits."),
    "placeholder_recipient": ("discrepancy", "Rendered recipient is a template placeholder such as 'Merchant Name'."),
    "missing_recipient":     ("insufficient_evidence", "Recipient line not rendered but an expected recipient is supplied; it cannot be compared."),
    "degraded_blur":         ("insufficient_evidence", "Consistent values, Gaussian blur sigma 2.5–3.5."),
    "degraded_lowres":       ("insufficient_evidence", "Consistent values, downscaled to 20–25 %."),
    "unreadable":            ("insufficient_evidence", "Blank or random-noise image; nothing can be read."),
}
LAYOUTS = ("rows", "receipt", "stacked", "card")
FONTS = ("arial", "verdana", "georgia", "calibri", "segoeui", "times")


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _rng(seed: int, *parts) -> random.Random:
    """Independent deterministic stream per record (string seeds hash with SHA-512)."""
    return random.Random("|".join([str(seed), *map(str, parts)]))


def _digits(rng: random.Random, n: int) -> str:
    return "".join(rng.choice("0123456789") for _ in range(n))


def _amount(rng: random.Random, lo: int, hi: int) -> Decimal:
    return Decimal(rng.randint(lo * 100, hi * 100)) / 100


def _fmt(value: Decimal) -> str:
    return f"{value:,.2f}"


def _inside(path: Path) -> Path:
    """Refuse to write outside this folder."""
    p = path.resolve()
    if p != HERE and HERE not in p.parents:
        raise SystemExit(f"Refusing to write outside {HERE}: {p}")
    return p


def normalise_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def skeleton(text: str) -> str:
    """Text with digits, amounts, codes and slot fillers masked — for near-duplicate checks."""
    t = normalise_text(text)
    t = re.sub(r"https?://\S+|\b[\w.-]+\.example\S*", "<link>", t)
    t = re.sub(r"\b[\w.]+@[\w]+\b", "<upi>", t)
    t = re.sub(r"\d[\d,./:-]*", "<n>", t)
    for vocab in (BANKS, PEOPLE, MERCHANTS, BRANDS, COURIERS, UTILITIES, AGENCIES):
        for v in vocab:
            t = t.replace(v.lower(), "<e>")
    return t


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ═══════════════════════════════════════════════════════════════════════════════
# Message records
# ═══════════════════════════════════════════════════════════════════════════════

def _fill(template: str, rng: random.Random) -> str:
    slots = {
        "bank": rng.choice(BANKS), "person": rng.choice(PEOPLE), "merchant": rng.choice(MERCHANTS),
        "brand": rng.choice(BRANDS), "courier": rng.choice(COURIERS), "utility": rng.choice(UTILITIES),
        "agency": rng.choice(AGENCIES),
        "amount": _fmt(_amount(rng, 50, 49999)).replace(".00", ""), "balance": _fmt(_amount(rng, 100, 250000)),
        "prize": f"{rng.choice([25000, 50000, 100000, 250000]):,}", "fee": f"{rng.choice([99, 199, 499, 999, 1499]):,}",
        "phone": "+91 9" + _digits(rng, 4) + " " + _digits(rng, 5),
        "last4": _digits(rng, 4), "hours": str(rng.choice([2, 6, 12, 24])), "otp": _digits(rng, 6),
        "link": "https://" + rng.choice(LINK_HOSTS) + "/" + _digits(rng, 5),
        "upi": rng.choice(["helpdesk", "refund", "pay", "rahul", "support"]) + _digits(rng, 3) + "@okdemo",
        "date": f"{rng.randint(1, 28):02d}/{rng.randint(1, 12):02d}/2026",
        "time": f"{rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}",
        "month": rng.choice(["January", "March", "June", "September", "October"]),
        "ref": "N" + _digits(rng, 10), "upiref": _digits(rng, 12), "awb": "AWB" + _digits(rng, 9),
    }
    return template.format(**slots)


def build_message_records(cfg: Config) -> list[dict]:
    records = []
    split_rng = _rng(cfg.seed, "message-template-assignment")
    seen = set()
    for category in sorted(MESSAGE_CATEGORIES):
        label, rationale, templates = MESSAGE_CATEGORIES[category]
        order = list(range(len(templates)))
        split_rng.shuffle(order)                        # which template goes to which split
        for split, t_idx in zip(SPLITS, order):
            template_id = f"msg:{category}:t{t_idx}"
            for i in range(cfg.texts_per_template[split]):
                rng = _rng(cfg.seed, split, template_id, i)
                for _attempt in range(50):              # regenerate on an exact duplicate
                    text = _fill(templates[t_idx], rng)
                    if normalise_text(text) not in seen:
                        break
                else:
                    raise RuntimeError(f"Could not produce a unique message for {template_id}")
                seen.add(normalise_text(text))
                records.append({
                    "modality": "message_text",
                    "input": {"message_text": text, "media_type": "text/plain"},
                    "ground_truth": {"label": label, "rationale": rationale},
                    "meta": {"split": split, "category": category, "template_family": template_id,
                             "template_id": template_id, "scenario_id": f"{template_id}#{i}"},
                })
    return records


# ═══════════════════════════════════════════════════════════════════════════════
# Payment-screenshot records
# ═══════════════════════════════════════════════════════════════════════════════

def _font(name: str, size: int):
    from PIL import ImageFont
    path = FONT_DIR / f"{name}.ttf"
    if not path.exists():
        raise SystemExit(f"Font {path} not found. Run with --no-images, or on Windows with standard fonts.")
    return ImageFont.truetype(str(path), size)


def _has_glyph(font, ch: str) -> bool:
    """True if the font draws `ch` differently from a codepoint it certainly lacks."""
    from PIL import Image, ImageDraw

    def bitmap(c):
        im = Image.new("L", (80, 80), 0)
        ImageDraw.Draw(im).text((10, 10), c, fill=255, font=font)
        return im.tobytes()
    return bitmap(ch) != bitmap("\U0010FFFD")


def _reference(rng: random.Random) -> str:
    kind = rng.choice(["TID", "TXN", "UTR"])
    return "UTR" + _digits(rng, 12) if kind == "UTR" else f"{kind}-" + _digits(rng, rng.choice([6, 7, 8]))


def _perturb(ref: str, rng: random.Random) -> str:
    chars = list(ref)
    for i in rng.sample([i for i, c in enumerate(chars) if c.isdigit()], 2):
        chars[i] = str((int(chars[i]) + rng.randint(1, 9)) % 10)
    return "".join(chars)


def _render(r: dict, path: Path) -> None:
    from PIL import Image, ImageDraw, ImageFilter
    p, v = r["meta"]["render"], r["ground_truth"]["rendered"]
    kind = p["degradation"]
    if kind in ("blank", "noise"):
        nrng = random.Random(p["noise_seed"])
        # Small (noise does not compress); size and shade vary per record so no two
        # "unreadable" inputs are byte-identical across splits.
        w, h = 240 + nrng.randrange(40), 360 + nrng.randrange(40)
        shade = 235 + nrng.randrange(21)
        img = Image.new("RGB", (w, h), (shade, shade, shade))
        if kind == "noise":
            img.putdata([tuple(nrng.randrange(256) for _ in range(3)) for _ in range(w * h)])
        img.save(path, optimize=True)
        return
    size = p["font_size"]
    font, small, bold = _font(p["font"], size), _font(p["font"], max(14, size - 8)), _font(p["font"], size + 6)
    fg, bg = tuple(p["fg"]), tuple(p["bg"])
    rows = []                                   # (label, value) pairs or ("", text) lines
    if p["layout"] == "rows":
        rows = [("One-time payment of", v["amount_text"]), ("To", v["recipient"]), ("From", v["payer"]),
                ("Transaction Ref No.", v["reference"]), ("Description", "Order " + v["order"])]
        img = Image.new("RGB", (900, 170 + len(rows) * (size + 40)), bg)
        d = ImageDraw.Draw(img)
        d.text((40, 30), "Payment confirmation", fill=fg, font=bold)
        d.text((40, 36 + size + 10), "Please confirm the following payment details", fill=fg, font=small)
        y = 160
        for label, value in rows:
            if value is None:
                continue
            d.text((40, y), label, fill=fg, font=font)
            d.text((860 - d.textlength(value, font=font), y), value, fill=fg, font=font)
            y += size + 40
    elif p["layout"] == "receipt":
        lines = [("Payment Successful", bold), (f"Paid to {v['recipient']}" if v["recipient"] else None, font),
                 (f"Amount: {v['amount_text']}", font), (f"UTR: {v['reference']}", font),
                 (f"Date {v['date']}", small), (f"From {v['payer']}", small)]
        img = Image.new("RGB", (760, 80 + len(lines) * (size + 34)), bg)
        d = ImageDraw.Draw(img)
        y = 40
        for text, f in lines:
            if text:
                d.text((50, y), text, fill=fg, font=f)
                y += size + 34
    elif p["layout"] == "stacked":
        pairs = [("Recipient", v["recipient"]), ("Amount", v["amount_text"]),
                 ("Reference", v["reference"]), ("Status", "Completed")]
        img = Image.new("RGB", (720, 120 + len(pairs) * (2 * size + 46)), bg)
        d = ImageDraw.Draw(img)
        d.text((40, 30), "Transfer details", fill=fg, font=bold)
        y = 110
        for label, value in pairs:
            if value is None:
                continue
            d.text((40, y), label, fill=(110, 110, 110), font=small)
            d.text((40, y + size), value, fill=fg, font=font)
            y += 2 * size + 46
    else:  # card: big amount, "to <name>", reference
        big = _font(p["font"], size + 18)
        img = Image.new("RGB", (720, 420 + 2 * size), bg)
        d = ImageDraw.Draw(img)
        d.text((40, 30), "Payment sent", fill=fg, font=bold)
        d.text((40, 100), v["amount_text"], fill=fg, font=big)
        y = 130 + size + 30
        if v["recipient"]:
            d.text((40, y), f"to {v['recipient']}", fill=fg, font=font)
        d.text((40, y + size + 30), f"Ref: {v['reference']}", fill=fg, font=font)
        d.text((40, y + 2 * size + 60), v["date"], fill=(110, 110, 110), font=small)
    if kind and kind.startswith("blur"):
        img = img.filter(ImageFilter.GaussianBlur(p["blur_sigma"]))
    elif kind and kind.startswith("downscale"):
        f = p["scale"]
        img = img.resize((max(1, int(img.width * f)), max(1, int(img.height * f))))
    img.save(path, optimize=True)


def _image_families(seed: int) -> dict[str, list[str]]:
    """Deal the (layout, font) families round-robin into disjoint per-split sets."""
    fams = [f"img:{layout}:{font}" for layout in LAYOUTS for font in FONTS]
    _rng(seed, "image-family-assignment").shuffle(fams)
    return {s: sorted(fams[i::len(SPLITS)]) for i, s in enumerate(SPLITS)}


def build_image_records(cfg: Config) -> list[dict]:
    families = _image_families(cfg.seed)
    records = []
    for split in SPLITS:
        for c_idx, category in enumerate(sorted(IMAGE_CATEGORIES)):
            label, rationale = IMAGE_CATEGORIES[category]
            for i in range(cfg.images_per_category[split]):
                rng = _rng(cfg.seed, split, category, i)
                if category == "unreadable":
                    family = f"img:unreadable:{split}"
                    layout = font = None
                else:
                    family = families[split][(c_idx + i) % len(families[split])]
                    _, layout, font = family.split(":")
                currency = rng.choice(["EUR", "GBP", "INR"])
                value = _amount(rng, 1, 60000)
                if category == "amount_format_only" and format(value.normalize(), "f") == _fmt(value):
                    # e.g. 12.34 is written the same both ways; add 1000 so the grouped
                    # form "1,012.34" really differs from "1012.34". Deterministic, no
                    # extra random draw (records for other categories are unaffected).
                    value += 1000
                ref = _reference(rng)
                recipient = rng.choice(MERCHANTS)
                style = rng.choice(["symbol", "symbol", "code"])
                amount_text = f"{currency} {_fmt(value)}"
                if font and style == "symbol" and _has_glyph(_font(font, 28), CURRENCY_SYMBOL[currency]):
                    amount_text = f"{CURRENCY_SYMBOL[currency]}{_fmt(value)}"
                rendered = {"amount": str(value), "currency": currency, "amount_text": amount_text,
                            "recipient": recipient, "reference": ref, "payer": rng.choice(BANKS),
                            "order": "#" + _digits(rng, 6),
                            "date": f"{rng.randint(1, 28):02d}/{rng.randint(1, 12):02d}/2026"}
                expected = {"amount": _fmt(value), "currency": currency, "recipient": recipient, "reference": ref}
                status = {"amount": "match", "currency": "match", "recipient": "match", "reference": "match"}
                render = {"layout": layout, "font": font, "font_size": rng.choice([24, 26, 28, 30, 32]),
                          "fg": rng.choice([[20, 20, 20], [40, 40, 60], [0, 0, 0]]),
                          "bg": rng.choice([[255, 255, 255], [248, 246, 240], [240, 244, 250]]),
                          "degradation": None}

                if category == "amount_format_only":
                    expected["amount"] = format(value.normalize(), "f")      # "1234.5" for 1,234.50
                elif category == "reference_format_only":
                    expected["reference"] = ref.replace("-", "").lower() if "-" in ref else ref[:3] + "-" + ref[3:]
                    status["reference"] = "format_difference"
                elif category == "no_expected_values":
                    expected = None
                    status = {k: "not_supplied" for k in status}
                elif category == "amount_mismatch":
                    new = rng.choice([value * 10, (value / 10).quantize(Decimal("0.01")), value + Decimal("1.00")])
                    expected["amount"] = _fmt(new if new != value and new > 0 else value + 5)
                    status["amount"] = "mismatch"
                elif category == "currency_mismatch":
                    expected["currency"] = rng.choice([c for c in ("EUR", "GBP", "INR", "USD") if c != currency])
                    status["currency"] = "mismatch"
                elif category == "recipient_mismatch":
                    expected["recipient"] = rng.choice([m for m in MERCHANTS if m != recipient])
                    status["recipient"] = "mismatch"
                elif category == "reference_mismatch":
                    expected["reference"] = _perturb(ref, rng)
                    status["reference"] = "mismatch"
                elif category == "placeholder_recipient":
                    rendered["recipient"] = rng.choice(PLACEHOLDER_RECIPIENTS)
                    expected = {"amount": _fmt(value), "currency": currency, "recipient": None, "reference": None}
                    status = {"amount": "match", "currency": "match", "recipient": "not_supplied",
                              "reference": "not_supplied"}
                elif category == "missing_recipient":
                    rendered["recipient"] = None
                    status["recipient"] = "not_compared"
                elif category == "degraded_blur":
                    render.update(degradation="blur", blur_sigma=rng.choice([2.5, 3.0, 3.5]))
                    status = {k: "not_compared" for k in status}
                elif category == "degraded_lowres":
                    render.update(degradation="downscale", scale=rng.choice([0.2, 0.22, 0.25]))
                    status = {k: "not_compared" for k in status}
                elif category == "unreadable":
                    render.update(degradation=rng.choice(["blank", "noise"]), noise_seed=rng.randint(0, 2**31))
                    rendered = {k: None for k in rendered}
                    expected = {"amount": _fmt(value), "currency": currency, "recipient": None, "reference": None}
                    status = {"amount": "not_compared", "currency": "not_compared",
                              "recipient": "not_supplied", "reference": "not_supplied"}
                if expected is not None:
                    expected = {k: v for k, v in expected.items() if v is not None}

                records.append({
                    "modality": "payment_screenshot",
                    "input": {"image_path": None, "media_type": "image/png", "expected_payment": expected},
                    "ground_truth": {"label": label, "rationale": rationale,
                                     "expected_field_status": status, "rendered": rendered},
                    "meta": {"split": split, "category": category, "template_family": family,
                             "template_id": family, "scenario_id": f"{family}:{category}#{i}",
                             "render": render},
                })
    return records


# ═══════════════════════════════════════════════════════════════════════════════
# Assembly
# ═══════════════════════════════════════════════════════════════════════════════

def generate(cfg: Config, out_dir: Path) -> dict:
    out_dir = _inside(out_dir)
    img_dir = out_dir / "images"
    out_dir.mkdir(parents=True, exist_ok=True)
    records = build_message_records(cfg) + (build_image_records(cfg) if cfg.images else [])

    image_hashes = {}
    for n, r in enumerate(records):
        r["record_id"] = f"rec-{n:05d}"
        r["schema_version"] = SCHEMA_VERSION
        if r["modality"] == "payment_screenshot":
            # Opaque name: nothing about the category or label is visible to the app.
            name = sha256_bytes(f"{cfg.seed}|{r['meta']['scenario_id']}".encode())[:20] + ".png"
            img_dir.mkdir(exist_ok=True)
            path = _inside(img_dir / name)
            _render(r, path)
            r["input"]["image_path"] = f"images/{name}"
            image_hashes[name] = sha256_bytes(path.read_bytes())
        r["meta"]["image_sha256"] = image_hashes.get(Path(r["input"].get("image_path") or "").name)

    lines = [json.dumps(r, ensure_ascii=False, sort_keys=True) for r in records]
    jsonl = ("\n".join(lines) + "\n").encode("utf-8")
    (_inside(out_dir / "synthetic_dataset.jsonl")).write_bytes(jsonl)

    def dist(key, sub=None):
        out = {}
        for r in records:
            k = (r["meta"]["split"], r["modality"], r["ground_truth"]["label"] if key == "label" else r["meta"]["category"])
            out.setdefault(k[0], {}).setdefault(k[1], {}).setdefault(k[2], 0)
            out[k[0]][k[1]][k[2]] += 1
        return out

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "dataset": "satyalens-synthetic-submission",
        "synthetic": True,
        "seed": cfg.seed,
        "config": asdict(cfg),
        "record_count": len(records),
        "image_count": len(image_hashes),
        "split_counts": {s: sum(r["meta"]["split"] == s for r in records) for s in SPLITS},
        "label_distribution": dist("label"),
        "category_distribution": dist("category"),
        "template_families": {s: sorted({r["meta"]["template_family"] for r in records if r["meta"]["split"] == s})
                              for s in SPLITS},
        "files": {"synthetic_dataset.jsonl": sha256_bytes(jsonl),
                  "images_digest": sha256_bytes("".join(f"{k}:{v}\n" for k, v in sorted(image_hashes.items())).encode())},
        "generator_sha256": sha256_bytes(Path(__file__).read_bytes()),
        "environment": {"python": sys.version.split()[0], "pillow": _pillow_version()},
        "methodology": "docs/generation_methodology.md",
        "label_rules": {
            "message_text": {c: {"label": v[0], "rationale": v[1]} for c, v in sorted(MESSAGE_CATEGORIES.items())},
            "payment_screenshot": {c: {"label": v[0], "rationale": v[1]} for c, v in sorted(IMAGE_CATEGORIES.items())},
        },
        "not_generated": {"audio": "SatyaLens audio detection is unavailable",
                          "webpage": "SatyaLens webpage/phishing-page detection is not implemented"},
    }
    (_inside(out_dir / "dataset_manifest.json")).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def _pillow_version() -> str:
    try:
        import PIL
        return PIL.__version__
    except ImportError:
        return "not installed"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED, help=f"random seed (default {DEFAULT_SEED})")
    ap.add_argument("--out", type=Path, default=HERE / "dataset",
                    help="output directory, must be inside this folder (default: dataset/)")
    ap.add_argument("--no-images", action="store_true", help="generate message records only")
    ap.add_argument("--images-per-category", default="4,4,6", help="train,dev,test (default 4,4,6)")
    ap.add_argument("--texts-per-template", default="8,8,8", help="train,dev,test (default 8,8,8)")
    a = ap.parse_args(argv)

    def triple(s):
        vals = [int(x) for x in s.split(",")]
        if len(vals) != 3 or min(vals) < 1:
            ap.error("expected three positive integers: train,dev,test")
        return dict(zip(SPLITS, vals))

    out = a.out if a.out.is_absolute() else (Path.cwd() / a.out)
    cfg = Config(seed=a.seed, images=not a.no_images,
                 images_per_category=triple(a.images_per_category),
                 texts_per_template=triple(a.texts_per_template))
    m = generate(cfg, out)
    print(json.dumps({"out": str(out.resolve()), "records": m["record_count"], "images": m["image_count"],
                      "splits": m["split_counts"], "seed": m["seed"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

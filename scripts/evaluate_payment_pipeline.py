"""
evaluate_payment_pipeline.py — reproducible evaluation of the payment-screenshot
pipeline through the PRODUCTION path.

    python scripts/evaluate_payment_pipeline.py --split dev
    python scripts/evaluate_payment_pipeline.py --split test      # held-out; runs once
    python scripts/evaluate_payment_pipeline.py --print-config-hash

Run with the backend virtualenv (it needs FastAPI, the backend app, Tesseract
and this package):  "person 2/person2_app/backend/.venv/Scripts/python.exe"

What is measured
----------------
Each sample is a SYNTHETIC screenshot rendered here with known ground truth.
It goes through the real HTTP API in-process (FastAPI TestClient, temporary
SQLite DB and upload dir, ANALYSIS_MODULE_ENABLED=true):
create case → consent → upload → POST .../analyse with expected payment
details → GET .../analysis → POST .../decision.

Primary task (legible images): "does this screenshot need human review?"
  positive = the screenshot conflicts with the user's expectation (amount,
             currency, recipient or reference) or names a placeholder recipient
  predicted positive = decision category verify_first or high_risk
Secondary: per-field comparison status vs. the status implied by construction;
extraction exact-match per field; image-quality warning behaviour on deliberately
degraded and unreadable images.

Dev / held-out separation
-------------------------
dev  : seed 1001, fonts Arial/Tahoma/Verdana, layouts rows+receipt, name list A.
test : seed 2002, fonts Calibri/Segoe UI/Georgia/Trebuchet, layouts rows+receipt
       + stacked (never in dev), name list B (disjoint), different values.
The quality thresholds in payment_ocr.py were set before this script existed,
on an Arial receipt and five mock-ups; dev may be used to find errors, test may
not. The test split refuses to overwrite an existing result.

Everything here is synthetic. Results do NOT measure real-world performance.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "person 2" / "person2_app" / "backend"
REPORT_DIR = ROOT / "reports" / "payment_pipeline_eval_v1"
FONT_DIR = Path("C:/Windows/Fonts")

NAMES_A = ["Riya Demo Stores", "Kabir Textiles", "Nova Book House", "Green Leaf Cafe",
           "Asha Medicals", "Orbit Electronics", "Sunrise Bakery", "Blue Harbor Travels",
           "Meadow Pet Care", "Indigo Print Works", "Copper Kettle Foods", "Silverline Optics"]
NAMES_B = ["Lotus Hardware Mart", "Meera Florists", "Northwind Stationers", "Peak Fitness Club",
           "Saffron Kitchen", "Willow Dental Clinic", "Crescent Mobile Hub", "Amber Yoga Studio",
           "Harbour View Hotel", "Maple Toy Shop", "Quartz Car Rentals", "Tulsi Grocers"]
PLACEHOLDERS = ["Merchant Name", "Payee Name", "Recipient Name"]
BANKS = ["Examplebank", "Demo Bank", "Sample Pay"]

CONFIG = {
    "version": 1,
    "dev": {"seed": 1001, "fonts": ["arial", "tahoma", "verdana"], "layouts": ["rows", "receipt"],
            "names": "A", "n_legible": 64, "n_degraded": 16, "n_unreadable": 4,
            "amount_range": [1, 5000]},
    "test": {"seed": 2002, "fonts": ["calibri", "segoeui", "georgia", "trebuc"],
             "layouts": ["rows", "receipt", "stacked"], "names": "B",
             "n_legible": 120, "n_degraded": 30, "n_unreadable": 6,
             "amount_range": [1, 80000]},
    "scenarios": ["consistent", "amount_mismatch", "currency_mismatch", "recipient_mismatch",
                  "reference_mismatch", "reference_format_only", "placeholder_recipient",
                  "no_expected_values"],
    "degradations": ["blur_2.5", "blur_3.5", "downscale_0.22"],
    "positive_rule": "decision.category in {verify_first, high_risk}",
}
# Held-out split v2: generated AFTER v1's failures were inspected (stacked layout
# recipient extraction was fixed in response). New seed, fonts used nowhere else,
# a third disjoint name list. Its own hash and protocol (protocol_v2.json); the
# v1 CONFIG above is left byte-identical so v1's registered hash stays valid.
NAMES_C = ["Banyan Book Depot", "Cobalt Bike Works", "Dhruv Opticals", "Ember Coffee Roasters",
           "Falcon Courier Hub", "Garnet Jewellers", "Hilltop Pharmacy", "Iris Beauty Lounge",
           "Juniper Pet Clinic", "Kestrel Music House", "Lumen Lighting Co", "Mosaic Tile Gallery"]
CONFIG_V2 = {
    "version": 2,
    "test2": {"seed": 3003, "fonts": ["times", "timesbd", "consola"],
              "layouts": ["rows", "receipt", "stacked"], "names": "C",
              "n_legible": 120, "n_degraded": 30, "n_unreadable": 6,
              "amount_range": [1, 60000]},
}
CURRENCY_SYMBOL = {"EUR": "€", "GBP": "£", "INR": "₹"}


def split_config(split: str) -> dict:
    return CONFIG_V2[split] if split in CONFIG_V2 else CONFIG[split]
POSITIVE_SCENARIOS = {"amount_mismatch", "currency_mismatch", "recipient_mismatch",
                      "reference_mismatch", "placeholder_recipient"}


def config_hash(split: str = "test") -> str:
    blob = json.dumps(CONFIG, sort_keys=True) + json.dumps([NAMES_A, NAMES_B, PLACEHOLDERS, BANKS])
    if split in CONFIG_V2:
        blob += json.dumps(CONFIG_V2, sort_keys=True) + json.dumps(NAMES_C)
    return hashlib.sha256(blob.encode()).hexdigest()


# ── Rendering ─────────────────────────────────────────────────────────────────

def _font(name: str, size: int):
    from PIL import ImageFont
    return ImageFont.truetype(str(FONT_DIR / f"{name}.ttf"), size)


def _has_glyph(font, ch: str) -> bool:
    """True if `font` draws `ch` differently from a codepoint it certainly lacks."""
    from PIL import Image, ImageDraw

    def bitmap(c):
        im = Image.new("L", (80, 80), 0)
        ImageDraw.Draw(im).text((10, 10), c, fill=255, font=font)
        return im.tobytes()
    return bitmap(ch) != bitmap("\U0010FFFD")


def _fmt_amount(value: Decimal) -> str:
    return f"{value:,.2f}"


def _money(font, currency: str, value: Decimal, style: str) -> str:
    sym = CURRENCY_SYMBOL[currency]
    if style == "code" or not _has_glyph(font, sym):
        return f"{currency} {_fmt_amount(value)}"
    return f"{sym}{_fmt_amount(value)}"


def render(sample: dict, path: Path) -> None:
    from PIL import Image, ImageDraw
    size = sample["font_size"]
    font, small = _font(sample["font"], size), _font(sample["font"], max(14, size - 8))
    bold = _font(sample["font"], size + 6)
    shown = sample["shown"]
    layout = sample["layout"]
    if layout == "rows":
        rows = [("One-time payment of", shown["amount_text"]), ("To", shown["recipient"]),
                ("From", shown["payer"]), ("Transaction Ref No.", shown["reference"]),
                ("Description", f"Order {shown['order']}")]
        h = 160 + len(rows) * (size + 40)
        img = Image.new("RGB", (900, h), sample["bg"])
        d = ImageDraw.Draw(img)
        d.text((40, 30), "Payment confirmation", fill=sample["fg"], font=bold)
        d.text((40, 30 + size + 16), "Please confirm the following payment details", fill=sample["fg"], font=small)
        y = 150
        for label, value in rows:
            d.text((40, y), label, fill=sample["fg"], font=font)
            w = d.textlength(value, font=font)
            d.text((860 - w, y), value, fill=sample["fg"], font=font)
            y += size + 40
    elif layout == "receipt":
        lines = [("Payment Successful", bold), (f"Paid to {shown['recipient']}", font),
                 (f"Amount: {shown['amount_text']}", font), (f"UTR: {shown['reference']}", font),
                 (f"Date {shown['date']}", small), (f"From {shown['payer']}", small)]
        h = 80 + len(lines) * (size + 34)
        img = Image.new("RGB", (760, h), sample["bg"])
        d = ImageDraw.Draw(img)
        y = 40
        for text, f in lines:
            d.text((50, y), text, fill=sample["fg"], font=f)
            y += size + 34
    else:  # stacked (held-out only): label above value
        pairs = [("Recipient", shown["recipient"]), ("Amount", shown["amount_text"]),
                 ("Reference", shown["reference"]), ("Status", "Completed")]
        h = 120 + len(pairs) * (2 * size + 46)
        img = Image.new("RGB", (720, h), sample["bg"])
        d = ImageDraw.Draw(img)
        d.text((40, 30), "Transfer details", fill=sample["fg"], font=bold)
        y = 110
        for label, value in pairs:
            d.text((40, y), label, fill=(110, 110, 110), font=small)
            d.text((40, y + size), value, fill=sample["fg"], font=font)
            y += 2 * size + 46
    img = _degrade(img, sample.get("degradation"))
    img.save(path)


def _degrade(img, kind):
    from PIL import ImageFilter
    if not kind:
        return img
    name, amount = kind.split("_")
    if name == "blur":
        return img.filter(ImageFilter.GaussianBlur(float(amount)))
    f = float(amount)
    return img.resize((max(1, int(img.width * f)), max(1, int(img.height * f))))


def _unreadable(path: Path, rng: random.Random, i: int) -> None:
    from PIL import Image
    if i % 2 == 0:
        Image.new("RGB", (600, 900), (250, 250, 250)).save(path)
    else:
        img = Image.new("RGB", (600, 900))
        img.putdata([tuple(rng.randrange(256) for _ in range(3)) for _ in range(600 * 900)])
        img.save(path)


# ── Sample construction ──────────────────────────────────────────────────────

def _reference(rng: random.Random) -> str:
    kind = rng.choice(["TID", "TXN", "UTR"])
    if kind == "UTR":
        return "UTR" + "".join(rng.choice("0123456789") for _ in range(12))
    return f"{kind}-" + "".join(rng.choice("0123456789") for _ in range(rng.choice([6, 7, 8])))


def _perturb_reference(ref: str, rng: random.Random) -> str:
    chars = list(ref)
    digits = [i for i, c in enumerate(chars) if c.isdigit()]
    for i in rng.sample(digits, 2):
        chars[i] = str((int(chars[i]) + rng.randint(1, 9)) % 10)
    return "".join(chars)


def build_samples(split: str) -> list[dict]:
    cfg = split_config(split)
    rng = random.Random(cfg["seed"])
    names = {"A": NAMES_A, "B": NAMES_B, "C": NAMES_C}[cfg["names"]]
    samples = []

    def base(i: int, scenario: str) -> dict:
        currency = rng.choice(["EUR", "GBP", "INR"])
        lo, hi = cfg["amount_range"]
        value = Decimal(rng.randint(lo * 100, hi * 100)) / 100
        font = rng.choice(cfg["fonts"])
        f = _font(font, 28)
        recipient = rng.choice(names)
        ref = _reference(rng)
        s = {
            "id": f"{split}-{i:03d}", "split": split, "scenario": scenario,
            "layout": rng.choice(cfg["layouts"]), "font": font,
            "font_size": rng.choice([24, 26, 28, 30, 32]),
            "fg": rng.choice([(20, 20, 20), (40, 40, 60), (0, 0, 0)]),
            "bg": rng.choice([(255, 255, 255), (248, 246, 240), (240, 244, 250)]),
            "degradation": None,
            "shown": {"currency": currency, "amount": str(value),
                      "amount_text": _money(f, currency, value, rng.choice(["symbol", "symbol", "code"])),
                      "recipient": recipient, "payer": rng.choice(BANKS), "reference": ref,
                      "order": "#" + "".join(rng.choice("0123456789") for _ in range(6)),
                      "date": f"{rng.randint(1, 28):02d}/{rng.randint(1, 12):02d}/2026"},
        }
        exp = {"amount": _fmt_amount(value), "currency": currency, "recipient": recipient, "reference": ref}
        truth = {"amount": "match", "currency": "match", "recipient": "match", "reference": "match"}
        if scenario == "amount_mismatch":
            new = rng.choice([value * 10, (value / 10).quantize(Decimal("0.01")), value + Decimal("1.00")])
            if new == value or new <= 0:
                new = value + Decimal("5.00")
            exp["amount"], truth["amount"] = _fmt_amount(new), "mismatch"
        elif scenario == "currency_mismatch":
            exp["currency"] = rng.choice([c for c in ("EUR", "GBP", "INR", "USD") if c != currency])
            truth["currency"] = "mismatch"
        elif scenario == "recipient_mismatch":
            exp["recipient"] = rng.choice([n for n in names if n != recipient])
            truth["recipient"] = "mismatch"
        elif scenario == "reference_mismatch":
            exp["reference"], truth["reference"] = _perturb_reference(ref, rng), "mismatch"
        elif scenario == "reference_format_only":
            exp["reference"] = ref.replace("-", "").lower() if "-" in ref else ref[:3] + "-" + ref[3:]
            truth["reference"] = "format_difference"
        elif scenario == "placeholder_recipient":
            s["shown"]["recipient"] = rng.choice(PLACEHOLDERS)
            exp = {"amount": exp["amount"], "currency": currency}
            truth = {"amount": "match", "currency": "match", "recipient": "not_supplied",
                     "reference": "not_supplied"}
        elif scenario == "no_expected_values":
            exp = None
            truth = {k: "not_supplied" for k in truth}
        s["expected"], s["truth_status"] = exp, truth
        s["positive"] = scenario in POSITIVE_SCENARIOS
        return s

    scen = CONFIG["scenarios"]
    for i in range(cfg["n_legible"]):
        samples.append(base(i, scen[i % len(scen)]))
    for j in range(cfg["n_degraded"]):
        s = base(cfg["n_legible"] + j, rng.choice(["consistent", "amount_mismatch"]))
        s["degradation"] = CONFIG["degradations"][j % len(CONFIG["degradations"])]
        samples.append(s)
    for k in range(cfg["n_unreadable"]):
        samples.append({"id": f"{split}-u{k:02d}", "split": split, "scenario": "unreadable",
                        "layout": "none", "font": "none", "degradation": "blank" if k % 2 == 0 else "noise",
                        "expected": {"amount": "10.00", "currency": "EUR"}, "positive": False,
                        "truth_status": {"amount": "not_compared", "currency": "not_compared",
                                         "recipient": "not_supplied", "reference": "not_supplied"},
                        "shown": {}, "_k": k})
    return samples


# ── Running through the production path ──────────────────────────────────────

def run_pipeline(samples: list[dict], image_dir: Path) -> list[dict]:
    tmp = Path(tempfile.mkdtemp(prefix="satyalens_payeval_"))
    os.environ.update({
        "DATABASE_URL": f"sqlite:///{(tmp / 'eval.db').as_posix()}",
        "UPLOAD_DIR": str(tmp / "uploads"), "ANALYSIS_MODULE_ENABLED": "true", "DEBUG": "false",
    })
    sys.path.insert(0, str(BACKEND))
    os.chdir(BACKEND)
    from fastapi.testclient import TestClient
    from app.main import app
    from app.services import analysis as svc
    if type(svc._adapter).__name__ != "Person1Adapter":
        raise SystemExit(f"Real adapter not loaded ({type(svc._adapter).__name__}); refusing to evaluate.")

    rng = random.Random(7)
    out = []
    with TestClient(app) as c:
        for n, s in enumerate(samples, 1):
            img = image_dir / f"{s['id']}.png"
            if s["scenario"] == "unreadable":
                _unreadable(img, rng, s["_k"])
            else:
                render(s, img)
            cid = c.post("/api/cases", json={"description": f"synthetic eval {s['id']}"}).json()["id"]
            c.post(f"/api/cases/{cid}/evidence/consent", json={"acknowledged": True})
            ev = c.post(f"/api/cases/{cid}/evidence", files={"file": (img.name, img.read_bytes(), "image/png")})
            eid = ev.json()["id"]
            body = {"expected_payment": s["expected"]} if s["expected"] else None
            post = c.post(f"/api/cases/{cid}/evidence/{eid}/analyse", json=body)
            res = c.get(f"/api/cases/{cid}/evidence/{eid}/analysis").json()
            dec = c.post(f"/api/cases/{cid}/decision").json()
            pay = res.get("payment") or {}
            out.append({**{k: v for k, v in s.items() if not k.startswith("_")},
                        "http_analyse": post.status_code, "analysis_status": res.get("status"),
                        "decision": dec.get("category"),
                        "findings": [f["label"] for f in res.get("findings", [])],
                        "statuses": {x["field"]: x["status"] for x in pay.get("comparisons", [])},
                        "extracted": {x["field"]: (x["extracted"], x["extracted_confidence"])
                                      for x in pay.get("comparisons", [])},
                        "extraction_status": pay.get("extraction_status"),
                        "quality": (pay.get("quality") or {}).get("assessment"),
                        "quality_warnings": [(w["code"], w["severity"])
                                             for w in (pay.get("quality") or {}).get("warnings", [])]})
            if n % 20 == 0:
                print(f"  {n}/{len(samples)}", flush=True)
    return out


# ── Metrics ───────────────────────────────────────────────────────────────────

def _ratio(a: int, b: int):
    return round(a / b, 4) if b else None   # None = undefined (empty denominator)


def binary_metrics(rows: list[dict]) -> dict:
    tp = sum(r["positive"] and r["pred"] for r in rows)
    fp = sum((not r["positive"]) and r["pred"] for r in rows)
    fn = sum(r["positive"] and not r["pred"] for r in rows)
    tn = sum((not r["positive"]) and not r["pred"] for r in rows)
    p, rec = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
    return {"n": len(rows), "positives": tp + fn, "negatives": tn + fp,
            "confusion_matrix": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
            "precision": p, "recall": rec,
            "f1": round(2 * p * rec / (p + rec), 4) if p and rec else (0.0 if p == 0 or rec == 0 else None),
            "false_positive_rate": _ratio(fp, fp + tn), "false_negative_rate": _ratio(fn, fn + tp)}


def _expected_extracted(r: dict, field: str):
    sh = r["shown"]
    return {"amount": sh.get("amount_text"), "currency": sh.get("amount_text"),
            "recipient": sh.get("recipient"), "reference": sh.get("reference")}[field]


def compute_metrics(results: list[dict]) -> dict:
    for r in results:
        r["pred"] = r["decision"] in ("verify_first", "high_risk")
    legible = [r for r in results if not r["degradation"]]
    degraded = [r for r in results if r["degradation"] in CONFIG["degradations"]]
    unreadable = [r for r in results if r["scenario"] == "unreadable"]

    by = lambda key: {k: binary_metrics([r for r in legible if r[key] == k])
                      for k in sorted({r[key] for r in legible})}
    per_scenario = {}
    for sc in CONFIG["scenarios"]:
        rows = [r for r in legible if r["scenario"] == sc]
        per_scenario[sc] = {"n": len(rows), "flagged": sum(r["pred"] for r in rows),
                            "expected_flag": sc in POSITIVE_SCENARIOS,
                            "decisions": dict(Counter(r["decision"] for r in rows))}

    # Per-field comparison status vs construction (legible, field supplied)
    field_status = {}
    for field in ("amount", "currency", "recipient", "reference"):
        rows = [r for r in legible if r["truth_status"][field] != "not_supplied"]
        outcome = Counter((r["truth_status"][field], r["statuses"].get(field)) for r in rows)
        field_status[field] = {
            "n": len(rows),
            "exact_status_agreement": _ratio(sum(n for (t, a), n in outcome.items() if t == a), len(rows)),
            "outcomes": {f"truth={t} -> got={a}": n for (t, a), n in sorted(outcome.items(), key=str)},
            "false_mismatch": sum(n for (t, a), n in outcome.items() if t != "mismatch" and a == "mismatch"),
            "withheld_not_compared": sum(n for (t, a), n in outcome.items() if a == "not_compared"),
        }

    # Extraction exact match on legible images (raw OCR string vs rendered string)
    extraction = {}
    for field in ("amount", "recipient", "reference"):
        read = wrong = missing = high = high_wrong = 0
        for r in legible:
            truth = _expected_extracted(r, field)
            got, conf = r["extracted"].get(field, (None, None))
            if got is None:
                missing += 1
                continue
            read += 1
            ok = got == truth
            wrong += not ok
            if conf == "high":
                high += 1
                high_wrong += not ok
        extraction[field] = {"n": len(legible), "read": read, "missing": missing,
                             "exact_match_rate_of_all": _ratio(read - wrong, len(legible)),
                             "wrong_when_read": wrong, "high_confidence_reads": high,
                             "high_confidence_but_wrong": high_wrong,
                             "wrong_rate_among_high_confidence": _ratio(high_wrong, high)}

    def has_severe(r):
        return any(sev == "severe" for _, sev in r["quality_warnings"])
    quality = {
        "degraded_n": len(degraded),
        "degraded_with_severe_warning": sum(has_severe(r) for r in degraded),
        "degraded_severe_detection_rate": _ratio(sum(has_severe(r) for r in degraded), len(degraded)),
        "degraded_payment_mismatch_findings": sum(
            any(lbl.endswith("you expected") for lbl in r["findings"]) for r in degraded),
        "degraded_decisions": dict(Counter(r["decision"] for r in degraded)),
        "by_degradation": {d: {"n": len(rs := [r for r in degraded if r["degradation"] == d]),
                               "severe": sum(has_severe(r) for r in rs)}
                           for d in CONFIG["degradations"]},
        "legible_n": len(legible),
        "legible_with_severe_warning": sum(has_severe(r) for r in legible),
        "legible_false_severe_rate": _ratio(sum(has_severe(r) for r in legible), len(legible)),
        "legible_assessments": dict(Counter(r["quality"] for r in legible)),
    }
    unread = {"n": len(unreadable),
              "decisions": dict(Counter(r["decision"] for r in unreadable)),
              "extraction_status": dict(Counter(r["extraction_status"] for r in unreadable)),
              "any_comparison_ran": sum(any(s in ("match", "mismatch", "format_difference", "partial_match")
                                            for s in r["statuses"].values()) for r in unreadable),
              "low_concern": sum(r["decision"] == "low_concern" for r in unreadable)}
    return {
        "review_needed_legible": binary_metrics(legible),
        "review_needed_by_layout": by("layout"),
        "review_needed_by_font": by("font"),
        "per_scenario": per_scenario,
        "field_comparison_status": field_status,
        "extraction_exact_match": extraction,
        "image_quality": quality,
        "unreadable": unread,
        "low_concern_anywhere": sum(r["decision"] == "low_concern" for r in results),
        "http_errors": sum(r["http_analyse"] != 202 for r in results),
    }


def write_report(split: str, results: list[dict], metrics: dict) -> Path:
    out = REPORT_DIR / split
    out.mkdir(parents=True, exist_ok=True)
    meta = {"split": split, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "config_hash": config_hash(split), "config": split_config(split), "synthetic": True}
    (out / "metrics.json").write_text(json.dumps({"meta": meta, "metrics": metrics}, indent=2), encoding="utf-8")
    with (out / "predictions.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "scenario", "layout", "font", "degradation", "positive", "decision", "findings",
                    "statuses", "extracted", "quality", "quality_warnings", "shown", "expected"])
        for r in results:
            w.writerow([r["id"], r["scenario"], r["layout"], r["font"], r["degradation"], r["positive"],
                        r["decision"], json.dumps(r["findings"], ensure_ascii=False),
                        json.dumps(r["statuses"]), json.dumps(r["extracted"], ensure_ascii=False),
                        r["quality"], json.dumps(r["quality_warnings"]),
                        json.dumps(r["shown"], ensure_ascii=False), json.dumps(r["expected"], ensure_ascii=False)])
    m = metrics["review_needed_legible"]
    cm = m["confusion_matrix"]
    lines = [f"# Payment pipeline evaluation — {split} split (SYNTHETIC)", "",
             f"- Generated {meta['generated_at']}; config hash `{meta['config_hash'][:16]}…`",
             "- Path: real FastAPI routes → Person1Adapter → Tesseract → payment_checks → decision engine",
             "- All screenshots are synthetic renderings; **not** a measure of real-world performance.", "",
             "## Review-needed detection (legible images)", "",
             "Positive = screenshot conflicts with the user's expectation or names a placeholder recipient. "
             "Predicted positive = decision `verify_first`/`high_risk`.", "",
             "| n | TP | FP | FN | TN | precision | recall | F1 | FPR | FNR |", "|---|---|---|---|---|---|---|---|---|---|",
             f"| {m['n']} | {cm['tp']} | {cm['fp']} | {cm['fn']} | {cm['tn']} | {m['precision']} | {m['recall']} | "
             f"{m['f1']} | {m['false_positive_rate']} | {m['false_negative_rate']} |", "",
             "## Per scenario", "", "| scenario | n | expected flag | flagged | decisions |", "|---|---|---|---|---|"]
    for sc, v in metrics["per_scenario"].items():
        lines.append(f"| {sc} | {v['n']} | {v['expected_flag']} | {v['flagged']} | {v['decisions']} |")
    lines += ["", "## By layout", "", "| layout | n | precision | recall | FPR | FNR |", "|---|---|---|---|---|---|"]
    for k, v in metrics["review_needed_by_layout"].items():
        lines.append(f"| {k} | {v['n']} | {v['precision']} | {v['recall']} | {v['false_positive_rate']} | "
                     f"{v['false_negative_rate']} |")
    lines += ["", "## Extraction exact match (raw OCR string == rendered string)", "",
              "| field | n | read | missing | exact-match rate | wrong when read | high-conf reads | high-conf but wrong |",
              "|---|---|---|---|---|---|---|---|"]
    for f, v in metrics["extraction_exact_match"].items():
        lines.append(f"| {f} | {v['n']} | {v['read']} | {v['missing']} | {v['exact_match_rate_of_all']} | "
                     f"{v['wrong_when_read']} | {v['high_confidence_reads']} | {v['high_confidence_but_wrong']} |")
    lines += ["", "## Field comparison status vs construction", ""]
    for f, v in metrics["field_comparison_status"].items():
        lines.append(f"- **{f}** (n={v['n']}): agreement {v['exact_status_agreement']}; false mismatches "
                     f"{v['false_mismatch']}; withheld {v['withheld_not_compared']}; {v['outcomes']}")
    q, u = metrics["image_quality"], metrics["unreadable"]
    lines += ["", "## Image quality", "",
              f"- Degraded images with a severe warning: {q['degraded_with_severe_warning']}/{q['degraded_n']} "
              f"({q['by_degradation']})",
              f"- Expected-value mismatch findings raised on degraded images: {q['degraded_payment_mismatch_findings']}",
              f"- Legible images with a (false) severe warning: {q['legible_with_severe_warning']}/{q['legible_n']}",
              f"- Unreadable images: {u}", f"- `low_concern` decisions anywhere: {metrics['low_concern_anywhere']}",
              "", "Undefined ratios (empty denominator) are reported as `null`."]
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", choices=["dev", "test", "test2"])
    ap.add_argument("--image-dir", type=Path, default=None, help="where to write rendered images (default: temp)")
    ap.add_argument("--print-config-hash", action="store_true")
    ap.add_argument("--force", action="store_true", help="dev only: overwrite an existing result")
    a = ap.parse_args()
    if a.print_config_hash:
        print(config_hash(a.split or "test"))
        return
    if not a.split:
        ap.error("--split is required")
    if a.split in ("test", "test2"):
        protocol = REPORT_DIR / ("protocol.json" if a.split == "test" else "protocol_v2.json")
        if not protocol.exists():
            raise SystemExit(f"Write {protocol.relative_to(ROOT)} before running the held-out split.")
        if json.loads(protocol.read_text(encoding="utf-8"))["config_hash"] != config_hash(a.split):
            raise SystemExit("Config changed after the protocol was registered; the held-out split is no longer valid.")
        if (REPORT_DIR / a.split / "metrics.json").exists():
            raise SystemExit("Held-out result already exists; it is run once and not overwritten.")
    elif (REPORT_DIR / "dev" / "metrics.json").exists() and not a.force:
        raise SystemExit("dev result exists; pass --force to overwrite.")
    image_dir = a.image_dir or Path(tempfile.mkdtemp(prefix=f"payeval_{a.split}_"))
    image_dir.mkdir(parents=True, exist_ok=True)
    samples = build_samples(a.split)
    print(f"{len(samples)} samples → {image_dir}")
    results = run_pipeline(samples, image_dir)
    metrics = compute_metrics(results)
    out = write_report(a.split, results, metrics)
    print(json.dumps(metrics["review_needed_legible"], indent=1))
    print("report:", out)


if __name__ == "__main__":
    main()

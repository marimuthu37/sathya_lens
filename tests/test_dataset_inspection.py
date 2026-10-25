"""
test_dataset_inspection.py — Inspection-only tooling for the team-supplied
datasets in dataset/ (scripts/inspect_message_sources.py and
scripts/inspect_phish360.py). Synthetic fixtures for behaviour; the real
files are used only when present, read-only.
"""
import csv
import hashlib
import importlib.util
import io
import json
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
REAL_IMC25 = REPO / "dataset" / "message_detection" / "final_dataset_output.csv"
REAL_MENDELEY = REPO / "dataset" / "message_detection" / "Financial scams detection dataset.csv"
REAL_PHISH360 = REPO / "dataset" / "image_detection" / "phish360.zip"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def msg():
    return _load("inspect_message_sources")


@pytest.fixture(scope="module")
def img():
    return _load("inspect_phish360")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()

# ---------------------------------------------------------------------------
# Message sources
# ---------------------------------------------------------------------------

def _mendeley(path: Path, rows, header=("label", "message")):
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    return path


class TestMessageValidation:
    def test_hash_mismatch_and_missing_file(self, msg, tmp_path):
        p = _mendeley(tmp_path / "m.csv", [("ham", "hello")])
        with pytest.raises(msg.InspectionError, match="does not match"):
            msg.verify(p, "0" * 64)
        with pytest.raises(msg.InspectionError, match="Missing"):
            msg.verify(tmp_path / "nope.csv", "0" * 64)

    def test_unexpected_columns_rejected(self, msg, tmp_path):
        p = _mendeley(tmp_path / "m.csv", [("ham", "hi", "x")], header=("label", "message", "extra"))
        with pytest.raises(msg.InspectionError, match="columns"):
            msg.read_mendeley(p, [])
        p = _mendeley(tmp_path / "n.csv", [("hi",)], header=("text",))
        with pytest.raises(msg.InspectionError, match="columns"):
            msg.read_mendeley(p, [])

    def test_invalid_label_and_empty_message_logged_without_text(self, msg, tmp_path):
        ex = []
        rows = msg.read_mendeley(_mendeley(tmp_path / "m.csv", [
            ("ham", "your tk 500 cash in received"), ("spam", "secret text 9876543210"), ("scam", "   ")]), ex)
        assert len(rows) == 1
        assert sorted(e["reason"] for e in ex) == ["empty message", "invalid label"]
        assert "9876543210" not in json.dumps(ex) and "secret" not in json.dumps(ex)

    def test_heuristics(self, msg):
        assert msg.script_of("আপনার টাকা") == "bengali_script"
        assert msg.script_of("hello") == "latin_only"
        assert msg.ham_kind("Tk 500 cash in received. TrxID ABC") == "financial_notification_like"
        assert msg.ham_kind("see you tomorrow") == "non_financial"
        assert msg.region_hint("send via bkash") == "bangladesh_cues"
        assert msg.region_hint("paid via UPI") == "india_cues"

    def test_duplicates_and_privacy_stats(self, msg):
        from satyalens_analysis.anonymise import anonymise
        raw = ["Pay 5000 now!", "pay 5000 NOW!", "Pay 7000 now!", "call 9876543210 today"]
        d = msg.duplicate_stats([anonymise(t) for t in raw])
        assert d["normalised_duplicate_rows"] >= 2
        p = msg.privacy_stats(raw, [anonymise(t) for t in raw])
        assert p["rows_with_4plus_digit_runs_before"] == 4 and p["rows_with_4plus_digit_runs_after"] == 0

    def test_overlap_detection(self, msg):
        out = msg.overlaps(["your account is blocked click urltoken now"],
                           {"ref": ["Your account is blocked, click https://x.example now"]})
        assert out["ref"]["total"] == 1


@pytest.mark.skipif(not (REAL_IMC25.is_file() and REAL_MENDELEY.is_file()), reason="dataset/ files not present")
class TestMessageInspectionOnRealFiles:
    def test_counts_only_report_and_originals_preserved(self, msg, tmp_path):
        before = (_sha(REAL_IMC25), _sha(REAL_MENDELEY))
        refs = {"challenge_suite_v1": [r["text"] for r in csv.DictReader(
            (REPO / "data" / "challenge_suite_v1.csv").open(encoding="utf-8"))]}
        rep = msg.inspect(out_dir=tmp_path, references=refs)
        assert (_sha(REAL_IMC25), _sha(REAL_MENDELEY)) == before
        assert rep["IMC25"]["matches_protocol_v3_pinned_file"] is True
        assert rep["IMC25"]["privacy"]["rows_with_4plus_digit_runs_after"] == 0
        assert rep["MENDELEY"]["in_protocol_v3"] is False
        assert rep["MENDELEY"]["label_counts"] == {"scam": 310, "ham": 213}
        assert "not_created" in rep and "test set" in rep["not_created"]
        blob = (tmp_path / "message_sources.json").read_text(encoding="utf-8") + \
            (tmp_path / "message_exclusions.csv").read_text(encoding="utf-8")
        samples = [r["message"] for r in csv.DictReader(REAL_MENDELEY.open(encoding="utf-8-sig"))][:40]
        samples += [r["text"] for _, r in zip(range(40), csv.DictReader(REAL_IMC25.open(encoding="utf-8", errors="replace")))]
        assert not any(len(s) > 25 and s[:25] in blob for s in samples)

    def test_no_training_pool_or_test_created(self):
        assert not (REPO / "data" / "training_pool_v3.csv").exists()
        assert not (REPO / "data" / "frozen_test_v3.csv").exists()
        assert not (REPO / "reports" / "frozen_test_v3_manifest.json").exists()

# ---------------------------------------------------------------------------
# Phish360 (synthetic zips)
# ---------------------------------------------------------------------------

def _png(color=(10, 20, 30), size=(320, 240)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


SECRET_URL = "https://login-secure.example-bank.co.uk/verify?token=SECRET123&user=alice@mail.example"


def _sample(z, split, folder, label="phish", url="https://a.example/x", html=b"<html>ok</html>",
            png=None, url_name="URL/url.txt", skip=()):
    base = f"Phish360/{split}/{folder}/"
    if "label" not in skip:
        z.writestr(base + "Label/label.txt", label)
    if "html" not in skip:
        z.writestr(base + "RAW-HTML/index.html", html)
    if "png" not in skip:
        z.writestr(base + "SCREEN-SHOT/screen_shoot.png", png if png is not None else _png())
    if "url" not in skip:
        z.writestr(base + url_name, url)


@pytest.fixture()
def toy_zip(tmp_path):
    p = tmp_path / "phish360.zip"
    dup = _png((200, 0, 0))
    with zipfile.ZipFile(p, "w") as z:
        _sample(z, "trainval", "P10001_paypal", url=SECRET_URL, png=dup)
        _sample(z, "test", "L00001_legitimate", label="legitimate", url="https://shop.example/login", png=dup)
        _sample(z, "trainval", "P10002_chase", png=b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + b"\x00" * 40)
        _sample(z, "trainval", "L00002_legitimate", label="phish", png=_png((0, 0, 200)))     # label mismatch
        _sample(z, "test", "P00003_phish", png=_png((0, 200, 0)), skip=("html",))               # missing html
        _sample(z, "trainval", "L00004_legitimate", label="legitimate", png=_png((9, 9, 9)),
                url="https://shop.example/home", url_name="URL/URL.txt")                        # case variant
        z.writestr("Phish360/trainval/weird_folder/Label/label.txt", "phish")                   # unrecognised
    return p


class TestPhish360Helpers:
    def test_registrable_domain(self, img):
        assert img.registrable_domain("https://a.b.example.com/x?y=1") == "example.com"
        assert img.registrable_domain("login.bank.co.uk/path") == "bank.co.uk"
        assert img.registrable_domain("http://10.0.0.1/x") == "10.0.0.1"
        assert img.registrable_domain("") is None

    def test_png_header(self, img):
        assert img.png_header(_png(size=(321, 123))) == (321, 123)
        assert img.png_header(b"not a png") is None

    def test_decode_failure_detected(self, img):
        ok, dh, mode = img.decode_png(b"\x89PNG\r\n\x1a\n" + b"\x00" * 60)
        assert ok is False and dh is None


class TestPhish360Inspection:
    def test_content_inventory_on_plain_zip(self, img, toy_zip, tmp_path):
        before = _sha(toy_zip)
        rep = img.inspect(toy_zip, tmp_path / "out", decode=True, expected_sha256=None)
        assert _sha(toy_zip) == before                                   # original untouched
        assert rep["access"]["content_readable"] is True
        assert rep["samples"] == 6
        assert rep["label_mismatches"] == 1
        assert rep["png"]["decode_failed"] == 1
        assert rep["file_completeness"].get("missing_html") == 1
        assert "missing_url" not in rep["file_completeness"]            # URL/URL.txt accepted
        d = rep["duplicates"]["screenshot_exact_sha256"]
        assert d["duplicate_groups"] == 1 and d["groups_spanning_both_classes"] == 1 \
            and d["groups_spanning_both_splits"] == 1
        assert rep["domains"]["domains_in_both_splits"] >= 1             # shop.example in both splits

    def test_reports_never_contain_urls_domains_or_tokens(self, img, toy_zip, tmp_path):
        img.inspect(toy_zip, tmp_path / "out", decode=True, expected_sha256=None)
        blob = (tmp_path / "out" / "phish360.json").read_text(encoding="utf-8") + \
            (tmp_path / "out" / "phish360_issues.csv").read_text(encoding="utf-8")
        for s in ("SECRET123", "alice", "example-bank", "shop.example", "a.example"):
            assert s not in blob
        import re
        allowed = {img.PROVENANCE["project_page"]}
        found = set(re.findall(r"https?://[^\s\"',)]+", blob))
        assert found <= allowed, found - allowed       # only the public provenance URL

    def test_index_inventory(self, img, toy_zip):
        idx = img.index_inventory(toy_zip)
        assert idx["samples"] == 6 and idx["unrecognised_folders"] == 1
        assert idx["by_split_and_class"]["trainval|phishing"] == 2
        assert idx["phishing_brand_tags"]["generic_phish_tag"] == 1
        assert idx["duplicate_candidates_by_crc32_and_size"]["png"]["groups_spanning_both_classes"] == 1

    def test_hash_mismatch_refused(self, img, toy_zip, tmp_path):
        with pytest.raises(RuntimeError, match="does not match"):
            img.inspect(toy_zip, tmp_path / "o", expected_sha256="0" * 64)

    def test_encrypted_archive_not_bypassed(self, img, toy_zip, tmp_path, monkeypatch):
        real = zipfile.ZipFile

        class Encrypted(real):
            def infolist(self):
                infos = super().infolist()
                for i in infos:
                    i.flag_bits |= 0x1
                    i.compress_type = 99
                return infos
        monkeypatch.setattr(img.zipfile, "ZipFile", Encrypted)
        monkeypatch.delenv(img.PASSWORD_ENV, raising=False)
        z, status = img.open_archive(toy_zip)
        assert z is None and "password not provided" in status["reason"]
        monkeypatch.setenv(img.PASSWORD_ENV, "pw")
        monkeypatch.setitem(__import__("sys").modules, "pyzipper", None)
        z, status = img.open_archive(toy_zip)
        assert z is None and "pyzipper" in status["reason"]
        monkeypatch.delenv(img.PASSWORD_ENV)
        rep = img.inspect(toy_zip, tmp_path / "enc", expected_sha256=None)
        assert rep["content_inventory"].startswith("NOT PERFORMED")
        assert "pw" not in (tmp_path / "enc" / "phish360.json").read_text(encoding="utf-8")


@pytest.mark.skipif(not REAL_PHISH360.is_file(), reason="phish360.zip not present")
class TestPhish360RealIndex:
    def test_real_index_counts(self, img):
        idx = img.index_inventory(REAL_PHISH360)
        assert idx["samples"] == 10748 and idx["unrecognised_folders"] == 0
        assert sum(idx["by_split_and_class"].values()) == 10748
        assert "missing_url" not in idx["file_completeness"]

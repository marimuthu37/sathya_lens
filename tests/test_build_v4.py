"""
test_build_v4.py — Protocol v4 builder and grouping policy G4 (synthetic
inputs; the real dataset is read only when present).
"""
import importlib.util
import random
from pathlib import Path

import pytest

from satyalens_analysis import text_classifier as tc

REPO = Path(__file__).resolve().parent.parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def b4():
    return _load("build_pool_and_test_v4")


def _row(source, key, text, label="suspicious", base=None, **kw):
    return {"source": source, "key": key, "raw": text, "text": text, "label": label, "base": base,
            "category": kw.get("category", "c"), "india_linked": False}


class TestJaccardPairs:
    def test_matches_brute_force(self):
        rng = random.Random(11)
        vocab = [f"w{i}" for i in range(35)]
        sets = [set(rng.sample(vocab, rng.randint(1, 8))) for _ in range(300)]
        for t in (0.6, 0.8):
            brute = {(i, j) for i in range(len(sets)) for j in range(i + 1, len(sets))
                     if len(sets[i] & sets[j]) / len(sets[i] | sets[j]) >= t}
            assert set(tc.jaccard_pairs(sets, t)) == brute


class TestG4:
    # Token-set Jaccard: A-D 9/11 = 0.82 (near copy, same unit); A-B 8/12 = 0.67 and
    # B-C 8/12 = 0.67 (template variants); A-C 6/14 = 0.43. At 0.8, A, B and C must stay
    # separate units (no 0.6 chaining); the 0.6 purge must catch B when A is in test.
    _a = "alpha beta gamma delta epsilon zeta eta theta iota kappa".split()
    A = " ".join(_a)
    D = " ".join(_a[:9] + ["lambda"])
    B = " ".join(_a[:8] + ["mu", "nu"])
    C = " ".join(_a[:6] + ["mu", "nu", "xi", "omicron"])

    def test_fixture_similarities(self):
        j = lambda x, y: len(set(x.split()) & set(y.split())) / len(set(x.split()) | set(y.split()))  # noqa: E731
        assert j(self.A, self.D) >= 0.8 and 0.6 <= j(self.A, self.B) < 0.8
        assert 0.6 <= j(self.B, self.C) < 0.8 and j(self.A, self.C) < 0.6

    def test_atomic_units_link_at_0_8_only(self, b4):
        rows = [_row("IMC25", "row2", self.A), _row("IMC25", "row3", self.D),
                _row("IMC25", "row4", self.B), _row("IMC25", "row5", self.C),
                _row("R115", "r1", "one message", base="k"), _row("R115", "r2", "another one", base="k")]
        stats = b4.atomic_units(rows)
        assert rows[0]["group"] == rows[1]["group"] == "IMC25:row2"
        assert len({rows[0]["group"], rows[2]["group"], rows[3]["group"]}) == 3
        assert rows[4]["group"] == rows[5]["group"]
        assert stats["IMC25"]["units"] == 3

    def test_giant_units_forced_to_train_and_assignment_deterministic(self, b4):
        rows = [_row("IMC25", f"g{i:03d}", "x") for i in range(60)]          # one giant unit (> 50)
        for r in rows:
            r["group"] = "IMC25:giant"
        rows += [_row("IMC25", f"s{i:04d}", "y") for i in range(1000)]
        for r in rows[60:]:
            r["group"] = f"IMC25:{r['key']}"
        rows += [_row("MWOZ_TEST", "m", "z", "legitimate"), _row("MENDELEY", "line2", "z", "legitimate"),
                 _row("SYN", "s", "z", "legitimate")]
        for r in rows[-3:]:
            r["group"] = f"{r['source']}:{r['key']}"
        stats = b4.assign(rows, "salt-a")
        first = [r["split"] for r in rows]
        b4.assign(rows, "salt-a")
        assert first == [r["split"] for r in rows]
        assert all(r["split"] == "train" for r in rows[:60])
        assert stats["IMC25"]["forced_train_units"] == 1 and stats["IMC25"]["giant_cap_rows"] == 50
        assert rows[-3]["split"] == "test" and rows[-2]["split"] == rows[-1]["split"] == "train"
        share = sum(r["split"] == "test" for r in rows[60:1060]) / 1000
        assert 0.16 < share < 0.24

    def test_purge_removes_train_near_test_and_never_touches_test(self, b4):
        rows = [_row("IMC25", "t", self.A), _row("IMC25", "near", self.B, ), _row("MENDELEY", "far",
                "completely unrelated words here today", "legitimate"), _row("SYN", "copy", self.A.upper())]
        for r, s in zip(rows, ["test", "train", "train", "train"]):
            r["split"] = s
        ex = []
        stats = b4.purge(rows, ex)
        assert [r["split"] for r in rows] == ["test", "purged", "train", "purged"]
        assert stats["purged_rows_total"] == 2 and stats["final_cross_split_pairs_jaccard_ge_0_6"] == 0
        assert all(e["outcome"] == "purged_near_test" for e in ex)

    def test_dedupe_priority_includes_mendeley(self, b4):
        ex = []
        rows = [_row("SYN", "s", "same words in both", "legitimate"),
                _row("MENDELEY", "line2", "Same words in BOTH", "legitimate"),
                _row("IMC25", "row9", "same words in both", "legitimate")]
        kept = b4.dedupe(rows, ex)
        assert [r["source"] for r in kept] == ["IMC25"]
        assert len(ex) == 2


class TestV4Integrity:
    def test_protocol_tamper_detected(self, b4, tmp_path, monkeypatch):
        fake = tmp_path / "p.json"
        fake.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(b4, "PROTOCOL", fake)
        with pytest.raises(b4.StopBuild, match="does not match"):
            b4.load_protocol()

    def test_protocol_loads(self, b4):
        p = b4.load_protocol()
        assert p["protocol_version"] == "4.0" and p["grouping"]["policy_id"] == "G4"

    def test_builder_does_not_name_the_sealed_file(self):
        assert "frozen_test_v4.csv" not in (REPO / "scripts" / "build_pool_and_test_v4.py").read_text(encoding="utf-8")

    def test_v3_builder_untouched_by_v4(self):
        src = (REPO / "scripts" / "build_pool_and_test_v3.py").read_text(encoding="utf-8")
        assert "protocol_v4" not in src and "anonymise_v2" not in src

    @pytest.mark.skipif(not (REPO / "dataset" / "message_detection" / "Financial scams detection dataset.csv").is_file(),
                        reason="Mendeley file not present")
    def test_mendeley_loader_latin_only(self, b4):
        ex = []
        rows = b4.load_mendeley(b4.load_protocol(), ex)
        assert len(rows) == 422
        assert sum(1 for e in ex if e["outcome"] == "excluded_script") == 101
        assert {r["label"] for r in rows} == {"suspicious", "legitimate"}
        assert all(r["source"] == "MENDELEY" for r in rows)


class TestSealGuardRecognisesV4:
    def test_v4_manifest_path_in_defaults(self):
        assert any(p.name == "frozen_test_v4_manifest.json" for p in tc.DEFAULT_SEALED_MANIFESTS)
        assert any(p.name == "frozen_test_v3_manifest.json" for p in tc.DEFAULT_SEALED_MANIFESTS)

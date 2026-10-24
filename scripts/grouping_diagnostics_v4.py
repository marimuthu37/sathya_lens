"""
grouping_diagnostics_v4.py — Counts-only diagnostics for choosing the
protocol v4 grouping policy (no model is trained or used).
==========================================================================

Data: IMC25 English rows with the v4 label mapping (incl. 'hey mum/dad'),
anon-v2, exact normalised de-duplication. Policies compared:
  P0  v3 rule: single-link Jaccard >= 0.6, rarest-3-token blocking
  P1  single-link Jaccard >= 0.6, exact join
  P2  single-link Jaccard >= 0.8, exact join (= the leakage definition)
  P3  greedy leader ("star") clustering at 0.6 in stable key order
For each: group sizes, rows in the largest group, cross-group pairs at
Jaccard >= 0.8 (would be leakage if split) and in [0.6, 0.8), and the
largest group's share of an expected 20 % test. The giant component is
characterised by scam_type, named-entity diversity and sampled internal
similarity (evidence of transitive chaining).

Test-membership simulation uses a DIAGNOSTIC salt, never the protocol salt,
so the policy is not chosen against the real test draw.

    python scripts/grouping_diagnostics_v4.py   -> reports/grouping_v4/diagnostics.json
"""

from __future__ import annotations

import csv
import hashlib
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from satyalens_analysis import text_classifier as tc  # noqa: E402
from satyalens_analysis.anonymise import anonymise_v2  # noqa: E402

IMC25 = REPO / "dataset" / "message_detection" / "final_dataset_output.csv"
OUT = REPO / "reports" / "grouping_v4"
MAPPING = {"banking", "delivery", "government", "telecom", "others", "hey mum/dad"}
DIAGNOSTIC_SALT = "grouping-diagnostic-only-not-a-protocol-salt"


class UnionFind:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def load_rows() -> List[Dict]:
    csv.field_size_limit(10 ** 8)
    rows, seen = [], set()
    with IMC25.open(encoding="utf-8", errors="replace", newline="") as fh:
        for line_no, r in enumerate(csv.DictReader(fh), start=2):
            text = (r.get("text") or "").strip()
            if not text or (r.get("scam_type") or "").strip() not in MAPPING:
                continue
            if (r.get("language") or "").strip() != "English":
                continue
            anon = anonymise_v2(text)
            key = tc.normalise_text(anon)
            if not anon or key in seen:
                continue
            seen.add(key)
            rows.append({"key": f"row{line_no}", "text": anon, "scam_type": r["scam_type"].strip(),
                         "entity": (r.get("named_entity") or "").strip() or "<none>"})
    return rows


def token_sets(rows: Sequence[Dict]) -> List[set]:
    return [set(tc.skeleton_text(r["text"]).split()) for r in rows]


def single_link(n: int, pairs: Sequence[Tuple[int, int]]) -> List[int]:
    uf = UnionFind(n)
    for a, b in pairs:
        uf.union(a, b)
    return [uf.find(i) for i in range(n)]


def leader_clusters(n: int, pairs: Sequence[Tuple[int, int]]) -> List[int]:
    """Each row (in index order) joins the lowest-index earlier LEADER it is
    linked to (pairs at the threshold), else becomes a leader. No chaining."""
    earlier: Dict[int, List[int]] = defaultdict(list)
    for a, b in pairs:
        earlier[max(a, b)].append(min(a, b))
    labels = [-1] * n
    is_leader = [False] * n
    for i in range(n):
        leaders = sorted(j for j in earlier.get(i, ()) if is_leader[j])
        if leaders:
            labels[i] = leaders[0]
        else:
            labels[i] = i
            is_leader[i] = True
    return labels


def v3_rule(rows: List[Dict]) -> List[int]:
    import build_pool_and_test_v3 as builder
    tmp = [{"source": "IMC25", "key": r["key"], "text": r["text"], "base": None} for r in rows]
    builder.assign_groups(tmp)
    ids = {g: i for i, g in enumerate(dict.fromkeys(t["group"] for t in tmp))}
    return [ids[t["group"]] for t in tmp]


def summarise(labels: List[int], pairs08, pairs06, n: int) -> Dict:
    sizes = Counter(labels)
    largest = sizes.most_common(10)
    cross08 = sum(1 for a, b in pairs08 if labels[a] != labels[b])
    cross06 = sum(1 for a, b in pairs06 if labels[a] != labels[b]) - cross08
    expected_test = 0.2 * n
    sim = Counter()
    for g, size in sizes.items():
        u = int(hashlib.sha256(f"{DIAGNOSTIC_SALT}|{g}".encode()).hexdigest()[:8], 16) / 2 ** 32
        sim["test" if u < 0.2 else "train"] += size
    return {
        "groups": len(sizes), "singletons": sum(1 for v in sizes.values() if v == 1),
        "groups_ge_100": sum(1 for v in sizes.values() if v >= 100),
        "largest_10_sizes": [s for _, s in largest],
        "largest_share_of_rows": round(largest[0][1] / n, 4),
        "largest_share_of_expected_20pct_test_if_drawn": round(largest[0][1] / (expected_test + 0.8 * largest[0][1]), 4),
        "cross_group_pairs_jaccard_ge_0_8": cross08,
        "cross_group_pairs_jaccard_0_6_to_0_8": cross06,
        "diagnostic_salt_test_share": round(sim["test"] / n, 4),
    }


def proposed_policy(n: int, pairs06, pairs08, salt: str = DIAGNOSTIC_SALT) -> Dict:
    """P4: atomic units = single-link >= 0.8; giant units (> max(50, 1 % of rows))
    forced to TRAIN; others hash-assigned at 20 %; TRAIN rows with Jaccard >= 0.6
    to any TEST row purged (test never altered)."""
    labels = single_link(n, pairs08)
    sizes = Counter(labels)
    cap = max(50, int(0.01 * n))
    split = {}
    for g, size in sizes.items():
        if size > cap:
            split[g] = "train_forced"
        else:
            u = int(hashlib.sha256(f"{salt}|{g}".encode()).hexdigest()[:8], 16) / 2 ** 32
            split[g] = "test" if u < 0.2 else "train"
    row_split = [split[labels[i]] for i in range(n)]
    purge = set()
    for a, b in pairs06:
        sa, sb = row_split[a], row_split[b]
        if sa == "test" and sb != "test":
            purge.add(b)
        elif sb == "test" and sa != "test":
            purge.add(a)
    test_rows = [i for i in range(n) if row_split[i] == "test"]
    test_sizes = Counter(labels[i] for i in test_rows)
    remaining_cross = sum(1 for a, b in pairs06 if (row_split[a] == "test") != (row_split[b] == "test")
                          and a not in purge and b not in purge)
    return {
        "giant_cap_rows": cap,
        "forced_train_units": sum(1 for v in split.values() if v == "train_forced"),
        "forced_train_rows": sum(1 for i in range(n) if row_split[i] == "train_forced"),
        "test_rows": len(test_rows), "test_share_of_rows": round(len(test_rows) / n, 4),
        "test_units": len(test_sizes),
        "largest_test_unit": max(test_sizes.values()) if test_sizes else 0,
        "largest_test_unit_share_of_test": round(max(test_sizes.values()) / len(test_rows), 4) if test_rows else None,
        "train_rows_purged_for_0_6_similarity": len(purge),
        "train_rows_after_purge": sum(1 for i in range(n) if row_split[i] != "test") - len(purge),
        "cross_split_pairs_jaccard_ge_0_6_after_purge": remaining_cross,
    }


def characterise(rows, sets, labels, label) -> Dict:
    members = [i for i, l in enumerate(labels) if l == label]
    rng = random.Random(7)
    sample = [tuple(rng.sample(members, 2)) for _ in range(min(3000, len(members) * 2))] if len(members) > 1 else []
    js = sorted(len(sets[a] & sets[b]) / max(1, len(sets[a] | sets[b])) for a, b in sample)
    ents = Counter(rows[i]["entity"] for i in members)
    return {
        "size": len(members),
        "scam_types": dict(Counter(rows[i]["scam_type"] for i in members)),
        "distinct_named_entities": len(ents), "top_named_entities": ents.most_common(8),
        "sampled_internal_jaccard": {"pairs": len(js), "median": round(js[len(js) // 2], 3) if js else None,
                                     "share_below_0_2": round(sum(j < 0.2 for j in js) / len(js), 3) if js else None,
                                     "share_ge_0_6": round(sum(j >= 0.6 for j in js) / len(js), 3) if js else None},
        "mean_tokens": round(sum(len(sets[i]) for i in members) / len(members), 1),
    }


def main() -> int:
    rows = load_rows()
    sets = token_sets(rows)
    n = len(rows)
    pairs06 = tc.jaccard_pairs(sets, 0.6)
    pairs08 = [(a, b) for a, b in pairs06
               if len(sets[a] & sets[b]) / len(sets[a] | sets[b]) >= 0.8]
    policies = {"P0_v3_rarest3_0.6": v3_rule(rows), "P1_single_link_0.6": single_link(n, pairs06),
                "P2_single_link_0.8": single_link(n, pairs08), "P3_leader_0.6": leader_clusters(n, pairs06)}
    report = {"rows_after_v4_mapping_anon_v2_dedup": n, "pairs_jaccard_ge_0_6": len(pairs06),
              "pairs_jaccard_ge_0_8": len(pairs08), "policies": {}, "giant_components": {}}
    for name, labels in policies.items():
        report["policies"][name] = summarise(labels, pairs08, pairs06, n)
        top = Counter(labels).most_common(1)[0][0]
        report["giant_components"][name] = characterise(rows, sets, labels, top)
    p1, p2 = policies["P1_single_link_0.6"], policies["P2_single_link_0.8"]
    giant = Counter(p1).most_common(1)[0][0]
    inner = Counter(p2[i] for i, l in enumerate(p1) if l == giant)
    report["giant_P1_split_by_0_8_components"] = {"components": len(inner), "largest_10": [s for _, s in inner.most_common(10)]}
    report["P4_proposed_policy_diagnostic_salt"] = proposed_policy(n, pairs06, pairs08)
    report["P4_salt_sensitivity"] = {f"salt_{k}": {x: v for x, v in proposed_policy(n, pairs06, pairs08, f"{DIAGNOSTIC_SALT}-{k}").items()
                                                   if x in ("test_share_of_rows", "largest_test_unit_share_of_test",
                                                            "train_rows_purged_for_0_6_similarity")}
                                     for k in range(5)}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "diagnostics.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

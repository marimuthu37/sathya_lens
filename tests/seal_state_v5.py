"""
seal_state_v5.py — shared check of the real repository's protocol v5 seal state
(imported by tests; not collected itself).

Exactly two states are valid:
  * CLEAN pre-seal: no v5 manifest, pool, frozen test, evaluation log or seal build report;
  * SEALED: a well-formed v5 manifest (pinned protocol hash and paths, one sealed
    file) whose recorded SHA-256 values match the frozen test and the pool on disk.
Anything else — files without a manifest, a malformed manifest, missing files,
mismatched hashes — is rejected with SealStateError. The sealed test's content is
only hashed, never parsed.
"""
import hashlib
import json
from pathlib import Path

PIN_V5 = "3889099d1da7fa6cdc987685a2dacda4592508339bd8cedb91a99fa29f25f3ee"
MANIFEST = "reports/frozen_test_v5_manifest.json"
TEST = "data/frozen_test_v5.csv"
POOL = "data/training_pool_v5.csv"
LOG = "reports/frozen_test_v5_evaluations.jsonl"
SEAL_REPORT = "reports/build_v5/build_report.json"


class SealStateError(AssertionError):
    pass


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def check_seal_state(root: Path) -> str:
    """Return 'clean' or 'sealed' for *root*; raise SealStateError otherwise."""
    root = Path(root)
    manifest = root / MANIFEST
    if not manifest.exists():
        present = [f for f in (TEST, POOL, LOG, SEAL_REPORT) if (root / f).exists()]
        if present:
            raise SealStateError(f"v5 outputs exist without a sealed manifest (unsealed state): {present}")
        return "clean"
    try:
        m = json.loads(manifest.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SealStateError(f"v5 manifest is malformed: {exc}") from exc
    if not isinstance(m, dict):
        raise SealStateError("v5 manifest is not a JSON object.")
    expected = {"protocol_sha256": PIN_V5, "protocol_file": "docs/protocol_v5.json", "evaluation_log": LOG}
    for k, v in expected.items():
        if m.get(k) != v:
            raise SealStateError(f"v5 manifest {k} is {m.get(k)!r}, expected {v!r}.")
    sealed = m.get("sealed_files")
    if not (isinstance(sealed, list) and len(sealed) == 1 and isinstance(sealed[0], dict)
            and sealed[0].get("path") == TEST and isinstance(sealed[0].get("sha256"), str)):
        raise SealStateError(f"v5 manifest must list exactly one sealed file at {TEST} with a sha256.")
    if not isinstance(m.get("pool_csv_sha256"), str) or not isinstance(m.get("pool_counts"), dict):
        raise SealStateError("v5 manifest lacks pool_csv_sha256 / pool_counts.")
    for rel, want in ((TEST, sealed[0]["sha256"]), (POOL, m["pool_csv_sha256"])):
        f = root / rel
        if not f.is_file():
            raise SealStateError(f"{rel} is missing although the v5 manifest exists.")
        if _sha(f) != want:
            raise SealStateError(f"{rel} SHA-256 does not match the v5 manifest.")
    return "sealed"

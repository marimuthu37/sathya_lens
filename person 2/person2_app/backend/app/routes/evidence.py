"""
app/routes/evidence.py
──────────────────────
Segment 4: consent, evidence upload, evidence deletion.

Endpoints:
    POST   /api/cases/{case_id}/consent              — record explicit consent
    POST   /api/cases/{case_id}/evidence             — upload evidence (requires consent)
    DELETE /api/cases/{case_id}/evidence/{evidence_id} — delete a single evidence item
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Case, ConsentStatus, Evidence
from app.schemas import ConsentRequest, ConsentResponse, EvidenceRead
from app.services.case_status import refresh_analysis_status

logger = logging.getLogger(__name__)

router = APIRouter()


# ── File-safety constants ─────────────────────────────────────────────────────

# Allowed MIME types (client-supplied). Checked alongside magic bytes.
ALLOWED_MIME_TYPES: frozenset[str] = frozenset(
    settings.allowed_mime_types_list
)

# Magic byte signatures: maps MIME type → expected header bytes.
# Only types in ALLOWED_MIME_TYPES need entries; anything not here is
# accepted on MIME type alone (still won't execute).
MAGIC_SIGNATURES: dict[str, bytes] = {
    "image/jpeg":    b"\xff\xd8\xff",
    "image/png":     b"\x89PNG\r\n\x1a\n",
    "image/gif":     b"GIF",
    "application/pdf": b"%PDF",
}

# Compile once — strip any character that is not alphanumeric, hyphen, underscore, or dot.
_SAFE_NAME_RE = re.compile(r"[^\w.\-]")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_case_or_404(case_id: str, db: Session) -> Case:
    case = db.get(Case, case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "CASE_NOT_FOUND", "message": f"Case '{case_id}' does not exist."},
        )
    return case


def _sanitise_filename(original: str) -> str:
    """
    Return a safe display name for the original upload filename.
    - Strips directory components.
    - Replaces unsafe characters with '_'.
    - Limits length to 255 chars.
    """
    name = Path(original).name          # strip any path prefix
    name = _SAFE_NAME_RE.sub("_", name) # replace unsafe chars
    return name[:255] or "upload"


def _storage_path() -> Path:
    """Return the validated, absolute upload directory path."""
    upload_dir = Path(settings.upload_dir).resolve()
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir


def _server_filename(evidence_id: str, original: str) -> str:
    """
    Generate a server-side storage name that cannot be path-traversed.
    Format: <uuid>.<original_extension_lowercased>
    """
    suffix = Path(original).suffix.lower()
    # Only allow safe extension chars
    suffix = re.sub(r"[^\w.]", "", suffix)[:16]
    return f"{evidence_id}{suffix}"


def _validate_magic(data: bytes, mime_type: str) -> bool:
    """
    Return True if the file's leading bytes match the expected magic for the
    given MIME type.  Types without a known signature are skipped (pass-through).
    """
    expected = MAGIC_SIGNATURES.get(mime_type)
    if expected is None:
        return True
    return data[: len(expected)] == expected


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/consent",
    response_model=ConsentResponse,
    status_code=status.HTTP_200_OK,
    summary="Record explicit user consent for a case",
    description=(
        "Records that the user has given informed, explicit consent to upload and "
        "process evidence for this case.  Consent must be active before evidence "
        "is accepted.  The timestamp is stored server-side."
    ),
    tags=["Evidence"],
)
def record_consent(
    case_id: str,
    body: ConsentRequest,
    db: Session = Depends(get_db),
) -> ConsentResponse:
    """
    FIX (Issue 2): body.acknowledged must be True (validated by schema).
    The backend records a server-generated timestamp and policy version.
    """
    from app.schemas import CONSENT_POLICY_VERSION
    case = _get_case_or_404(case_id, db)

    if case.consent_status == ConsentStatus.GRANTED:
        # Idempotent: already consented — just return current state.
        return ConsentResponse(
            case_id=case.id,
            consent_status=case.consent_status,
            consent_at=case.consent_at,
            consent_policy_version=CONSENT_POLICY_VERSION,
            message="Consent was already recorded for this case.",
        )

    from datetime import datetime, timezone
    case.consent_status = ConsentStatus.GRANTED
    case.consent_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(case)

    logger.info("Consent recorded for case id=%s policy_version=%s", case.id, CONSENT_POLICY_VERSION)
    return ConsentResponse(
        case_id=case.id,
        consent_status=case.consent_status,
        consent_at=case.consent_at,
        consent_policy_version=CONSENT_POLICY_VERSION,
        message="Consent recorded. You may now upload evidence.",
    )


@router.post(
    "",
    response_model=EvidenceRead,
    status_code=status.HTTP_201_CREATED,
    summary="Upload evidence to a case",
    description=(
        "Uploads a single evidence file (multipart/form-data).  "
        "The case must exist and the user must have given explicit consent. "
        "The SHA-256 hash of the original bytes is computed server-side and returned. "
        "Raw file bytes are never returned by any API endpoint."
    ),
    tags=["Evidence"],
)
async def upload_evidence(
    case_id: str,
    file: UploadFile = File(..., description="The evidence file to upload."),
    db: Session = Depends(get_db),
) -> EvidenceRead:
    # ── 1. Case existence ──────────────────────────────────────────────────────
    case = _get_case_or_404(case_id, db)

    # ── 2. Consent check ──────────────────────────────────────────────────────
    if case.consent_status != ConsentStatus.GRANTED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "CONSENT_REQUIRED",
                "message": (
                    "Explicit consent must be recorded before uploading evidence. "
                    "POST /api/cases/{case_id}/consent first."
                ),
            },
        )

    # ── 3. MIME type allow-list ───────────────────────────────────────────────
    client_mime = (file.content_type or "application/octet-stream").split(";")[0].strip().lower()
    if client_mime not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={
                "code": "UNSUPPORTED_MEDIA_TYPE",
                "message": (
                    f"File type '{client_mime}' is not accepted. "
                    f"Allowed types: {', '.join(sorted(ALLOWED_MIME_TYPES))}."
                ),
            },
        )

    # ── 4. Stream to temp file, enforce size limit, compute SHA-256 ─────────────
    # FIX (Issue 6): stream to a temp file rather than accumulating in memory.
    # The entire file was previously held in a list of chunks, then joined.
    # For large uploads this is wasteful. We now write chunks directly to a
    # NamedTemporaryFile so resident memory is bounded to the chunk size (64 KiB).
    import tempfile
    max_bytes = settings.max_upload_bytes
    sha256 = hashlib.sha256()
    total_read = 0
    tmp_file = None

    try:
        # delete=False so we can move/read the file after closing it
        tmp_file = tempfile.NamedTemporaryFile(
            dir=_storage_path(), delete=False, suffix=".tmp"
        )
        try:
            while True:
                chunk = await file.read(65_536)  # 64 KiB chunks
                if not chunk:
                    break
                total_read += len(chunk)
                if total_read > max_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail={
                            "code": "FILE_TOO_LARGE",
                            "message": (
                                f"File exceeds the maximum allowed size of "
                                f"{max_bytes // (1024 * 1024)} MiB."
                            ),
                        },
                    )
                sha256.update(chunk)
                tmp_file.write(chunk)
            tmp_file.flush()
        finally:
            tmp_file.close()
    except HTTPException:
        if tmp_file:
            Path(tmp_file.name).unlink(missing_ok=True)
        raise
    except Exception as exc:
        if tmp_file:
            Path(tmp_file.name).unlink(missing_ok=True)
        logger.exception("Error reading upload for case=%s", case_id)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "UPLOAD_READ_ERROR", "message": "Failed to read the uploaded file."},
        ) from exc

    file_size = total_read
    tmp_path_obj = Path(tmp_file.name)

    if file_size == 0:
        tmp_path_obj.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "EMPTY_FILE", "message": "Uploaded file is empty."},
        )

    # ── 5. Magic-byte validation (read header from temp file) ─────────────────
    try:
        with tmp_path_obj.open("rb") as fh:
            file_header = fh.read(16)
    except OSError as exc:
        tmp_path_obj.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "STORAGE_ERROR", "message": "Could not read uploaded file."},
        ) from exc

    if not _validate_magic(file_header, client_mime):
        tmp_path_obj.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={
                "code": "INVALID_FILE_SIGNATURE",
                "message": (
                    f"File header does not match the declared type '{client_mime}'. "
                    "Upload the original, unmodified file."
                ),
            },
        )

    sha256_hex = sha256.hexdigest()
    original_name = _sanitise_filename(file.filename or "upload")

    # ── 6. Move temp file to final server-side name ────────────────────────────
    evidence_id = str(uuid.uuid4())
    server_name = _server_filename(evidence_id, original_name)
    storage_dir = _storage_path()
    dest_path = storage_dir / server_name

    # Double-check: resolved path must remain inside upload_dir (path traversal guard)
    try:
        dest_path.resolve().relative_to(storage_dir)
    except ValueError:
        tmp_path_obj.unlink(missing_ok=True)
        logger.error("Path traversal attempt: server_name=%s", server_name)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_FILENAME", "message": "Invalid filename."},
        )

    try:
        tmp_path_obj.rename(dest_path)
    except OSError as exc:
        tmp_path_obj.unlink(missing_ok=True)
        logger.exception("Failed to move temp file to %s", server_name)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "STORAGE_ERROR", "message": "Could not save the uploaded file."},
        ) from exc

    # ── 7. Persist metadata to DB ─────────────────────────────────────────────
    evidence = Evidence(
        id=evidence_id,
        case_id=case_id,
        original_filename=original_name,
        media_type=client_mime,
        file_size_bytes=file_size,
        sha256_hash=sha256_hex,
    )
    db.add(evidence)
    try:
        refresh_analysis_status(db, case)  # a new, unanalysed item → not fully analysed
        db.commit()
        db.refresh(evidence)
    except Exception as exc:
        # Rollback DB; also remove the orphaned file
        db.rollback()
        try:
            dest_path.unlink(missing_ok=True)
        except OSError:
            pass
        logger.exception("DB error saving evidence for case=%s", case_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "DB_ERROR", "message": "Failed to save evidence metadata."},
        ) from exc

    logger.info(
        "Evidence uploaded: case=%s evidence=%s size=%d sha256=%s",
        case_id, evidence_id, file_size, sha256_hex,
    )
    return EvidenceRead.model_validate(evidence)


@router.delete(
    "/{evidence_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a single evidence item",
    description=(
        "Permanently removes evidence metadata from the database and the stored file "
        "from disk.  Both are deleted atomically as far as possible; if the file "
        "is already missing from disk the metadata is still removed."
    ),
    tags=["Evidence"],
)
def delete_evidence(
    case_id: str,
    evidence_id: str,
    db: Session = Depends(get_db),
) -> Response:
    # Verify case exists
    case = _get_case_or_404(case_id, db)

    evidence = db.query(Evidence).filter(
        Evidence.id == evidence_id,
        Evidence.case_id == case_id,
    ).first()

    if evidence is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "EVIDENCE_NOT_FOUND", "message": f"Evidence '{evidence_id}' not found on case '{case_id}'."},
        )

    # Build storage path (server_filename re-derives it safely)
    server_name = _server_filename(evidence_id, evidence.original_filename)
    storage_dir = _storage_path()
    dest_path = storage_dir / server_name
    # Temporary name used to stage the delete atomically
    tmp_path = storage_dir / f"{server_name}.deleting"

    # FIX (Issue 4): stage the file rename BEFORE touching the DB.
    # Recoverable states:
    #   - rename fails: abort, file and DB record untouched -> 500 returned.
    #   - rename succeeds + DB commit succeeds: remove .deleting file -> 204.
    #   - rename succeeds + DB commit fails: restore rename -> 500, no data lost.
    file_staged = False
    if dest_path.exists():
        try:
            dest_path.rename(tmp_path)
            file_staged = True
        except OSError as exc:
            logger.exception("Could not stage evidence file for deletion: %s", server_name)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "STORAGE_ERROR", "message": "Could not stage file for deletion. No data removed."},
            ) from exc

    # Delete DB record
    db.delete(evidence)
    try:
        refresh_analysis_status(db, case)
        db.commit()
    except Exception as exc:
        db.rollback()
        # Undo the file rename so the file is recoverable
        if file_staged:
            try:
                tmp_path.rename(dest_path)
            except OSError:
                logger.error(
                    "CRITICAL: Could not restore staged file %s after DB rollback "
                    "— manual recovery needed.", tmp_path,
                )
        logger.exception("DB error deleting evidence id=%s", evidence_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "DB_ERROR", "message": "Failed to delete evidence metadata. File has been restored."},
        ) from exc

    # DB committed — permanently remove the staged file
    if file_staged:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            logger.error(
                "DB deleted evidence id=%s but could not remove staged file %s "
                "— manual cleanup needed.", evidence_id, tmp_path,
            )

    logger.info("Deleted evidence id=%s from case=%s", evidence_id, case_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

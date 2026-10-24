"""
integration_example.py -- FastAPI Integration Example for Person 2
==================================================================
Shows Person 2 exactly how to call satyalens_analysis from their FastAPI app.

DO NOT copy this file verbatim. It is a reference example only.
Person 2 owns the FastAPI application, case management, file upload,
user consent, and final risk decision logic.

Dependency (add to Person 2's requirements.txt):
    pip install -e /path/to/IIT      # editable install from Person 1's workspace
    # or copy satyalens_analysis/ into Person 2's project root.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# FastAPI snippet — reference pattern for Person 2
# ---------------------------------------------------------------------------
#
# Key security requirements demonstrated here:
#   1. Server-side random filenames (uuid4) — never use the client filename as path.
#   2. MIME-type allowlist enforced before the file is written to disk.
#   3. Maximum upload size enforced (10 MB audio, 5 MB screenshot).
#   4. Temporary files deleted in a finally block covering BOTH success and failure.
#   5. Upload directory is NOT inside a web-served directory.

FASTAPI_SNIPPET = '''
# In Person 2\'s FastAPI app (e.g. routers/analysis.py)
#
# Security notes
# --------------
# - Never use upload.filename to construct a filesystem path.
# - Always generate a random server-side name (uuid4 + fixed extension).
# - Validate MIME type / extension against an allowlist.
# - Cap file sizes before writing to prevent denial-of-service.
# - Delete temporary files in a finally block so they are removed on errors too.

from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse
from typing import Optional
import os, uuid, shutil, mimetypes

from satyalens_analysis import run_analysis

router = APIRouter()

# Store uploads outside any web-served directory.
_UPLOAD_DIR = os.environ.get("SATYALENS_UPLOAD_DIR", "/tmp/satyalens_uploads")
os.makedirs(_UPLOAD_DIR, exist_ok=True)

# Allowlists — only these types are accepted.
_AUDIO_ALLOWED_EXTENSIONS = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}
_IMAGE_ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp"}

# Maximum file sizes in bytes.
_MAX_AUDIO_BYTES = 10 * 1024 * 1024   # 10 MB
_MAX_IMAGE_BYTES = 5 * 1024 * 1024    # 5 MB


def _safe_save(upload: UploadFile, allowed_extensions: set, max_bytes: int) -> str:
    """
    Save an UploadFile to a temporary path using a random server-side name.

    Parameters
    ----------
    upload : UploadFile
        The incoming upload object.
    allowed_extensions : set[str]
        Allowlist of lowercase file extensions (e.g. {".png", ".jpg"}).
    max_bytes : int
        Maximum permitted file size in bytes.

    Returns
    -------
    str
        Absolute path to the saved temporary file.

    Raises
    ------
    HTTPException(400) if the extension or size is not permitted.
    """
    # Derive extension from the ORIGINAL filename for type-checking only.
    # The extension is validated against the allowlist; it is NEVER used as
    # part of the saved path directly.
    original_name = upload.filename or ""
    _, ext = os.path.splitext(original_name)
    ext = ext.lower()

    if ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=(
                f"File type {ext!r} is not allowed. "
                f"Permitted: {sorted(allowed_extensions)}"
            ),
        )

    # Generate a random server-side filename using only the validated extension.
    server_filename = f"{uuid.uuid4().hex}{ext}"
    dest_path = os.path.join(_UPLOAD_DIR, server_filename)

    # Stream to disk with size enforcement.
    bytes_written = 0
    with open(dest_path, "wb") as out:
        while chunk := upload.file.read(65536):
            bytes_written += len(chunk)
            if bytes_written > max_bytes:
                out.close()
                os.remove(dest_path)
                raise HTTPException(
                    status_code=413,
                    detail=f"File exceeds maximum size of {max_bytes // 1024 // 1024} MB.",
                )
            out.write(chunk)

    return dest_path


@router.post("/api/analyse")
async def analyse_evidence(
    case_id: str = Form(...),
    message_text: Optional[str] = Form(None),
    expected_amount: Optional[str] = Form(None),
    expected_payee: Optional[str] = Form(None),
    expected_transaction_id: Optional[str] = Form(None),
    audio_file: Optional[UploadFile] = File(None),
    screenshot_file: Optional[UploadFile] = File(None),
):
    """
    Person 2\'s FastAPI endpoint that calls Person 1\'s analysis package.

    File upload security
    --------------------
    * Audio and screenshot files are saved with random server-side names.
    * Client-supplied filenames are NEVER used to construct filesystem paths.
    * Extension is validated against a fixed allowlist.
    * File size is capped before writing.
    * Temporary files are always deleted in the finally block.

    Analysis output
    ---------------
    The returned JSON dict conforms to satyalens_analysis schema v1.0.
    Person 2 uses result["audio"], result["payment"], result["context"] to
    feed their own risk decision engine.  This endpoint does NOT return a
    final risk verdict.
    """
    audio_path = None
    screenshot_path = None

    try:
        if audio_file and audio_file.filename:
            audio_path = _safe_save(audio_file, _AUDIO_ALLOWED_EXTENSIONS, _MAX_AUDIO_BYTES)

        if screenshot_file and screenshot_file.filename:
            screenshot_path = _safe_save(
                screenshot_file, _IMAGE_ALLOWED_EXTENSIONS, _MAX_IMAGE_BYTES
            )

        # Build expected payment fields.
        expected_payment_fields: dict = {}
        if expected_amount:
            expected_payment_fields["amount"] = expected_amount
        if expected_payee:
            expected_payment_fields["payee_name"] = expected_payee
        if expected_transaction_id:
            expected_payment_fields["transaction_id"] = expected_transaction_id

        # -------------------------------------------------------------------
        # CALL PERSON 1\'S ANALYSIS PACKAGE
        # -------------------------------------------------------------------
        result = run_analysis(
            case_id=case_id,
            audio_path=audio_path,
            screenshot_path=screenshot_path,
            message_text=message_text,
            expected_payment_fields=expected_payment_fields or None,
        )
        # -------------------------------------------------------------------
        # Person 2 now uses:
        #   result["audio"]   -- deepfake detection signal (currently unavailable)
        #   result["payment"] -- OCR field extraction and comparison
        #   result["context"] -- scam-context rule flags
        # to drive their own risk decision engine.
        # This package does NOT return a final risk verdict.
        # -------------------------------------------------------------------

        return JSONResponse(content=result)

    finally:
        # Always clean up temporary files — runs on both success and exception.
        for p in [audio_path, screenshot_path]:
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError as err:
                    # Log but do not re-raise; cleanup failure must not mask
                    # the original response or exception.
                    import logging
                    logging.getLogger(__name__).warning(
                        "Could not delete temporary file %s: %s", p, err
                    )
'''


# ---------------------------------------------------------------------------
# Standalone demo (run directly: python integration_example.py)
# ---------------------------------------------------------------------------

def run_demo():
    """Demonstrate the package without any uploaded files."""
    from satyalens_analysis import run_analysis
    import json

    sep = "=" * 68
    print(sep)
    print("SatyaLens Analysis Package -- Integration Demo")
    print(sep)

    # Scenario 1: High-risk scam message
    print("\n[Scenario 1] High-risk scam text only\n")
    result = run_analysis(
        case_id="DEMO-001",
        message_text=(
            "URGENT! I am calling from your bank. Your account will be blocked "
            "in 5 minutes. Act now! Share your OTP with me. Don't tell anyone. "
            "Pay Rs.25,000 via gift card to avoid legal action."
        ),
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))

    print("\n" + sep)

    # Scenario 2: Clean message
    print("\n[Scenario 2] Clean / legitimate-looking text\n")
    result2 = run_analysis(
        case_id="DEMO-002",
        message_text="Please transfer the invoice amount to our usual account by Friday.",
    )
    print(json.dumps(result2, indent=2, ensure_ascii=False))

    print("\n" + sep)

    # Scenario 3: Missing audio file (demonstrates graceful failure)
    print("\n[Scenario 3] Missing audio file (graceful failure)\n")
    result3 = run_analysis(
        case_id="DEMO-003",
        audio_path="/nonexistent/voice_note.wav",
        message_text="Please verify this voice note.",
    )
    print(json.dumps(result3, indent=2, ensure_ascii=False))
    print(
        "\nNOTE: audio status =",
        result3["audio"]["status"],
        "-- this does NOT mean the audio is genuine.",
    )


if __name__ == "__main__":
    run_demo()

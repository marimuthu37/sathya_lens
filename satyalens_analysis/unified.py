"""
unified.py — Unified Analysis Interface for SatyaLens
======================================================
Provides run_analysis(), the single entry point that Person 2's FastAPI
backend calls.  This function orchestrates all three analysis components
(audio, payment, context) and returns a single AnalysisResult dict.

Calling convention (for FastAPI integration)
--------------------------------------------
    from satyalens_analysis import run_analysis

    result = run_analysis(
        case_id="CASE-001",
        audio_path="/uploads/voice_note.wav",           # optional
        screenshot_path="/uploads/payment_ss.png",      # optional
        message_text="Act now or your account is blocked!",  # optional
        expected_payment_fields={                        # optional
            "amount": "5000",
            "payee_name": "John Doe",
            "transaction_id": "TXN123456",
        },
    )
    # result is a JSON-serialisable dict.
    return JSONResponse(content=result)

Component isolation
-------------------
Each component is wrapped in an independent try/except.  A failure in
one component does NOT abort the other components.  Errors are collected
in result["errors"] and the affected component returns a 'failed' status.

IMPORTANT
---------
* This function does NOT make a final risk decision.
* A failed or unavailable component NEVER implies the input is genuine.
* Do not pass secrets, PINs, or passwords as any input field.
"""

from __future__ import annotations

import logging
import traceback
from typing import Any, Dict, List, Optional

from satyalens_analysis.schema import (
    AnalysisResult,
    build_empty_result,
    audio_failed,
    payment_failed,
    context_failed,
    ComponentStatus,
    AudioResult,
    PaymentResult,
    ContextResult,
)

logger = logging.getLogger(__name__)


def run_analysis(
    case_id: str = "",
    audio_path: Optional[str] = None,
    screenshot_path: Optional[str] = None,
    message_text: Optional[str] = None,
    expected_payment_fields: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Run all three analysis components and return a unified JSON-compatible dict.

    Parameters
    ----------
    case_id : str
        Caller-supplied case identifier. Echoed back in the response.
        Must not contain sensitive data.
    audio_path : str, optional
        Path to an audio file for deepfake detection.
        If None, audio component returns status='not_applicable'.
    screenshot_path : str, optional
        Path to a payment screenshot image for OCR analysis.
        If None, payment component returns status='not_applicable'.
    message_text : str, optional
        Text of the suspicious message / scenario for context analysis.
        If None, context component returns status='not_applicable'.
    expected_payment_fields : dict, optional
        Expected field values to compare against OCR output.
        Keys: 'amount', 'transaction_id', 'date', 'payee_name',
              'payer_name', 'upi_id', 'status_text', 'payment_method'.
        If None, fields are extracted but not compared.

    Returns
    -------
    dict
        JSON-serialisable dict conforming to schema_version="1.0".
        See schema.py and INTEGRATION_CONTRACT.md for field documentation.
    """
    result = build_empty_result(case_id=case_id)
    global_errors: List[str] = []

    # ------------------------------------------------------------------
    # Component 1: Audio detection
    # ------------------------------------------------------------------
    if audio_path:
        try:
            from satyalens_analysis.audio_detector import analyse_audio
            result.audio = analyse_audio(audio_path)
            logger.info(
                "unified: audio analysis complete — status=%s",
                result.audio.status.value,
            )
        except Exception as exc:  # noqa: BLE001
            err = f"Audio component unexpected error: {exc}"
            logger.exception("unified: %s", err)
            global_errors.append(err)
            result.audio = audio_failed(err)
    else:
        result.audio = AudioResult(
            status=ComponentStatus.NOT_APPLICABLE,
            reason="No audio path provided.",
        )

    # ------------------------------------------------------------------
    # Component 2: Payment OCR
    # ------------------------------------------------------------------
    if screenshot_path:
        try:
            from satyalens_analysis.payment_ocr import analyse_payment
            result.payment = analyse_payment(
                image_path=screenshot_path,
                expected_fields=expected_payment_fields,
            )
            logger.info(
                "unified: payment analysis complete — status=%s",
                result.payment.status.value,
            )
        except Exception as exc:  # noqa: BLE001
            err = f"Payment component unexpected error: {exc}"
            logger.exception("unified: %s", err)
            global_errors.append(err)
            result.payment = payment_failed(err)
    else:
        result.payment = PaymentResult(
            status=ComponentStatus.NOT_APPLICABLE,
            limitations=["No screenshot path provided."],
        )

    # ------------------------------------------------------------------
    # Component 3: Context analysis
    # ------------------------------------------------------------------
    if message_text:
        try:
            from satyalens_analysis.context_analyser import analyse_context
            result.context = analyse_context(message_text)
            logger.info(
                "unified: context analysis complete — status=%s, flags=%d",
                result.context.status.value,
                len(result.context.flags),
            )
        except Exception as exc:  # noqa: BLE001
            err = f"Context component unexpected error: {exc}"
            logger.exception("unified: %s", err)
            global_errors.append(err)
            result.context = context_failed(err)
    else:
        result.context = ContextResult(
            status=ComponentStatus.NOT_APPLICABLE,
            limitations=["No message text provided."],
        )

    result.errors = global_errors

    return result.to_dict()

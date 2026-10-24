"""
audio_detector.py — Audio Deepfake / Anti-Spoofing Detection
=============================================================

VERIFIED MODEL STATUS (as of 2026-10-23)
-----------------------------------------
The originally specified model ID "Jenrola/AASIST-Speech-Deepfake-Detector"
could not be confirmed to exist as a publicly accessible HuggingFace Hub
repository (returns 401 / no public search evidence).

The verified public AASIST model is:
  "SpeechAntiSpoofingBenchmarks/AASIST"
  Licence: MIT (inherited from clovaai/aasist — Copyright NAVER Corporation)
  Source:  https://github.com/clovaai/aasist

INTEGRATION BLOCKER
-------------------
The AASIST architecture is a custom graph attention network (heterogeneous
stacking graph layers + SincNet front-end). It is NOT compatible with
Hugging Face's AutoModelForAudioClassification API. Loading it requires:

  1. The AASIST custom Python package / source from the HF repo.
  2. Custom state-dict loading (torch .pth checkpoint, not transformers weights).
  3. Custom inference wrapper (AASIST().load() + score_batch()).

This means standard `transformers.AutoModelForAudioClassification.from_pretrained()`
will FAIL even when torch and torchaudio are installed.

CURRENT BEHAVIOUR
-----------------
Until a verified, API-compatible checkpoint is identified and integrated:
  - analyse_audio() returns status='unavailable' for all audio inputs.
  - The reason field states the integration blocker explicitly.
  - This NEVER implies the audio is genuine or the request is safe.
  - No fabricated scores, findings or accuracy metrics are returned.

FUTURE INTEGRATION PATH (documented for Person 2)
--------------------------------------------------
To enable real audio analysis, the following steps are required:

  Option A: Use SpeechAntiSpoofingBenchmarks/AASIST with custom loader
    pip install torch torchaudio huggingface_hub
    from huggingface_hub import snapshot_download
    # Download model repo then import AASIST class from it
    # Requires sys.path manipulation to import custom model code

  Option B: Use a transformers-native audio classifier
    E.g. m3hrdadfi/wav2vec2-base-100k-voxpopuli-gtzan-music (audio clf example)
    Or find/wait for a verified AASIST HF model with AutoModel support.

Licence
-------
AASIST model weights: MIT Licence — Copyright NAVER Corporation.
See: https://github.com/clovaai/aasist/blob/master/LICENSE
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Tuple

from satyalens_analysis.schema import (
    AudioResult,
    ComponentStatus,
    audio_unavailable,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Verified model information (not yet successfully integrated)
# ---------------------------------------------------------------------------

_VERIFIED_MODEL_ID = "SpeechAntiSpoofingBenchmarks/AASIST"
_MODEL_LICENCE = (
    "MIT Licence — Copyright NAVER Corporation. "
    "Source: https://github.com/clovaai/aasist/blob/master/LICENSE"
)

# Decision thresholds (for future use when model is integrated)
_SPOOF_THRESHOLD   = 0.65
_GENUINE_THRESHOLD = 0.35
_MIN_DURATION_SEC  = 1.0
_RECOMMENDED_SR    = 16000

# ---------------------------------------------------------------------------
# Integration blocker explanation (returned in every result until resolved)
# ---------------------------------------------------------------------------

_BLOCKER_REASON = (
    "Audio deepfake detection is currently unavailable. "
    "The AASIST model (SpeechAntiSpoofingBenchmarks/AASIST, MIT licence) uses a "
    "custom graph-attention-network architecture that is NOT compatible with "
    "transformers.AutoModelForAudioClassification. A custom loader is required. "
    "See audio_detector.py module docstring for the integration path. "
    "IMPORTANT: This does not indicate the audio is genuine."
)

_STATIC_LIMITATIONS = [
    "Audio deepfake detection requires additional integration work before it is operational.",
    "AASIST was trained on ASVspoof 2019 LA data. Generalisation to real-world telephone "
    "audio, codec artefacts and non-English speech is limited.",
    "A raw model score is NOT a calibrated probability — it is a model output only.",
    "An 'unavailable' result NEVER implies the audio is genuine or the request is safe.",
    "This output is a signal, not a verdict. Human review is required.",
    f"Verified model: {_VERIFIED_MODEL_ID} | Licence: MIT (NAVER Corporation)",
]

# ---------------------------------------------------------------------------
# Dependency check (kept for future activation)
# ---------------------------------------------------------------------------

def _check_audio_deps() -> Tuple[bool, str]:
    """Return (available, reason). Checks torch/torchaudio/huggingface_hub."""
    missing = []
    try:
        import torch  # noqa: F401
    except ImportError:
        missing.append("torch")
    try:
        import torchaudio  # noqa: F401
    except ImportError:
        missing.append("torchaudio")
    try:
        from huggingface_hub import snapshot_download  # noqa: F401
    except ImportError:
        missing.append("huggingface_hub")

    if missing:
        deps = ", ".join(missing)
        return False, (
            f"Missing audio analysis dependencies: {deps}. "
            f"Install with: pip install {deps.replace(',', '')}. "
            "Audio detection is unavailable without these packages."
        )
    return True, ""


_AUDIO_DEPS_OK, _AUDIO_DEP_REASON = _check_audio_deps()

# ---------------------------------------------------------------------------
# Input quality checks (kept for future activation once model is integrated)
# ---------------------------------------------------------------------------

def _check_input_quality(waveform, sample_rate: int) -> List[str]:
    """Return a list of input quality warnings (may be empty)."""
    warnings_out: List[str] = []
    duration = waveform.shape[-1] / sample_rate
    if duration < _MIN_DURATION_SEC:
        warnings_out.append(
            f"Audio is very short ({duration:.2f}s). "
            "Detectors perform poorly on clips under 1 second."
        )
    if sample_rate != _RECOMMENDED_SR:
        warnings_out.append(
            f"Sample rate is {sample_rate} Hz; model expects {_RECOMMENDED_SR} Hz. "
            "Audio will be resampled; quality may affect results."
        )
    max_amp = waveform.abs().max().item()
    if max_amp >= 0.999:
        warnings_out.append(
            "Waveform appears clipped (max amplitude >= 0.999). "
            "Clipping may distort model features."
        )
    return warnings_out


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def analyse_audio(audio_path: str) -> AudioResult:
    """
    Attempt audio deepfake detection.

    CURRENT STATE: Returns status='unavailable' for all inputs.
    The AASIST model requires a custom loader that is not yet integrated.
    See module docstring for the integration path.

    Parameters
    ----------
    audio_path : str
        Path to an audio file. Used only for existence validation at present.

    Returns
    -------
    AudioResult
        status='not_applicable' — No audio path provided.
        status='unavailable'    — Model integration blocker (current state).
        status='failed'         — File access error (even when model available).

    IMPORTANT
    ---------
    * 'unavailable' NEVER implies the audio is genuine.
    * Do not interpret this result as evidence of authenticity.
    """
    if not audio_path:
        return AudioResult(
            status=ComponentStatus.NOT_APPLICABLE,
            reason="No audio file path provided.",
        )

    # File existence check — report failed if file missing
    path = Path(audio_path)
    if not path.exists():
        return AudioResult(
            status=ComponentStatus.FAILED,
            reason=f"Audio file not found: {audio_path!r}. "
                   "Additionally, audio model integration is not yet complete — "
                   "see audio_detector.py for details.",
            limitations=list(_STATIC_LIMITATIONS),
        )
    if not path.is_file():
        return AudioResult(
            status=ComponentStatus.FAILED,
            reason=f"Path is not a file: {audio_path!r}.",
            limitations=list(_STATIC_LIMITATIONS),
        )

    # File exists but model integration is not complete
    return audio_unavailable(
        reason=_BLOCKER_REASON,
        limitations=list(_STATIC_LIMITATIONS),
    )

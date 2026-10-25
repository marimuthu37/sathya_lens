"""
SatyaLens Analysis Package — Person 1: AI Detection & Evidence Analysis
=======================================================================
Provides four analysis components:

  1. audio           — Audio deepfake / spoofing detection interface
                       (currently always 'unavailable'; no model loader yet)
  2. payment         — Payment screenshot OCR and field reconciliation
  3. context         — Financial scam-context rule analysis
  4. text_classifier — TF-IDF + Logistic Regression message classifier
                       (optional [ml] extra; trained offline)

All components return dicts that conform to AnalysisResult schema
(see schema.py).  Person 2's FastAPI backend imports and calls
`run_analysis()` from this package.

Licence notice
--------------
AASIST model weights: MIT Licence — NAVER Corporation
pytesseract / Tesseract-OCR: Apache 2.0
All other dependencies: see requirements.txt

IMPORTANT
---------
* A failed or unavailable module NEVER implies the media is genuine or
  the request is safe.
* Model outputs are NOT calibrated confidence scores. Treat them as
  signals, not verdicts.
* This package does not issue a final Low/Verify/High-Risk decision.
  That responsibility belongs to Person 2's risk engine.
"""

from satyalens_analysis.schema import AnalysisResult, build_empty_result
from satyalens_analysis.unified import run_analysis

__all__ = ["run_analysis", "AnalysisResult", "build_empty_result"]
__version__ = "1.0.0"

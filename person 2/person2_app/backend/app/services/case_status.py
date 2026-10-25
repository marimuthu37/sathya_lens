"""
app/services/case_status.py
───────────────────────────
Keeps Case.analysis_status consistent with the analysis state of its evidence.
Previously nothing updated it, so every case showed "not_started" forever.

  no evidence, or none has been analysed        → NOT_STARTED
  any analysis pending or running               → RUNNING
  any analysis failed                           → FAILED
  every item completed (or unavailable)         → DONE
  otherwise (some items not yet analysed)       → QUEUED  ("partly analysed")

DONE means analysis ran; it says nothing about whether the evidence is genuine.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import AnalysisResult, AnalysisResultStatus, AnalysisStatus, Case, Evidence


def refresh_analysis_status(db: Session, case: Case) -> AnalysisStatus:
    """Recompute and set case.analysis_status; the caller commits."""
    db.flush()  # sessions use autoflush=False; make pending results visible to the query
    statuses = [
        s for (s,) in db.query(AnalysisResult.status)
        .select_from(Evidence)
        .outerjoin(AnalysisResult, AnalysisResult.evidence_id == Evidence.id)
        .filter(Evidence.case_id == case.id)
        .all()
    ]
    analysed = [s for s in statuses if s is not None]
    if not analysed:
        new = AnalysisStatus.NOT_STARTED
    elif any(s in (AnalysisResultStatus.PENDING, AnalysisResultStatus.RUNNING) for s in analysed):
        new = AnalysisStatus.RUNNING
    elif AnalysisResultStatus.FAILED in analysed:
        new = AnalysisStatus.FAILED
    elif len(analysed) == len(statuses):
        new = AnalysisStatus.DONE
    else:
        new = AnalysisStatus.QUEUED
    case.analysis_status = new
    return new

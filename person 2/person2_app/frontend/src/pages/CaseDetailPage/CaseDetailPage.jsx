import { useState } from 'react'
import { useCaseDetail } from '../../hooks/useCases'
import { deleteCase } from '../../api/api'
import { formatDateTime, shortId, statusPillClass, statusLabel } from '../../utils/formatters'
import ConsentPanel from '../../components/ConsentPanel/ConsentPanel'
import EvidencePanel from '../../components/EvidencePanel/EvidencePanel'
import DecisionPanel from '../../components/DecisionPanel/DecisionPanel'
import ChecklistPanel from '../../components/ChecklistPanel/ChecklistPanel'
import './CaseDetailPage.css'

/**
 * CaseDetailPage
 * ───────────────
 * GET  /api/cases/{case_id}               — full case view
 * DELETE /api/cases/{case_id}             — with confirmation modal
 *
 * Segment 4 additions:
 *   - ConsentPanel shown when consent_status === 'pending'
 *   - EvidencePanel shown when consent_status === 'granted'
 *
 * Props:
 *   caseId     (string) — UUID of the case to display
 *   onNavigate (fn)     — navigate to a named page
 *   onDeleted  (fn)     — called after successful deletion
 */
export default function CaseDetailPage({ caseId, onNavigate, onDeleted }) {
  const { caseData, loading, error, reload } = useCaseDetail(caseId)
  const [showConfirm, setShowConfirm] = useState(false)
  const [deleting,    setDeleting]    = useState(false)
  const [deleteError, setDeleteError] = useState(null)
  const [decisionCategory, setDecisionCategory] = useState(null)   // from DecisionPanel

  // ── Delete case flow ────────────────────────────────────────────────────────
  async function handleDelete() {
    setDeleting(true)
    setDeleteError(null)
    try {
      await deleteCase(caseId)
      setShowConfirm(false)
      onDeleted?.()
    } catch (err) {
      setDeleteError(err.status === 404
        ? 'This case no longer exists.'
        : err.message || 'Failed to delete. Please try again.')
      setDeleting(false)
    }
  }

  // ── Loading skeleton ────────────────────────────────────────────────────────
  if (loading) {
    return (
      <main className="page-content" id="case-detail-page">
        <div className="container">
          <button className="back-btn" onClick={() => onNavigate('history')}>← Case History</button>
          <div className="detail-skeleton-grid">
            {[1, 2, 3, 4].map(i => <div key={i} className="detail-skeleton" />)}
          </div>
        </div>
      </main>
    )
  }

  // ── Error state ─────────────────────────────────────────────────────────────
  if (error) {
    return (
      <main className="page-content" id="case-detail-page">
        <div className="container">
          <button className="back-btn" onClick={() => onNavigate('history')}>← Case History</button>
          <div className="alert alert--error" role="alert">
            <span className="alert__icon">⚠️</span>
            <span>{error}&nbsp;—&nbsp;
              <button className="alert-retry-btn" onClick={reload}>Retry</button>
            </span>
          </div>
        </div>
      </main>
    )
  }

  if (!caseData) return null

  const c = caseData
  const hasConsent = c.consent_status === 'granted'

  return (
    <main className="page-content" id="case-detail-page">
      <div className="container">

        {/* Breadcrumb */}
        <nav className="breadcrumb" aria-label="Breadcrumb">
          <button className="breadcrumb__link" onClick={() => onNavigate('history')}>Case History</button>
          <span className="breadcrumb__sep">›</span>
          <span>Case #{shortId(c.id)}</span>
        </nav>

        {/* Title bar */}
        <div className="detail-title-bar">
          <div className="detail-title-bar__left">
            <h1 className="section-heading" style={{ marginBottom: 0 }}>
              Case <span>#{shortId(c.id)}</span>
            </h1>
            <span className={`status-pill ${statusPillClass(c.status)}`}>
              {statusLabel(c.status)}
            </span>
          </div>
          <div className="detail-title-bar__actions">
            <button
              className="btn btn--ghost"
              onClick={reload}
              aria-label="Refresh case"
              title="Refresh"
            >
              ↻ Refresh
            </button>
            <button
              id="delete-case-btn"
              className="btn btn--danger"
              onClick={() => setShowConfirm(true)}
            >
              🗑 Delete Case
            </button>
          </div>
        </div>

        {/* Description */}
        {c.description && (
          <div className="card detail-desc-card">
            <p className="detail-field__label">Description</p>
            <p className="detail-desc-text">{c.description}</p>
          </div>
        )}
        {!c.description && (
          <div className="detail-no-desc">No description provided.</div>
        )}

        {/* Status overview */}
        <div className="card detail-status-card">
          <h2 className="detail-section-title">Status Overview</h2>
          <div className="detail-grid">
            <StatusField label="Case ID"        value={<span style={{ fontFamily: 'var(--font-mono)', fontSize: '0.8rem' }}>{c.id}</span>} />
            <StatusField label="Case Status"    value={<span className={`status-pill ${statusPillClass(c.status)}`}>{statusLabel(c.status)}</span>} />
            <StatusField label="Consent"        value={<ConsentBadge status={c.consent_status} at={c.consent_at} />} />
            <StatusField label="Analysis"       value={statusLabel(c.analysis_status)} />
            <StatusField label="Decision"       value={
              // The DecisionPanel's category is authoritative: INSUFFICIENT_EVIDENCE
              // is stored as decision=null, so null alone does not mean "none yet".
              decisionCategory ? statusLabel(decisionCategory)
                : c.decision ? statusLabel(c.decision)
                : <span style={{ color: 'var(--color-text-muted)' }}>Not computed yet</span>
            } />
            <StatusField label="Evidence Items" value={c.evidence_items?.length ?? 0} />
            <StatusField label="Opened"         value={formatDateTime(c.created_at)} />
            <StatusField label="Last Updated"   value={formatDateTime(c.updated_at)} />
          </div>
        </div>

        {/* ── Segment 4: Consent + Evidence section ── */}
        <section className="detail-evidence-section" aria-label="Evidence management">
          <h2 className="section-heading" style={{ fontSize: '1.1rem', marginBottom: 'var(--space-4)' }}>
            🔒 Consent & <span>Evidence</span>
          </h2>

          {!hasConsent ? (
            /* Show consent gate */
            <ConsentPanel caseId={c.id} onConsented={reload} />
          ) : (
            /* Show upload + evidence list */
            <EvidencePanel
              caseId={c.id}
              evidenceItems={c.evidence_items ?? []}
              onChanged={reload}
              onAnalysed={() => reload({ silent: true })}
            />
          )}
        </section>

        {/* ── Decision engine section ── */}
        <DecisionPanel
          caseId={c.id}
          consentGranted={hasConsent}
          onDecision={(category, opts) => {
            setDecisionCategory(category)
            if (opts?.computed) reload({ silent: true })   // Case.decision/updated_at changed
          }}
        />

        {/* ── Verification checklist + feedback ── */}
        <ChecklistPanel caseId={c.id} />

      </div>

      {/* ── Delete Case Confirmation Modal ── */}
      {showConfirm && (
        <div
          className="modal-overlay"
          role="dialog"
          aria-modal="true"
          aria-labelledby="delete-modal-title"
          onClick={e => { if (e.target === e.currentTarget && !deleting) setShowConfirm(false) }}
        >
          <div className="modal">
            <h2 id="delete-modal-title" className="modal__title">Delete Case?</h2>
            <p className="modal__body">
              Permanently delete case <strong>#{shortId(c.id)}</strong> and all its
              associated evidence metadata and stored files? This action cannot be undone.
            </p>

            {deleteError && (
              <div className="alert alert--error" role="alert" style={{ marginBottom: 'var(--space-4)' }}>
                <span className="alert__icon">⚠️</span>
                <span>{deleteError}</span>
              </div>
            )}

            <div className="modal__actions">
              <button
                className="btn btn--ghost"
                onClick={() => { setShowConfirm(false); setDeleteError(null) }}
                disabled={deleting}
                autoFocus
              >
                Cancel
              </button>
              <button
                id="confirm-delete-btn"
                className="btn btn--danger"
                onClick={handleDelete}
                disabled={deleting}
                aria-busy={deleting}
              >
                {deleting
                  ? <><span className="spinner" aria-hidden="true" /> Deleting…</>
                  : '🗑 Yes, Delete'}
              </button>
            </div>
          </div>
        </div>
      )}
    </main>
  )
}

// ── Sub-components ────────────────────────────────────────────────────────────
function StatusField({ label, value }) {
  return (
    <div className="detail-field">
      <dt className="detail-field__label">{label}</dt>
      <dd className="detail-field__value">{value}</dd>
    </div>
  )
}

function ConsentBadge({ status, at }) {
  if (status === 'granted') {
    return (
      <span style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        <span className="status-pill status-pill--complete">✓ Granted</span>
        {at && <span style={{ fontSize: '0.7rem', color: 'var(--color-text-muted)', marginTop: 2 }}>{formatDateTime(at)}</span>}
      </span>
    )
  }
  if (status === 'revoked') return <span className="status-pill status-pill--archived">Revoked</span>
  return <span className="status-pill status-pill--pending">Pending</span>
}

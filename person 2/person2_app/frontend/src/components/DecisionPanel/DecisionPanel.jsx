import { useState, useEffect } from 'react'
import { computeDecision, getDecision } from '../../api/decision'
import './DecisionPanel.css'

/**
 * DecisionPanel
 * ──────────────
 * Displays the guidance decision for a case.
 *
 * Honesty guarantees:
 * - INSUFFICIENT_EVIDENCE is shown when analysis is unavailable/incomplete.
 * - No reassuring LOW_CONCERN is shown when there are no usable findings.
 * - Interpretation findings are labelled; limitations always visible.
 * - No numeric risk scores are displayed.
 * - Clear disclaimer: advisory only, not proof of fraud or safety.
 * - Independent-verification checklist always present.
 *
 * Props:
 *   caseId       (string)
 *   consentGranted (bool) — show gate if consent not yet given
 *   onDecision   (fn(category|null, {computed}), optional) — called with the current
 *                decision category when loaded or computed (null if none yet);
 *                computed=true only right after POST /decision
 */
export default function DecisionPanel({ caseId, consentGranted, onDecision }) {
  const [result,    setResult]    = useState(null)
  const [loading,   setLoading]   = useState(true)
  const [computing, setComputing] = useState(false)
  const [error,     setError]     = useState(null)

  // ── Load existing decision on mount ────────────────────────────────────────
  useEffect(() => {
    if (!consentGranted) { setLoading(false); return }
    let cancelled = false
    setLoading(true)
    getDecision(caseId)
      .then(d => { if (!cancelled) { setResult(d); setError(null); onDecision?.(d.category) } })
      .catch(err => {
        if (cancelled) return
        if (err.status === 404) { setResult(null); onDecision?.(null) }
        else setError(err.message)
      })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [caseId, consentGranted])

  // ── Compute / re-compute ───────────────────────────────────────────────────
  async function handleCompute() {
    setComputing(true)
    setError(null)
    try {
      const d = await computeDecision(caseId)
      setResult(d)
      onDecision?.(d.category, { computed: true })
    } catch (err) {
      setError(err.message || 'Failed to compute decision.')
    } finally {
      setComputing(false)
    }
  }

  // ── Consent gate ───────────────────────────────────────────────────────────
  if (!consentGranted) {
    return (
      <div className="dp-gate">
        <span className="dp-gate__icon">🔒</span>
        <span>Consent is required before a decision can be requested.</span>
      </div>
    )
  }

  if (loading) return <div className="dp-loading">Loading decision…</div>

  return (
    <section className="decision-panel" aria-label="Decision summary">

      <div className="dp-header">
        <h2 className="dp-title">⚖️ Guidance <span>Decision</span></h2>
        <button
          className="btn btn--primary dp-compute-btn"
          onClick={handleCompute}
          disabled={computing}
          aria-busy={computing}
          id="compute-decision-btn"
        >
          {computing
            ? <><span className="spinner" aria-hidden="true" /> Computing…</>
            : result ? '↻ Re-evaluate' : '⚖️ Get Decision'}
        </button>
      </div>

      {/* Persistent disclaimer */}
      <div className="dp-disclaimer" role="note">
        <span className="dp-disclaimer__icon">⚠️</span>
        <span>
          This is <strong>decision support only</strong>, not legal or financial advice.
          Guidance categories do not prove fraud or authenticity. Always verify
          independently through official channels before acting.
        </span>
      </div>

      {error && (
        <div className="alert alert--error" role="alert">
          <span className="alert__icon">⚠️</span>
          <span>{error}</span>
        </div>
      )}

      {!result && !error && (
        <div className="dp-empty">
          <p>No decision has been computed yet for this case.</p>
          <p className="dp-empty__hint">
            Ensure evidence is uploaded and analysed, then click <strong>Get Decision</strong>.
          </p>
        </div>
      )}

      {result && (
        <div className="dp-result">

          {/* Category banner */}
          <CategoryBanner category={result.category} explanation={result.short_explanation} />

          {/* Factors of concern */}
          {result.factors_of_concern.length > 0 && (
            <section className="dp-section">
              <h3 className="dp-section-title">🔴 Factors of Concern</h3>
              <ul className="dp-list dp-list--concern">
                {result.factors_of_concern.map((f, i) => (
                  <li key={i} className="dp-list__item">{f}</li>
                ))}
              </ul>
            </section>
          )}

          {/* Unknown / unassessed */}
          {result.factors_unknown.length > 0 && (
            <section className="dp-section">
              <h3 className="dp-section-title">❓ Could Not Assess</h3>
              <ul className="dp-list dp-list--unknown">
                {result.factors_unknown.map((f, i) => (
                  <li key={i} className="dp-list__item">{f}</li>
                ))}
              </ul>
            </section>
          )}

          {/* Conflicts */}
          {result.conflicts.length > 0 && (
            <section className="dp-section">
              <h3 className="dp-section-title">⚡ Conflicting Findings</h3>
              <ul className="dp-list dp-list--conflict">
                {result.conflicts.map((c, i) => (
                  <li key={i} className="dp-list__item">{c}</li>
                ))}
              </ul>
            </section>
          )}

          {/* Limitations */}
          {result.limitations.length > 0 && (
            <section className="dp-section">
              <h3 className="dp-section-title">📋 System Limitations</h3>
              <ul className="dp-list dp-list--limitations">
                {result.limitations.map((l, i) => (
                  <li key={i} className="dp-list__item">{l}</li>
                ))}
              </ul>
            </section>
          )}

          {/* Next steps — always show */}
          <section className="dp-section dp-section--steps">
            <h3 className="dp-section-title">✅ Independent Verification Checklist</h3>
            <ol className="dp-checklist">
              {result.next_steps.map((s, i) => (
                <li key={i} className="dp-checklist__item">
                  <span className="dp-checklist__num">{i + 1}</span>
                  <span>{s}</span>
                </li>
              ))}
            </ol>
          </section>

          {/* Meta */}
          <div className="dp-meta">
            <span>Rule set v{result.rule_version}</span>
            <span>Generated {new Date(result.generated_at).toLocaleString()}</span>
          </div>
        </div>
      )}
    </section>
  )
}

// ── Category banner ───────────────────────────────────────────────────────────

const CATEGORY_CONFIG = {
  insufficient_evidence: {
    icon: '📂',
    label: 'Insufficient Evidence',
    className: 'dp-banner--insufficient',
    note: 'Not enough usable analysis to reach an assessment. This does not mean the interaction is safe.',
  },
  low_concern: {
    icon: '🟢',
    label: 'Low Concern',
    className: 'dp-banner--low',
    note: 'No specific concern patterns detected in available evidence. This does not guarantee safety.',
  },
  verify_first: {
    icon: '🟡',
    label: 'Verify First',
    className: 'dp-banner--verify',
    note: 'Some findings warrant independent verification. This does not confirm fraudulent activity.',
  },
  high_risk: {
    icon: '🔴',
    label: 'High Risk',
    className: 'dp-banner--high',
    note: 'Findings associated with high-risk patterns detected. Strongly recommended: verify before acting.',
  },
}

function CategoryBanner({ category, explanation }) {
  const cfg = CATEGORY_CONFIG[category] ?? CATEGORY_CONFIG.insufficient_evidence
  return (
    <div className={`dp-banner ${cfg.className}`} role="status">
      <div className="dp-banner__header">
        <span className="dp-banner__icon" aria-hidden="true">{cfg.icon}</span>
        <span className="dp-banner__label">{cfg.label}</span>
      </div>
      <p className="dp-banner__explanation">{explanation}</p>
      <p className="dp-banner__note">{cfg.note}</p>
    </div>
  )
}

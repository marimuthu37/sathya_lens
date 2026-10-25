import { useState } from 'react'
import { recordConsent } from '../../api/api'
import './ConsentPanel.css'

/**
 * ConsentPanel
 * ─────────────
 * Shown when a case has consent_status === 'pending'.
 * User must actively tick the checkbox before submitting.
 *
 * Props:
 *   caseId      (string)
 *   onConsented (fn)  — called after consent is successfully recorded
 */
export default function ConsentPanel({ caseId, onConsented }) {
  const [agreed,     setAgreed]     = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error,      setError]      = useState(null)

  async function handleConsent(e) {
    e.preventDefault()
    if (!agreed || submitting) return
    setSubmitting(true)
    setError(null)
    try {
      await recordConsent(caseId)
      onConsented?.()
    } catch (err) {
      setError(err.message || 'Failed to record consent. Please try again.')
      setSubmitting(false)
    }
  }

  return (
    <section className="consent-panel card" aria-labelledby="consent-heading">
      <div className="consent-panel__icon" aria-hidden="true">🔐</div>

      <h2 id="consent-heading" className="consent-panel__title">
        Consent Required Before Upload
      </h2>

      <div className="consent-panel__body">
        <p>
          Before you can attach evidence to this case, you must confirm that you
          understand how your data will be handled.
        </p>

        <div className="consent-info-grid">
          <ConsentItem icon="📁" title="What is collected">
            The file you upload (e.g. a screenshot, audio recording, or PDF),
            together with its filename, file size, media type, and a SHA-256
            integrity hash.
          </ConsentItem>
          <ConsentItem icon="⚙️" title="Why it is processed">
            The SatyaLens prototype analyses uploaded evidence to help identify
            patterns associated with financial-fraud communications. This is a
            local prototype; processing occurs on the device running this server.
            Do not use this system for confidential or sensitive cases in a
            shared or networked environment.
          </ConsentItem>
          <ConsentItem icon="🗓️" title="Retention and deletion">
            Evidence is retained while the case is open. Deleting the case or
            deleting an individual evidence item removes the stored file and its
            database record from this application. There is no guarantee that
            copies do not exist elsewhere on the host filesystem (e.g. in OS
            temp files or filesystem snapshots). This is a local/demo prototype.
          </ConsentItem>
          <ConsentItem icon="⚠️" title="Limitations">
            This system is a prototype. Do <strong>not</strong> upload OTPs,
            PINs, banking passwords, or account credentials. SHA-256 hashes
            record what bytes were uploaded; they do not prove a file is
            genuine or unaltered before upload.
          </ConsentItem>
        </div>

        {error && (
          <div className="alert alert--error" role="alert">
            <span className="alert__icon">⚠️</span>
            <span>{error}</span>
          </div>
        )}

        <form onSubmit={handleConsent} className="consent-form">
          <label className="consent-checkbox" htmlFor="consent-agree">
            <input
              id="consent-agree"
              type="checkbox"
              checked={agreed}
              onChange={e => setAgreed(e.target.checked)}
              disabled={submitting}
              aria-required="true"
            />
            <span>
              I have read the above, I understand how my data will be used,
              and I give my explicit consent to upload evidence for this case.
            </span>
          </label>

          <button
            id="grant-consent-btn"
            type="submit"
            className="btn btn--primary"
            disabled={!agreed || submitting}
            aria-busy={submitting}
          >
            {submitting
              ? <><span className="spinner" aria-hidden="true" /> Recording consent…</>
              : '🔐 Grant Consent & Continue'}
          </button>
        </form>
      </div>
    </section>
  )
}

function ConsentItem({ icon, title, children }) {
  return (
    <div className="consent-item">
      <div className="consent-item__icon" aria-hidden="true">{icon}</div>
      <div>
        <p className="consent-item__title">{title}</p>
        <p className="consent-item__body">{children}</p>
      </div>
    </div>
  )
}

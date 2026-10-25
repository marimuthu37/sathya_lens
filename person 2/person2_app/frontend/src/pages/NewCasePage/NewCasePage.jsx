import { useState } from 'react'
import { createCase } from '../../api/api'
import './NewCasePage.css'

const MAX_DESC = 2000

/**
 * NewCasePage
 * ────────────
 * POST /api/cases — open a new verification case.
 *
 * Props:
 *   onNavigate  (fn) — navigate to another page
 *   onCreated   (fn: caseId → void) — called after successful creation
 */
export default function NewCasePage({ onNavigate, onCreated }) {
  const [description, setDescription] = useState('')
  const [submitting,  setSubmitting]  = useState(false)
  const [error,       setError]       = useState(null)
  const [success,     setSuccess]     = useState(null)

  const remaining = MAX_DESC - description.length
  const overLimit = remaining < 0

  async function handleSubmit(e) {
    e.preventDefault()
    if (submitting || overLimit) return

    setSubmitting(true)
    setError(null)
    setSuccess(null)

    try {
      const created = await createCase({ description: description.trim() || undefined })
      setSuccess(`Case #${created.id.slice(0, 8)} opened successfully.`)
      setDescription('')
      // Navigate to detail after a short delay so user sees the success message
      setTimeout(() => onCreated?.(created.id), 1200)
    } catch (err) {
      setError(err.message || 'Failed to create case. Please try again.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="page-content" id="new-case-page">
      <div className="container">

        {/* Back navigation */}
        <button className="back-btn" onClick={() => onNavigate('history')} aria-label="Back to case history">
          ← Case History
        </button>

        <div className="new-case-layout">

          {/* Header */}
          <header className="new-case-header">
            <div className="new-case-header__icon" aria-hidden="true">🛡️</div>
            <div>
              <h1 className="section-heading">
                Open New <span>Verification Case</span>
              </h1>
              <p className="section-sub">
                Describe the suspicious communication you received. Our system will guide
                you through the verification process step by step.
              </p>
            </div>
          </header>

          {/* Form card */}
          <div className="card new-case-card">

            {/* Success alert */}
            {success && (
              <div className="alert alert--success" role="status" aria-live="polite">
                <span className="alert__icon">✅</span>
                <span>{success}</span>
              </div>
            )}

            {/* Error alert */}
            {error && (
              <div className="alert alert--error" role="alert">
                <span className="alert__icon">⚠️</span>
                <span>{error}</span>
              </div>
            )}

            <form onSubmit={handleSubmit} noValidate>

              <div className="form-group">
                <label htmlFor="case-description" className="form-label">
                  Description of Suspicious Communication
                </label>
                <textarea
                  id="case-description"
                  className="form-textarea"
                  name="description"
                  rows={5}
                  placeholder="e.g. I received a call from someone claiming to be from SBI, asking me to share my OTP to 'unblock' my account…"
                  value={description}
                  onChange={e => setDescription(e.target.value)}
                  disabled={submitting}
                  aria-describedby="desc-hint desc-char"
                />
                <div className="new-case-meta">
                  <p id="desc-hint" className="form-hint">
                    Describe what happened in your own words. Include any numbers, names, or
                    websites mentioned. You can add audio or screenshots in the next step.
                  </p>
                  <span
                    id="desc-char"
                    className={`char-count${overLimit ? ' char-count--warn' : ''}`}
                    aria-live="polite"
                  >
                    {remaining < 200 ? `${remaining} remaining` : ''}
                  </span>
                </div>
                {overLimit && (
                  <p className="form-error" role="alert">
                    ⚠ Description exceeds 2 000 characters.
                  </p>
                )}
              </div>

              {/* Info note */}
              <div className="alert alert--info new-case-info">
                <span className="alert__icon">ℹ️</span>
                <span>
                  No personal data is required to open a case. File uploads and AI analysis
                  will be available after this step. A case ID will be generated automatically.
                </span>
              </div>

              {/* Actions */}
              <div className="new-case-actions">
                <button
                  type="button"
                  className="btn btn--ghost"
                  onClick={() => onNavigate('history')}
                  disabled={submitting}
                >
                  Cancel
                </button>
                <button
                  id="submit-new-case"
                  type="submit"
                  className="btn btn--primary"
                  disabled={submitting || overLimit}
                  aria-busy={submitting}
                >
                  {submitting ? (
                    <><span className="spinner" aria-hidden="true" /> Opening Case…</>
                  ) : (
                    '🛡️ Open Case'
                  )}
                </button>
              </div>

            </form>
          </div>

          {/* Side help panel */}
          <aside className="new-case-tips card" aria-label="Tips for a good case description">
            <h2 className="tips-heading">📋 What to Include</h2>
            <ul className="tips-list">
              {[
                'The date and time of the communication',
                'The channel: call, SMS, WhatsApp, email',
                'Any phone numbers, UPI IDs, or URLs mentioned',
                'What action you were asked to take',
                'Whether any payment was made',
                'Names or organisation claims made by the caller',
              ].map(tip => (
                <li key={tip} className="tips-list__item">
                  <span aria-hidden="true">›</span> {tip}
                </li>
              ))}
            </ul>
          </aside>

        </div>
      </div>
    </main>
  )
}

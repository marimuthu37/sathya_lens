import { useState, useEffect, useCallback } from 'react'
import { getChecklist, updateChecklistItem, getFeedback, submitFeedback } from '../../api/checklist'
import './ChecklistPanel.css'

/**
 * ChecklistPanel
 * ──────────────
 * Displays the independent-verification checklist and user feedback form
 * for a case. Mounted inside CaseDetailPage.
 *
 * Honesty guarantees:
 * - Disclaimer always visible: completion ≠ proof of safety.
 * - Checking an item records user intent only, not independent verification.
 * - India-specific items (helpline 1930, cybercrime.gov.in) always surfaced.
 * - No credentials, identity, or payment data are requested.
 *
 * Props:
 *   caseId (string)
 */
export default function ChecklistPanel({ caseId }) {
  const [checklist,     setChecklist]     = useState(null)   // ChecklistStateRead
  const [loadingList,   setLoadingList]   = useState(true)
  const [togglingId,    setTogglingId]    = useState(null)   // item currently being saved
  const [listError,     setListError]     = useState(null)

  const [feedback,      setFeedback]      = useState(null)   // FeedbackRead | null
  const [loadingFb,     setLoadingFb]     = useState(true)
  const [fbDraft,       setFbDraft]       = useState({
    verified_independently: null,
    still_has_concerns: null,
    result_useful: null,
    result_clear: null,
    note: '',
  })
  const [fbSaving,      setFbSaving]      = useState(false)
  const [fbError,       setFbError]       = useState(null)
  const [fbSaved,       setFbSaved]       = useState(false)

  // ── Load checklist ──────────────────────────────────────────────────────────
  const loadChecklist = useCallback(() => {
    setLoadingList(true)
    getChecklist(caseId)
      .then(d => { setChecklist(d); setListError(null) })
      .catch(e => setListError(e.message))
      .finally(() => setLoadingList(false))
  }, [caseId])

  // ── Load feedback ───────────────────────────────────────────────────────────
  const loadFeedback = useCallback(() => {
    setLoadingFb(true)
    getFeedback(caseId)
      .then(d => {
        setFeedback(d)
        setFbDraft({
          verified_independently: d.verified_independently,
          still_has_concerns: d.still_has_concerns,
          result_useful: d.result_useful,
          result_clear: d.result_clear,
          note: d.note ?? '',
        })
      })
      .catch(e => {
        if (e.status === 404) setFeedback(null)
        // 404 is normal on first load
      })
      .finally(() => setLoadingFb(false))
  }, [caseId])

  useEffect(() => { loadChecklist() }, [loadChecklist])
  useEffect(() => { loadFeedback() },  [loadFeedback])

  // ── Toggle a checklist item ─────────────────────────────────────────────────
  async function handleToggle(itemId, currentChecked) {
    setTogglingId(itemId)
    setListError(null)
    try {
      await updateChecklistItem(caseId, itemId, !currentChecked)
      await loadChecklist()
    } catch (e) {
      setListError(e.message || 'Failed to save checklist item.')
    } finally {
      setTogglingId(null)
    }
  }

  // ── Submit feedback ─────────────────────────────────────────────────────────
  async function handleFeedbackSubmit(e) {
    e.preventDefault()
    setFbSaving(true)
    setFbError(null)
    setFbSaved(false)
    try {
      const payload = {
        verified_independently: fbDraft.verified_independently,
        still_has_concerns:     fbDraft.still_has_concerns,
        result_useful:          fbDraft.result_useful,
        result_clear:           fbDraft.result_clear,
        note:                   fbDraft.note.trim() || null,
      }
      const saved = await submitFeedback(caseId, payload)
      setFeedback(saved)
      setFbSaved(true)
    } catch (e) {
      setFbError(e.message || 'Failed to save feedback.')
    } finally {
      setFbSaving(false)
    }
  }

  // Build a progress lookup
  const progressMap = checklist
    ? Object.fromEntries(checklist.progress.map(p => [p.item_id, p]))
    : {}

  // Group items by category
  const byCategory = checklist
    ? checklist.items.reduce((acc, item) => {
        if (!acc[item.category]) acc[item.category] = []
        acc[item.category].push(item)
        return acc
      }, {})
    : {}

  const CATEGORY_LABELS = {
    contact:      '📞 Contact Verification',
    payment:      '💳 Payment Checks',
    warning_signs:'⚠️ Warning Signs',
    credentials:  '🔒 Credentials & Passwords',
    reporting:    '🚨 Reporting',
  }

  return (
    <section className="checklist-panel" aria-label="Independent verification checklist">

      {/* ── Checklist section ── */}
      <div className="cl-header">
        <h2 className="cl-title">
          ✅ Independent <span>Verification Checklist</span>
        </h2>
      </div>

      {/* Persistent disclaimer */}
      <div className="cl-disclaimer" role="note">
        <span className="cl-disclaimer__icon">⚠️</span>
        <span>
          Ticking a step records that <strong>you</strong> completed it.
          It does <strong>not</strong> prove a payment, requester, or communication
          is legitimate or safe. Always exercise independent judgement.
        </span>
      </div>

      {listError && (
        <div className="alert alert--error" role="alert">
          <span className="alert__icon">⚠️</span>
          <span>{listError}</span>
        </div>
      )}

      {loadingList && <div className="cl-loading">Loading checklist…</div>}

      {checklist && (
        <div className="cl-body">
          {Object.entries(byCategory).map(([cat, items]) => (
            <div key={cat} className="cl-group">
              <h3 className="cl-group__title">
                {CATEGORY_LABELS[cat] ?? cat}
              </h3>
              <ul className="cl-items">
                {items.map(item => {
                  const progress  = progressMap[item.item_id]
                  const isChecked = progress?.checked ?? false
                  const isToggling = togglingId === item.item_id
                  return (
                    <li
                      key={item.item_id}
                      className={`cl-item ${isChecked ? 'cl-item--checked' : ''}`}
                    >
                      <label className="cl-item__label">
                        <input
                          type="checkbox"
                          className="cl-item__checkbox"
                          id={`chk-${item.item_id}`}
                          checked={isChecked}
                          disabled={isToggling}
                          onChange={() => handleToggle(item.item_id, isChecked)}
                          aria-label={item.text}
                        />
                        <span className="cl-item__box" aria-hidden="true">
                          {isToggling ? <span className="spinner spinner--sm" /> : isChecked ? '✓' : ''}
                        </span>
                        <span className="cl-item__text">
                          {item.text}
                          {item.india_only && (
                            <span className="cl-india-badge" title="India-specific guidance">🇮🇳</span>
                          )}
                        </span>
                      </label>
                      {item.detail && (
                        <p className="cl-item__detail">{item.detail}</p>
                      )}
                    </li>
                  )
                })}
              </ul>
            </div>
          ))}

          <div className="cl-progress-bar" role="progressbar"
            aria-valuenow={checklist.progress.filter(p => p.checked).length}
            aria-valuemax={checklist.items.length}
          >
            <div
              className="cl-progress-bar__fill"
              style={{
                width: `${(checklist.progress.filter(p => p.checked).length / checklist.items.length) * 100}%`
              }}
            />
            <span className="cl-progress-bar__label">
              {checklist.progress.filter(p => p.checked).length} / {checklist.items.length} steps marked
            </span>
          </div>
        </div>
      )}

      {/* ── Feedback section ── */}
      <div className="cl-feedback-section">
        <h2 className="cl-title cl-title--feedback">
          💬 Your <span>Feedback</span>
        </h2>
        <p className="cl-feedback-hint">
          Optional — share how useful this guidance was. Do not include personal
          information, OTPs, PINs, or payment credentials.
        </p>

        {loadingFb ? (
          <div className="cl-loading">Loading feedback…</div>
        ) : (
          <form
            className="cl-feedback-form"
            onSubmit={handleFeedbackSubmit}
            aria-label="Feedback form"
            noValidate
          >
            <TriStateField
              id="fb-verified"
              label="Did you complete independent verification steps?"
              value={fbDraft.verified_independently}
              onChange={v => setFbDraft(d => ({ ...d, verified_independently: v }))}
            />
            <TriStateField
              id="fb-concerns"
              label="Do you still have concerns about this communication?"
              value={fbDraft.still_has_concerns}
              onChange={v => setFbDraft(d => ({ ...d, still_has_concerns: v }))}
            />
            <TriStateField
              id="fb-useful"
              label="Did you find the guidance result useful?"
              value={fbDraft.result_useful}
              onChange={v => setFbDraft(d => ({ ...d, result_useful: v }))}
            />
            <TriStateField
              id="fb-clear"
              label="Was the guidance result clearly explained?"
              value={fbDraft.result_clear}
              onChange={v => setFbDraft(d => ({ ...d, result_clear: v }))}
            />

            <div className="cl-feedback-field">
              <label htmlFor="fb-note" className="cl-feedback-field__label">
                Short note <span className="cl-optional">(optional, max 500 characters)</span>
              </label>
              <textarea
                id="fb-note"
                className="cl-feedback-field__textarea"
                rows={3}
                maxLength={500}
                placeholder="Any other observations about this case or the tool… (no credentials or personal data)"
                value={fbDraft.note}
                onChange={e => setFbDraft(d => ({ ...d, note: e.target.value }))}
              />
              <span className="cl-char-count">
                {fbDraft.note.length}/500
              </span>
            </div>

            {fbError && (
              <div className="alert alert--error" role="alert">
                <span className="alert__icon">⚠️</span>
                <span>{fbError}</span>
              </div>
            )}

            {fbSaved && (
              <div className="cl-feedback-saved" role="status">
                ✅ Feedback saved.
              </div>
            )}

            <div className="cl-feedback-actions">
              <button
                type="submit"
                className="btn btn--primary"
                disabled={fbSaving}
                aria-busy={fbSaving}
                id="save-feedback-btn"
              >
                {fbSaving
                  ? <><span className="spinner" aria-hidden="true" /> Saving…</>
                  : feedback ? '↻ Update Feedback' : '💾 Save Feedback'}
              </button>
            </div>
          </form>
        )}
      </div>
    </section>
  )
}

// ── Tri-state boolean field (Yes / No / Not answered) ─────────────────────────

function TriStateField({ id, label, value, onChange }) {
  return (
    <div className="cl-tristate" role="group" aria-labelledby={`${id}-label`}>
      <span className="cl-tristate__label" id={`${id}-label`}>{label}</span>
      <div className="cl-tristate__options">
        {[
          { val: true,  text: 'Yes' },
          { val: false, text: 'No'  },
          { val: null,  text: 'Not answered' },
        ].map(({ val, text }) => (
          <button
            key={String(val)}
            type="button"
            className={`cl-tristate__opt ${value === val ? 'cl-tristate__opt--active' : ''}`}
            onClick={() => onChange(val)}
            aria-pressed={value === val}
          >
            {text}
          </button>
        ))}
      </div>
    </div>
  )
}

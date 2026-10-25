import { useState, useEffect } from 'react'
import { requestAnalysis, getAnalysis } from '../../api/analysis'
import './AnalysisPanel.css'

/**
 * AnalysisPanel
 * ──────────────
 * Shows analysis status for one evidence item.
 * Displayed inline within EvidencePanel per evidence row.
 *
 * Honesty guarantees enforced here:
 * - "unavailable" state is labelled clearly — never shown as completed analysis.
 * - is_interpretation findings are flagged with a visual badge.
 * - All reliability_note values from the module are surfaced.
 * - No confidence scores, risk verdicts, or deepfake labels are invented here.
 *
 * - Image evidence offers optional Expected Payment Details; only the values
 *   the user supplies are compared, and the UI shows which comparisons ran.
 * - Analysis execution status is shown separately from payment verification
 *   status, which is never "verified": a screenshot cannot prove settlement.
 * - Image-quality warnings are shown apart from findings; they are not fraud signals.
 *
 * Props:
 *   caseId     (string)
 *   evidenceId (string)
 *   mediaType  (string, optional) — enables the expected-payment form for image/*
 *   onAnalysed (fn, optional)     — called after each analysis request completes
 */
export default function AnalysisPanel({ caseId, evidenceId, mediaType = '', onAnalysed }) {
  const [result,     setResult]     = useState(null)   // AnalysisResultRead | null
  const [loading,    setLoading]    = useState(true)
  const [requesting, setRequesting] = useState(false)
  const [error,      setError]      = useState(null)
  const [expected,   setExpected]   = useState(EMPTY_EXPECTED)
  const isImage = mediaType.toLowerCase().startsWith('image/')

  // Pre-fill the form with the expectations the stored result was computed with.
  useEffect(() => {
    const supplied = result?.payment?.expected_supplied
    if (supplied) {
      setExpected(Object.fromEntries(
        Object.keys(EMPTY_EXPECTED).map(k => [k, supplied[k] ?? '']),
      ))
    }
  }, [result])

  // ── Load existing result on mount ──────────────────────────────────────────
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    getAnalysis(caseId, evidenceId)
      .then(data => { if (!cancelled) { setResult(data); setError(null) } })
      .catch(err => {
        if (cancelled) return
        if (err.status === 404) {
          setResult(null)   // not yet requested — normal state
        } else {
          setError(err.message)
        }
      })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [caseId, evidenceId])

  // ── Request analysis (also used for retry and re-check) ─────────────────────
  async function handleAnalyse(failureMessage = 'Failed to request analysis.') {
    const invalid = isImage ? validateExpected(expected) : null
    if (invalid) {
      setError(invalid)
      return
    }
    setRequesting(true)
    setError(null)
    try {
      await requestAnalysis(caseId, evidenceId, isImage ? expected : null)
      // Re-fetch to get full result with findings
      const fresh = await getAnalysis(caseId, evidenceId)
      setResult(fresh)
    } catch (err) {
      // A failed run is stored server-side; show it alongside the error.
      if (err.status === 500) {
        getAnalysis(caseId, evidenceId).then(setResult).catch(() => {})
      }
      setError(err.message || failureMessage)
    } finally {
      setRequesting(false)
      onAnalysed?.()   // evidence/case statuses changed server-side, success or failure
    }
  }

  const handleRetry = () => handleAnalyse('Retry failed.')

  const expectedForm = isImage && (
    <ExpectedPaymentForm value={expected} onChange={setExpected} disabled={requesting} />
  )

  if (loading) {
    return <div className="ap-loading">Loading analysis…</div>
  }

  // ── Not yet requested ───────────────────────────────────────────────────────
  if (!result) {
    return (
      <div className="ap-not-requested">
        {expectedForm}
        <span className="ap-not-requested__label">No analysis requested</span>
        <button
          className="btn btn--primary ap-analyse-btn"
          onClick={() => handleAnalyse()}
          disabled={requesting}
          aria-busy={requesting}
        >
          {requesting
            ? <><span className="spinner" aria-hidden="true" /> Requesting…</>
            : '🔬 Analyse'}
        </button>
        {error && <p className="ap-error" role="alert">{error}</p>}
      </div>
    )
  }

  // ── Render by status ────────────────────────────────────────────────────────
  return (
    <div className="analysis-panel">
      <StatusHeader result={result} />

      {/* Module unavailable — honest placeholder */}
      {result.status === 'unavailable' && (
        <div className="ap-unavailable">
          <div className="alert alert--info ap-unavailable__alert">
            <span className="alert__icon">🔬</span>
            <span>
              <strong>Analysis module not available.</strong> The AI detection
              component (Person 1) has not been integrated into this deployment.
              No analysis has been performed and <strong>no findings have been
              generated</strong>. This placeholder does not represent a real
              analysis result.
            </span>
          </div>
          {result.limitations && (
            <p className="ap-limitations">{result.limitations}</p>
          )}
        </div>
      )}

      {/* Pending / running */}
      {(result.status === 'pending' || result.status === 'running') && (
        <div className="ap-in-progress">
          <span className="spinner" aria-hidden="true" />
          <span>
            {result.status === 'running' ? 'Analysis in progress…' : 'Queued for analysis…'}
          </span>
        </div>
      )}

      {/* Failed */}
      {result.status === 'failed' && (
        <div className="ap-failed">
          <div className="alert alert--error">
            <span className="alert__icon">⚠️</span>
            <span>
              Analysis failed.
              {result.error_message && <> Error: <em>{result.error_message}</em></>}
            </span>
          </div>
          <button
            className="btn btn--ghost ap-retry-btn"
            onClick={handleRetry}
            disabled={requesting}
            aria-busy={requesting}
          >
            {requesting ? <><span className="spinner" aria-hidden="true" /> Retrying…</> : '↻ Retry'}
          </button>
        </div>
      )}

      {/* Completed with real findings */}
      {result.status === 'completed' && !result.is_stub && (
        <div className="ap-completed">
          <div className="ap-disclaimer">
            <span className="ap-disclaimer__icon">⚠️</span>
            <span>
              Analysis results describe observations about the uploaded file.
              They <strong>do not prove</strong> the communication is fraudulent
              or genuine. Interpretations are marked and require human review.
              SHA-256 does not certify content authenticity.
            </span>
          </div>

          {result.payment && <PaymentSummary payment={result.payment} />}

          <p className="ap-section-label">Findings requiring human review</p>
          {result.findings.length === 0 ? (
            <p className="ap-no-findings">
              The analysis ran but found no specific concern indicators in this file.
              This is <strong>not</strong> evidence that the file, sender or payment is
              genuine: the module cannot verify identities, payments or settlement.
              Check the extracted values and limitations below.
            </p>
          ) : (
            <ul className="ap-findings-list">
              {result.findings.map((f, i) => (
                <FindingCard
                  key={i}
                  finding={f}
                  sharedWith={result.findings.filter(o => o.correlation_group && o.correlation_group === f.correlation_group).length}
                />
              ))}
            </ul>
          )}

          {result.limitations && (
            <LimitationsBox text={result.limitations} structured={Boolean(result.payment)} />
          )}

          {isImage && (
            <details className="ap-recheck">
              <summary>Change expected payment details and re-check</summary>
              {expectedForm}
              <button
                className="btn btn--ghost ap-analyse-btn"
                onClick={() => handleAnalyse('Re-check failed.')}
                disabled={requesting}
                aria-busy={requesting}
              >
                {requesting ? <><span className="spinner" aria-hidden="true" /> Re-checking…</> : '↻ Re-check'}
              </button>
            </details>
          )}
        </div>
      )}

      {error && <p className="ap-error" role="alert">{error}</p>}
    </div>
  )
}

// ── Expected payment details ─────────────────────────────────────────────────

const EMPTY_EXPECTED = { amount: '', currency: '', recipient: '', reference: '' }

// Mirrors the backend rules (app/services/payment_checks.py); the backend is
// authoritative and returns a 422 message for anything this misses.
const AMOUNT_RE = /^(\d+|\d{1,3}(,\d{3})+)(\.\d{1,2})?$/

function validateExpected({ amount }) {
  const a = amount.trim().replace(/ /g, '')
  if (a && !AMOUNT_RE.test(a)) {
    return 'Expected amount must be digits with an optional "." and up to two decimals ' +
      '(e.g. 100 or 1,000.50). Enter the currency in its own field.'
  }
  return null
}

function ExpectedPaymentForm({ value, onChange, disabled }) {
  const set = key => e => onChange({ ...value, [key]: e.target.value })
  return (
    <fieldset className="ap-expected" disabled={disabled}>
      <legend className="ap-expected__legend">Expected payment details (optional)</legend>
      <p className="form-hint">
        Enter only what you know. Each value is compared with the screenshot only when
        you supply it and the screenshot value can be read reliably. Leaving a field
        blank is never treated as a mismatch.
      </p>
      <div className="ap-expected__grid">
        <label className="ap-expected__field">
          <span className="ap-expected__label">Expected amount</span>
          <input className="form-input" inputMode="decimal" autoComplete="off"
                 placeholder="e.g. 25.00" value={value.amount} onChange={set('amount')} maxLength={32} />
        </label>
        <label className="ap-expected__field">
          <span className="ap-expected__label">Currency</span>
          <input className="form-input" list="ap-currency-options" autoComplete="off"
                 placeholder="e.g. EUR or €" value={value.currency} onChange={set('currency')} maxLength={8} />
        </label>
        <label className="ap-expected__field">
          <span className="ap-expected__label">Expected recipient / merchant</span>
          <input className="form-input" autoComplete="off" placeholder="e.g. Amazon Europe"
                 value={value.recipient} onChange={set('recipient')} maxLength={200} />
        </label>
        <label className="ap-expected__field">
          <span className="ap-expected__label">Expected transaction reference</span>
          <input className="form-input" autoComplete="off" placeholder="optional"
                 value={value.reference} onChange={set('reference')} maxLength={64} />
        </label>
      </div>
      <datalist id="ap-currency-options">
        {['EUR', 'USD', 'GBP', 'INR', 'CHF', 'JPY', 'CAD', 'AUD'].map(c => <option key={c} value={c} />)}
      </datalist>
      <p className="form-hint">
        Use a 3-letter currency code, or €, £ or ₹. Shared symbols such as $ are rejected
        because they could mean several currencies.
      </p>
    </fieldset>
  )
}

// ── Payment summary ──────────────────────────────────────────────────────────

const COMPARISON_LABELS = {
  not_supplied:      'Not supplied',
  not_compared:      'Not compared',
  match:             'Matches',
  format_difference: 'Formatting differs only',
  partial_match:     'Partly matches — review',
  mismatch:          'Differs — review',
}

const EXTRACTION_LABELS = {
  fields_extracted:     'Payment fields were read from the screenshot',
  no_fields_recognised: 'Text was read, but no payment fields were recognised',
  no_text:              'No readable text — nothing could be checked',
}

function PaymentSummary({ payment }) {
  const supplied = payment.comparisons.filter(c => c.status !== 'not_supplied')
  const ran = supplied.filter(c => c.ran)
  return (
    <div className="ap-payment">
      <div className="ap-payment__status">
        <span className="ap-payment__status-label">Payment verification</span>
        <span className="ap-verify-badge">Not verified — cannot be determined from a screenshot</span>
        <span className="ap-payment__status-note">{payment.settlement_note}</span>
      </div>
      <p className="ap-payment__extraction">
        <strong>Extraction:</strong> {EXTRACTION_LABELS[payment.extraction_status] ?? payment.extraction_status}
      </p>

      {payment.quality && <QualityBox quality={payment.quality} />}

      <p className="ap-section-label">Expected vs. screenshot</p>
      <p className="ap-payment__coverage">
        {supplied.length === 0
          ? 'No expected payment details were supplied, so no comparison was made.'
          : `Supplied: ${supplied.map(c => c.field).join(', ')} · Comparisons run: ${ran.length} of ${supplied.length}`}
      </p>
      <table className="ap-compare-table">
        <thead>
          <tr><th scope="col">Field</th><th scope="col">You expected</th>
            <th scope="col">Screenshot shows (raw OCR)</th><th scope="col">Result</th></tr>
        </thead>
        <tbody>
          {payment.comparisons.map(c => (
            <tr key={c.field} className={`ap-compare-row ap-compare-row--${c.status}`}>
              <th scope="row">{c.field}</th>
              <td className="ap-extracted-value">{c.expected ?? '—'}</td>
              <td className="ap-extracted-value">
                {c.extracted ?? '—'}
                {c.extracted_confidence && (
                  <span className={`ap-extracted-conf ap-extracted-conf--${c.extracted_confidence}`}>
                    {' '}({c.extracted_confidence} OCR confidence)
                  </span>
                )}
              </td>
              <td>
                <span className={`ap-compare-badge ap-compare-badge--${c.status}`} title={c.detail}>
                  {COMPARISON_LABELS[c.status] ?? c.status}
                </span>
                {c.status !== 'not_supplied' && <span className="ap-compare-detail">{c.detail}</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {payment.extracted_fields.length > 0 && (
        <>
          <p className="ap-section-label">All extracted fields (raw OCR text)</p>
          <table className="ap-extracted-table">
            <tbody>
              {payment.extracted_fields.map(e => (
                <tr key={e.field_name}>
                  <th scope="row">{e.field_name.replace(/_/g, ' ')}</th>
                  <td className="ap-extracted-value">{e.extracted_value ?? '—'}</td>
                  <td className={`ap-extracted-conf ap-extracted-conf--${e.confidence}`}>
                    {e.confidence} confidence
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="form-hint">
            Confidence is Tesseract's self-report about its own reading. It is not a
            probability that the value is correct.
          </p>
        </>
      )}
    </div>
  )
}

const QUALITY_LABELS = {
  no_issues_detected: 'No quality issues detected',
  limited:            'Limited — some values may be missing or misread',
  unreadable:         'Unreadable — the screenshot could not be analysed',
}

function QualityBox({ quality }) {
  const m = quality.metrics || {}
  return (
    <div className={`ap-quality ap-quality--${quality.assessment}`}>
      <p className="ap-section-label">Image quality (not a fraud indicator)</p>
      <p className="ap-quality__assessment">{QUALITY_LABELS[quality.assessment] ?? quality.assessment}</p>
      {quality.warnings.length > 0 && (
        <ul className="ap-quality__list">
          {quality.warnings.map(w => (
            <li key={w.code} className={`ap-quality__item ap-quality__item--${w.severity}`}>
              <span className="ap-quality__severity">{w.severity}</span> {w.message}
            </li>
          ))}
        </ul>
      )}
      <p className="ap-quality__metrics">
        {[
          m.width_px != null && `${m.width_px}×${m.height_px}px`,
          m.median_text_height_px != null && `text ≈${m.median_text_height_px}px`,
          m.edge_sharpness != null && `sharpness ${m.edge_sharpness}`,
          m.ocr_mean_word_confidence != null && `mean OCR confidence ${m.ocr_mean_word_confidence}/100`,
        ].filter(Boolean).join(' · ')}
      </p>
      {quality.note && <p className="form-hint">{quality.note}</p>}
    </div>
  )
}

// ── Sub-components ────────────────────────────────────────────────────────────

function StatusHeader({ result }) {
  const cfg = STATUS_CONFIG[result.status] ?? STATUS_CONFIG.pending
  return (
    <div className="ap-status-header">
      <span className="ap-payment__status-label">Analysis execution</span>
      <span className={`ap-status-badge ap-status-badge--${result.status}`}>
        {cfg.icon} {cfg.label}
      </span>
      {result.module_name && result.module_name !== 'unavailable' && (
        <span className="ap-module-id">
          {result.module_name} {result.module_version}
        </span>
      )}
      {result.requested_at && (
        <span className="ap-timestamp">
          Requested {new Date(result.requested_at).toLocaleString()}
        </span>
      )}
    </div>
  )
}

function FindingCard({ finding, sharedWith = 0 }) {
  return (
    <li className={`ap-finding ${finding.is_interpretation ? 'ap-finding--interp' : ''}`}>
      <div className="ap-finding__header">
        <span className="ap-finding__label">{finding.label}</span>
        {finding.is_interpretation && (
          <span className="ap-interp-badge" title="This is an interpretation, not a direct measurement">
            Interpretation
          </span>
        )}
        {sharedWith > 1 && (
          <span
            className="ap-corr-badge"
            title="Derived from the same reading as other findings on this file — not an independent confirmation"
          >
            Correlated · same OCR reading as {sharedWith - 1} other
          </span>
        )}
      </div>
      <p className="ap-finding__detail">{finding.detail}</p>
      <div className="ap-finding__meta">
        <span className="ap-finding__source">Source: {finding.evidence_source}</span>
        {finding.reliability_note && (
          <span className="ap-finding__reliability">⚠ {finding.reliability_note}</span>
        )}
      </div>
    </li>
  )
}

// Lines written by the backend adapter as
// "[payment] Extracted <field>: '<value>' (OCR confidence: <level>)".
const EXTRACTED_FIELD_RE = /^\[payment\] Extracted (\w+): (.*) \(OCR confidence: (\w+)\)$/

// When the structured payment summary is shown, these lines repeat it.
const STRUCTURED_DUPLICATE_RE = /^\[(quality\]|payment\] Expected )/

function LimitationsBox({ text, structured = false }) {
  const extracted = []
  const notes = []
  for (const line of text.split('\n').map(l => l.trim()).filter(Boolean)) {
    const m = line.match(EXTRACTED_FIELD_RE)
    if (m) extracted.push({ field: m[1], value: m[2], confidence: m[3] })
    else if (!(structured && STRUCTURED_DUPLICATE_RE.test(line))) notes.push(line)
  }
  return (
    <div className="ap-limitations-box">
      {!structured && extracted.length > 0 && (
        <>
          <p className="ap-limitations-label">Extracted payment fields (raw OCR text)</p>
          <table className="ap-extracted-table">
            <tbody>
              {extracted.map(e => (
                <tr key={e.field}>
                  <th scope="row">{e.field.replace(/_/g, ' ')}</th>
                  <td className="ap-extracted-value">{e.value}</td>
                  <td className={`ap-extracted-conf ap-extracted-conf--${e.confidence}`}>
                    {e.confidence} confidence
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      <p className="ap-limitations-label">Module notes &amp; limitations</p>
      <ul className="ap-limitations-list">
        {notes.map((n, i) => <li key={i}>{n}</li>)}
      </ul>
    </div>
  )
}

const STATUS_CONFIG = {
  unavailable: { icon: '🔬', label: 'Analysis Unavailable' },
  pending:     { icon: '⏳', label: 'Pending' },
  running:     { icon: '⚙️', label: 'Running' },
  completed:   { icon: '✅', label: 'Analysis Completed' },
  failed:      { icon: '❌', label: 'Failed' },
}

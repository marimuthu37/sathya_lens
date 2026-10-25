/**
 * src/api/analysis.js
 * ─────────────────────
 * API client methods for Segment 5: analysis request and retrieval.
 */

// Same variable as api.js (documented in .env.example); VITE_API_URL kept as a fallback.
const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? import.meta.env.VITE_API_URL ?? ''

async function apiFetch(path, options = {}) {
  let resp
  try {
    resp = await fetch(`${BASE_URL}${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    })
  } catch {
    const err = new Error('Unable to reach the SatyaLens backend. Is it running?')
    err.status = 0
    err.isNetworkError = true
    throw err
  }
  if (resp.status === 204) return null
  const data = await resp.json().catch(() => null)
  if (!resp.ok) {
    // FastAPI request-validation errors arrive as detail: [{ msg, loc }, ...]
    const validation = Array.isArray(data?.detail)
      ? data.detail.map(d => String(d.msg ?? '').replace(/^Value error, /, '')).filter(Boolean).join(' ')
      : null
    const message =
      data?.detail?.message ??
      data?.error?.message ??
      (typeof data?.detail === 'string' ? data.detail : null) ??
      (validation || null) ??
      `HTTP ${resp.status}`
    const err = new Error(message)
    err.status = resp.status
    err.data = data
    throw err
  }
  return data
}

/**
 * POST /api/cases/{caseId}/evidence/{evidenceId}/analyse
 * Requests analysis. Returns AnalysisRequestResponse.
 *
 * expectedPayment (optional, image evidence only):
 *   { amount?, currency?, recipient?, reference? } — strings; blanks are ignored.
 *   Omitted or all-blank → no body is sent (original behaviour).
 */
export async function requestAnalysis(caseId, evidenceId, expectedPayment = null) {
  const supplied = expectedPayment
    ? Object.fromEntries(
        Object.entries(expectedPayment)
          .map(([k, v]) => [k, typeof v === 'string' ? v.trim() : v])
          .filter(([, v]) => v),
      )
    : {}
  const options = { method: 'POST' }
  if (Object.keys(supplied).length > 0) {
    options.body = JSON.stringify({ expected_payment: supplied })
  }
  return apiFetch(
    `/api/cases/${encodeURIComponent(caseId)}/evidence/${encodeURIComponent(evidenceId)}/analyse`,
    options,
  )
}

/**
 * GET /api/cases/{caseId}/evidence/{evidenceId}/analysis
 * Retrieves current analysis result. Returns AnalysisResultRead or throws 404.
 */
export async function getAnalysis(caseId, evidenceId) {
  return apiFetch(
    `/api/cases/${encodeURIComponent(caseId)}/evidence/${encodeURIComponent(evidenceId)}/analysis`,
  )
}

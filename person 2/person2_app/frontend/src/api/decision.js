/**
 * src/api/decision.js
 * ────────────────────
 * API client methods for Segment 6: decision engine.
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
    const err = new Error('Unable to reach the SatyaLens backend.')
    err.status = 0
    throw err
  }
  if (resp.status === 204) return null
  const data = await resp.json().catch(() => null)
  if (!resp.ok) {
    const message =
      data?.detail?.message ??
      data?.error?.message ??
      (typeof data?.detail === 'string' ? data.detail : null) ??
      `HTTP ${resp.status}`
    const err = new Error(message)
    err.status = resp.status
    err.data = data
    throw err
  }
  return data
}

/**
 * POST /api/cases/{caseId}/decision
 * Computes (or re-computes) the guidance decision.
 */
export async function computeDecision(caseId) {
  return apiFetch(`/api/cases/${encodeURIComponent(caseId)}/decision`, { method: 'POST' })
}

/**
 * GET /api/cases/{caseId}/decision
 * Retrieves the most-recently computed decision. Throws 404 if none exists yet.
 */
export async function getDecision(caseId) {
  return apiFetch(`/api/cases/${encodeURIComponent(caseId)}/decision`)
}

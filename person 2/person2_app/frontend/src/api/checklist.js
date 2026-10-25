/**
 * src/api/checklist.js
 * ─────────────────────
 * API client for Segment 7: verification checklist and user feedback.
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

/** GET /api/cases/{caseId}/checklist */
export async function getChecklist(caseId) {
  return apiFetch(`/api/cases/${encodeURIComponent(caseId)}/checklist`)
}

/** PATCH /api/cases/{caseId}/checklist/{itemId} */
export async function updateChecklistItem(caseId, itemId, checked) {
  return apiFetch(
    `/api/cases/${encodeURIComponent(caseId)}/checklist/${encodeURIComponent(itemId)}`,
    { method: 'PATCH', body: JSON.stringify({ checked }) },
  )
}

/** GET /api/cases/{caseId}/feedback */
export async function getFeedback(caseId) {
  return apiFetch(`/api/cases/${encodeURIComponent(caseId)}/feedback`)
}

/** PUT /api/cases/{caseId}/feedback */
export async function submitFeedback(caseId, payload) {
  return apiFetch(`/api/cases/${encodeURIComponent(caseId)}/feedback`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

/** DELETE /api/cases/{caseId}/feedback */
export async function deleteFeedback(caseId) {
  return apiFetch(`/api/cases/${encodeURIComponent(caseId)}/feedback`, { method: 'DELETE' })
}

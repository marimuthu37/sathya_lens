/**
 * api/api.js
 * ──────────
 * Centralised API client for the SatyaLens backend.
 *
 * Base URL is read from VITE_API_BASE_URL.
 * In development the Vite proxy forwards /api → http://127.0.0.1:8000,
 * so the default is an empty string (same origin).
 */

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''

// ── Internal fetch wrapper ────────────────────────────────────────────────────

/**
 * Wraps fetch: sets JSON headers, parses response body, throws on non-2xx.
 * For 204 No Content the body is returned as null.
 *
 * @param {string}      path
 * @param {RequestInit} [options]
 * @returns {Promise<any>}
 */
async function apiFetch(path, options = {}) {
  const url = `${BASE_URL}${path}`

  let response
  try {
    response = await fetch(url, {
      headers: { 'Content-Type': 'application/json', ...options.headers },
      ...options,
    })
  } catch {
    // Network failure (backend unreachable)
    const err = new Error('Unable to reach the SatyaLens backend. Is it running?')
    err.status = 0
    err.isNetworkError = true
    throw err
  }

  // 204 No Content — no body to parse
  if (response.status === 204) return null

  let data
  try {
    data = await response.json()
  } catch {
    data = null
  }

  if (!response.ok) {
    // Prefer our own error envelope, then FastAPI default, then generic
    const message =
      data?.error?.message ??
      data?.detail?.message ??
      (typeof data?.detail === 'string' ? data.detail : null) ??
      `HTTP ${response.status}`

    const err = new Error(message)
    err.status = response.status
    err.data = data
    throw err
  }

  return data
}

// ── Health ────────────────────────────────────────────────────────────────────

/**
 * GET /api/health
 * @returns {Promise<{status:string, version:string, environment:string}>}
 */
export async function fetchHealth() {
  return apiFetch('/api/health')
}

// ── Cases ─────────────────────────────────────────────────────────────────────

/**
 * POST /api/cases
 * @param {{ description?: string }} body
 * @returns {Promise<CaseRead>}
 */
export async function createCase(body) {
  return apiFetch('/api/cases', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

/**
 * GET /api/cases
 * @returns {Promise<{ total: number, items: CaseList[] }>}
 */
export async function listCases() {
  return apiFetch('/api/cases')
}

/**
 * GET /api/cases/{case_id}
 * @param {string} caseId
 * @returns {Promise<CaseRead>}
 */
export async function getCase(caseId) {
  return apiFetch(`/api/cases/${encodeURIComponent(caseId)}`)
}

/**
 * DELETE /api/cases/{case_id}
 * Returns null on 204 success.
 * @param {string} caseId
 * @returns {Promise<null>}
 */
export async function deleteCase(caseId) {
  return apiFetch(`/api/cases/${encodeURIComponent(caseId)}`, {
    method: 'DELETE',
  })
}

// ── Evidence & Consent ────────────────────────────────────────────────────────

/**
 * POST /api/cases/{case_id}/evidence/consent
 * Records explicit user consent for this case.
 * @param {string} caseId
 * @returns {Promise<ConsentResponse>}
 */
export async function recordConsent(caseId) {
  // FIX (Issue 2): send explicit acknowledgement body as required by backend.
  // The backend rejects acknowledged=false or a missing body.
  return apiFetch(`/api/cases/${encodeURIComponent(caseId)}/evidence/consent`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ acknowledged: true }),
  })
}

/**
 * POST /api/cases/{case_id}/evidence
 * Uploads a single evidence file (multipart).
 * @param {string}   caseId
 * @param {File}     file       — browser File object
 * @param {function} [onProgress] — callback(percent: number)
 * @returns {Promise<EvidenceRead>}
 */
export async function uploadEvidence(caseId, file, onProgress) {
  const url = `${BASE_URL}/api/cases/${encodeURIComponent(caseId)}/evidence`
  const formData = new FormData()
  formData.append('file', file)

  // Use XHR so we can report progress; fetch does not expose upload progress yet.
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', url)

    if (onProgress) {
      xhr.upload.addEventListener('progress', (e) => {
        if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100))
      })
    }

    xhr.onload = () => {
      let data
      try { data = JSON.parse(xhr.responseText) } catch { data = null }
      if (xhr.status === 201) {
        resolve(data)
      } else {
        const message =
          data?.detail?.message ??
          data?.error?.message ??
          (typeof data?.detail === 'string' ? data.detail : null) ??
          `HTTP ${xhr.status}`
        const err = new Error(message)
        err.status = xhr.status
        err.data = data
        reject(err)
      }
    }

    xhr.onerror = () => {
      const err = new Error('Unable to reach the SatyaLens backend. Is it running?')
      err.status = 0
      err.isNetworkError = true
      reject(err)
    }

    xhr.send(formData)
  })
}

/**
 * DELETE /api/cases/{case_id}/evidence/{evidence_id}
 * Returns null on 204 success.
 * @param {string} caseId
 * @param {string} evidenceId
 * @returns {Promise<null>}
 */
export async function deleteEvidence(caseId, evidenceId) {
  return apiFetch(
    `/api/cases/${encodeURIComponent(caseId)}/evidence/${encodeURIComponent(evidenceId)}`,
    { method: 'DELETE' },
  )
}

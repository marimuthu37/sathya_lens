/**
 * utils/formatters.js
 * ────────────────────
 * Formatting helpers used across case-management pages.
 */

/**
 * Format an ISO timestamp to a readable local date+time string.
 * e.g. "24 Oct 2026, 11:20 AM"
 */
export function formatDateTime(iso) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString('en-IN', {
      day: '2-digit', month: 'short', year: 'numeric',
      hour: '2-digit', minute: '2-digit', hour12: true,
    })
  } catch {
    return iso
  }
}

/**
 * Format an ISO timestamp to a short date.
 * e.g. "24 Oct 2026"
 */
export function formatDate(iso) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleDateString('en-IN', {
      day: '2-digit', month: 'short', year: 'numeric',
    })
  } catch {
    return iso
  }
}

/**
 * Shorten a UUID for display: "abc12345-…" → "abc12345"
 */
export function shortId(id) {
  if (!id) return '—'
  return id.slice(0, 8)
}

/**
 * Map a status string to a pill CSS modifier.
 */
export function statusPillClass(status) {
  const s = (status ?? '').toLowerCase()
  if (s === 'open')     return 'status-pill--open'
  if (s === 'processing') return 'status-pill--processing'
  if (s === 'complete') return 'status-pill--complete'
  if (s === 'archived') return 'status-pill--archived'
  return 'status-pill--pending'
}

/**
 * Human-readable label for a status enum value.
 */
export function statusLabel(status) {
  const map = {
    open: 'Open',
    processing: 'Processing',
    complete: 'Complete',
    archived: 'Archived',
    pending: 'Pending',
    not_started: 'Not Started',
    // Case.analysis_status (see backend app/services/case_status.py)
    queued: 'Partly Analysed',
    running: 'Running',
    done: 'Analysed',
    failed: 'Failed',
    processed: 'Processed',
    insufficient_evidence: 'Insufficient Evidence',
    low_concern: 'Low Concern',
    verify_first: 'Verify First',
    high_risk: 'High Risk',
  }
  return map[(status ?? '').toLowerCase()] ?? status ?? '—'
}

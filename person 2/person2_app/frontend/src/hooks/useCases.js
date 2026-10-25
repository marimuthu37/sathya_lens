/**
 * hooks/useCases.js
 * ─────────────────
 * Reusable data hooks for case-management operations.
 */
import { useState, useEffect, useCallback } from 'react'
import { listCases, getCase } from '../api/api'

/**
 * Fetches and manages the full list of cases.
 * Returns { cases, total, loading, error, reload }
 */
export function useCaseList() {
  const [cases, setCases]   = useState([])
  const [total, setTotal]   = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError]   = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await listCases()
      setCases(data.items)
      setTotal(data.total)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  return { cases, total, loading, error, reload: load }
}

/**
 * Fetches a single case by ID.
 * Returns { caseData, loading, error, reload }
 */
export function useCaseDetail(caseId) {
  const [caseData, setCaseData] = useState(null)
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState(null)

  // reload({ silent: true }) refreshes without the loading skeleton, so child
  // panels (analysis, decision) keep their state while the case status updates.
  const load = useCallback(async (opts) => {
    if (!caseId) return
    if (opts?.silent !== true) setLoading(true)
    setError(null)
    try {
      const data = await getCase(caseId)
      setCaseData(data)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [caseId])

  useEffect(() => { load() }, [load])

  return { caseData, loading, error, reload: load }
}

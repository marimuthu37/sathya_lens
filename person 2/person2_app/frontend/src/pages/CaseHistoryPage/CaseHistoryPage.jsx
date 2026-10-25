import { useCaseList } from '../../hooks/useCases'
import { formatDate, shortId, statusPillClass, statusLabel } from '../../utils/formatters'
import './CaseHistoryPage.css'

/**
 * CaseHistoryPage
 * ────────────────
 * GET /api/cases — displays all cases in a sortable table.
 *
 * Props:
 *   onNavigate (fn)               — navigate to a named page
 *   onSelectCase (fn: id → void)  — navigate to CaseDetailPage
 */
export default function CaseHistoryPage({ onNavigate, onSelectCase }) {
  const { cases, total, loading, error, reload } = useCaseList()

  return (
    <main className="page-content" id="case-history-page">
      <div className="container">

        {/* Header row */}
        <div className="history-header">
          <div>
            <h1 className="section-heading">
              Case <span>History</span>
            </h1>
            <p className="section-sub">
              {loading
                ? 'Loading your cases…'
                : error
                  ? 'Could not load cases.'
                  : `${total} case${total !== 1 ? 's' : ''} on record`}
            </p>
          </div>
          <div className="history-header__actions">
            <button
              className="btn btn--ghost"
              onClick={reload}
              disabled={loading}
              aria-label="Refresh case list"
              title="Refresh"
            >
              {loading ? <span className="spinner" aria-hidden="true" /> : '↻'}&nbsp;Refresh
            </button>
            <button
              id="open-new-case"
              className="btn btn--primary"
              onClick={() => onNavigate('new-case')}
            >
              + Open New Case
            </button>
          </div>
        </div>

        {/* Error state */}
        {error && !loading && (
          <div className="alert alert--error" role="alert" style={{ marginBottom: 'var(--space-5)' }}>
            <span className="alert__icon">⚠️</span>
            <span>{error}&nbsp;—&nbsp;
              <button className="alert-retry-btn" onClick={reload}>Retry</button>
            </span>
          </div>
        )}

        {/* Loading skeleton */}
        {loading && (
          <div className="history-loading" aria-busy="true" aria-label="Loading cases">
            {[1, 2, 3].map(i => <div key={i} className="history-skeleton" />)}
          </div>
        )}

        {/* Empty state */}
        {!loading && !error && cases.length === 0 && (
          <div className="card">
            <div className="empty-state">
              <div className="empty-state__icon">🗂️</div>
              <p className="empty-state__title">No cases yet</p>
              <p className="empty-state__body">
                You haven't opened any verification cases. Start by reporting a
                suspicious communication.
              </p>
              <button
                className="btn btn--primary"
                onClick={() => onNavigate('new-case')}
              >
                + Open First Case
              </button>
            </div>
          </div>
        )}

        {/* Case table */}
        {!loading && cases.length > 0 && (
          <div className="card history-card">
            <div className="history-table-wrap">
              <table className="case-table" aria-label="Case history">
                <thead>
                  <tr>
                    <th scope="col">Case ID</th>
                    <th scope="col">Description</th>
                    <th scope="col">Status</th>
                    <th scope="col">Analysis</th>
                    <th scope="col">Opened</th>
                    <th scope="col">Evidence</th>
                  </tr>
                </thead>
                <tbody>
                  {cases.map(c => (
                    <tr
                      key={c.id}
                      onClick={() => onSelectCase(c.id)}
                      tabIndex={0}
                      role="button"
                      aria-label={`View case ${shortId(c.id)}`}
                      onKeyDown={e => e.key === 'Enter' && onSelectCase(c.id)}
                    >
                      <td>
                        <span className="case-table__id" title={c.id}>
                          #{shortId(c.id)}
                        </span>
                      </td>
                      <td>
                        <span className="case-table__desc" title={c.description || ''}>
                          {c.description || <em style={{ color: 'var(--color-text-muted)' }}>No description</em>}
                        </span>
                      </td>
                      <td>
                        <span className={`status-pill ${statusPillClass(c.status)}`}>
                          {statusLabel(c.status)}
                        </span>
                      </td>
                      <td>
                        <span style={{ color: 'var(--color-text-secondary)', fontSize: '0.8rem' }}>
                          {statusLabel(c.analysis_status)}
                        </span>
                      </td>
                      <td style={{ whiteSpace: 'nowrap', color: 'var(--color-text-secondary)', fontSize: '0.8rem' }}>
                        {formatDate(c.created_at)}
                      </td>
                      <td style={{ color: 'var(--color-text-secondary)', fontSize: '0.8rem', textAlign: 'center' }}>
                        {c.evidence_count}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

      </div>
    </main>
  )
}

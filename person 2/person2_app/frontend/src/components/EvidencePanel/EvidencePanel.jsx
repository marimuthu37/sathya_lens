import { useState, useRef } from 'react'
import { uploadEvidence, deleteEvidence } from '../../api/api'
import { formatDateTime, shortId, statusPillClass, statusLabel } from '../../utils/formatters'
import AnalysisPanel from '../AnalysisPanel/AnalysisPanel'
import './EvidencePanel.css'

// Allowed formats as shown to user (must match backend config)
const ALLOWED_FORMATS = [
  'JPEG', 'PNG', 'GIF', 'WebP',
  'PDF',
  'MP3', 'WAV', 'OGG', 'M4A (audio)', 'WebM (audio)',
  'MP4 (video)', 'WebM (video)',
  'TXT',
]
const MAX_MB = 10

/**
 * EvidencePanel
 * ──────────────
 * Upload interface + evidence list for a case that has consent_status === 'granted'.
 *
 * Props:
 *   caseId         (string)
 *   evidenceItems  (EvidenceRead[])
 *   onChanged      (fn)  — called after upload or delete so parent can reload
 *   onAnalysed     (fn)  — called after an analysis request finishes (refreshes statuses)
 */
export default function EvidencePanel({ caseId, evidenceItems, onChanged, onAnalysed }) {
  const [selectedFile, setSelectedFile] = useState(null)
  const [uploading,    setUploading]    = useState(false)
  const [progress,     setProgress]     = useState(0)
  const [uploadError,  setUploadError]  = useState(null)
  const [uploadSuccess,setUploadSuccess]= useState(null)

  const [confirmDelete, setConfirmDelete] = useState(null)  // evidence object | null
  const [deleting,      setDeleting]      = useState(false)
  const [deleteError,   setDeleteError]   = useState(null)

  const fileInputRef = useRef(null)

  // ── Upload flow ─────────────────────────────────────────────────────────────
  function handleFileChange(e) {
    const file = e.target.files?.[0] ?? null
    setSelectedFile(file)
    setUploadError(null)
    setUploadSuccess(null)
  }

  async function handleUpload(e) {
    e.preventDefault()
    if (!selectedFile || uploading) return
    setUploading(true)
    setProgress(0)
    setUploadError(null)
    setUploadSuccess(null)
    try {
      const ev = await uploadEvidence(caseId, selectedFile, pct => setProgress(pct))
      setUploadSuccess(`Uploaded "${ev.original_filename}" (SHA-256: ${ev.sha256_hash.slice(0, 16)}…)`)
      setSelectedFile(null)
      setProgress(0)
      if (fileInputRef.current) fileInputRef.current.value = ''
      onChanged?.()
    } catch (err) {
      setUploadError(err.message || 'Upload failed. Please try again.')
    } finally {
      setUploading(false)
    }
  }

  // ── Delete flow ─────────────────────────────────────────────────────────────
  async function handleDelete() {
    if (!confirmDelete || deleting) return
    setDeleting(true)
    setDeleteError(null)
    try {
      await deleteEvidence(caseId, confirmDelete.id)
      setConfirmDelete(null)
      onChanged?.()
    } catch (err) {
      setDeleteError(err.status === 404
        ? 'This evidence item no longer exists.'
        : err.message || 'Delete failed. Please try again.')
      setDeleting(false)
    }
  }

  return (
    <div className="evidence-panel">

      {/* ── Upload form ── */}
      <div className="evidence-upload-card card">
        <h3 className="evidence-section-title">📎 Upload Evidence</h3>

        <div className="alert alert--info evidence-hash-note">
          <span className="alert__icon">ℹ️</span>
          <span>
            A <strong>SHA-256 hash</strong> of each upload is computed server-side and recorded.
            This can help detect whether the file's bytes changed after upload —
            it does <strong>not</strong> prove the file is genuine or unmodified before upload.
            Do <strong>not</strong> upload OTPs, PINs, passwords, or account credentials.
          </span>
        </div>

        <form onSubmit={handleUpload} className="evidence-upload-form">
          <div className="form-group">
            <label htmlFor="evidence-file" className="form-label">
              Select File
            </label>
            <input
              id="evidence-file"
              ref={fileInputRef}
              type="file"
              className="file-input"
              onChange={handleFileChange}
              disabled={uploading}
              aria-describedby="file-hint"
              accept="image/jpeg,image/png,image/gif,image/webp,application/pdf,audio/mpeg,audio/wav,audio/ogg,audio/mp4,audio/webm,video/mp4,video/webm,text/plain"
            />
            <p id="file-hint" className="form-hint">
              Allowed: {ALLOWED_FORMATS.join(', ')} — Max {MAX_MB} MiB.
            </p>
          </div>

          {selectedFile && (
            <div className="selected-file-info">
              <span className="selected-file-info__icon">📄</span>
              <div>
                <p className="selected-file-info__name">{selectedFile.name}</p>
                <p className="selected-file-info__meta">
                  {selectedFile.type || 'unknown type'} · {(selectedFile.size / 1024).toFixed(1)} KB
                </p>
              </div>
            </div>
          )}

          {uploading && (
            <div className="upload-progress" role="progressbar" aria-valuenow={progress} aria-valuemin={0} aria-valuemax={100}>
              <div className="upload-progress__bar" style={{ width: `${progress}%` }} />
              <span className="upload-progress__label">{progress}%</span>
            </div>
          )}

          {uploadError && (
            <div className="alert alert--error" role="alert">
              <span className="alert__icon">⚠️</span>
              <span>{uploadError}</span>
            </div>
          )}
          {uploadSuccess && (
            <div className="alert alert--success" role="status" aria-live="polite">
              <span className="alert__icon">✅</span>
              <span>{uploadSuccess}</span>
            </div>
          )}

          <div className="evidence-upload-actions">
            <button
              id="upload-evidence-btn"
              type="submit"
              className="btn btn--primary"
              disabled={!selectedFile || uploading}
              aria-busy={uploading}
            >
              {uploading
                ? <><span className="spinner" aria-hidden="true" /> Uploading…</>
                : '⬆ Upload Evidence'}
            </button>
          </div>
        </form>
      </div>

      {/* ── Evidence list ── */}
      <div className="evidence-list-card card">
        <h3 className="evidence-section-title">
          📂 Uploaded Evidence ({evidenceItems.length})
        </h3>

        {evidenceItems.length === 0 ? (
          <div className="empty-state" style={{ padding: 'var(--space-5) 0' }}>
            <div className="empty-state__icon">📁</div>
            <p className="empty-state__title">No evidence uploaded yet</p>
            <p className="empty-state__body">
              Use the form above to attach a file. No evidence has been fabricated
              or pre-loaded.
            </p>
          </div>
        ) : (
          <ul className="ev-list">
            {evidenceItems.map(ev => (
              <li key={ev.id} className="ev-item">
                <div className="ev-item__header">
                  <div className="ev-item__name" title={ev.original_filename}>
                    {ev.original_filename}
                  </div>
                  <span className={`status-pill ${statusPillClass(ev.processing_status)}`}>
                    {statusLabel(ev.processing_status)}
                  </span>
                  <button
                    className="btn btn--danger ev-item__delete"
                    onClick={() => { setConfirmDelete(ev); setDeleteError(null) }}
                    aria-label={`Delete evidence ${ev.original_filename}`}
                    title="Delete this evidence item"
                  >
                    🗑
                  </button>
                </div>

                <div className="ev-item__meta">
                  <MetaChip label="Type"   value={ev.media_type} />
                  <MetaChip label="Size"   value={`${(ev.file_size_bytes / 1024).toFixed(1)} KB`} />
                  <MetaChip label="Uploaded" value={formatDateTime(ev.created_at)} />
                </div>

                <div className="ev-item__hash">
                  <span className="ev-item__hash-label">SHA-256</span>
                  <code className="ev-item__hash-value" title={ev.sha256_hash}>
                    {ev.sha256_hash}
                  </code>
                </div>
                <p className="ev-item__hash-note">
                  Hash records the exact bytes received. It does not certify that the
                  file was unaltered before upload or that it is genuine evidence.
                </p>

                {/* ── Analysis section per evidence item ── */}
                <AnalysisPanel caseId={ev.case_id} evidenceId={ev.id} mediaType={ev.media_type}
                               onAnalysed={onAnalysed} />

              </li>
            ))}
          </ul>
        )}
      </div>

      {/* ── Delete Evidence Confirmation Modal ── */}
      {confirmDelete && (
        <div
          className="modal-overlay"
          role="dialog"
          aria-modal="true"
          aria-labelledby="ev-delete-modal-title"
          onClick={e => { if (e.target === e.currentTarget && !deleting) setConfirmDelete(null) }}
        >
          <div className="modal">
            <h2 id="ev-delete-modal-title" className="modal__title">Delete Evidence?</h2>
            <p className="modal__body">
              Permanently delete <strong>{confirmDelete.original_filename}</strong>?
              Both the stored file and its metadata will be removed. This cannot be undone.
            </p>
            {deleteError && (
              <div className="alert alert--error" role="alert" style={{ marginBottom: 'var(--space-4)' }}>
                <span className="alert__icon">⚠️</span>
                <span>{deleteError}</span>
              </div>
            )}
            <div className="modal__actions">
              <button
                className="btn btn--ghost"
                onClick={() => { setConfirmDelete(null); setDeleteError(null) }}
                disabled={deleting}
                autoFocus
              >
                Cancel
              </button>
              <button
                id="confirm-ev-delete-btn"
                className="btn btn--danger"
                onClick={handleDelete}
                disabled={deleting}
                aria-busy={deleting}
              >
                {deleting
                  ? <><span className="spinner" aria-hidden="true" /> Deleting…</>
                  : '🗑 Yes, Delete'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function MetaChip({ label, value }) {
  return (
    <span className="ev-meta-chip">
      <span className="ev-meta-chip__label">{label}</span>
      <span className="ev-meta-chip__value">{value}</span>
    </span>
  )
}

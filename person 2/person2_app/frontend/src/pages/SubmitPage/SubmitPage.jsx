import './SubmitPage.css'

const UPCOMING_STEPS = [
  {
    label: 'Consent & case creation',
    desc:  'User accepts data handling terms; a unique case ID is generated.',
    segment: 2,
  },
  {
    label: 'File upload',
    desc:  'Upload audio recordings and/or payment screenshots. Files are hashed on arrival.',
    segment: 2,
  },
  {
    label: 'Analysis pipeline',
    desc:  'Audio deepfake detection, OCR field extraction, and reconciliation run in sequence.',
    segment: '2–3 (Person 1)',
  },
  {
    label: 'Decision engine verdict',
    desc:  'Weighted signals produce a Low Concern, Verify First, or High Risk verdict with full evidence.',
    segment: 3,
  },
  {
    label: 'Report export',
    desc:  'Download a SHA-256-anchored PDF/JSON report for the case.',
    segment: 4,
  },
]

/**
 * SubmitPage — Placeholder for the evidence-submission workflow.
 *
 * The actual consent + file-upload form is implemented in Segment 2.
 * This page documents the planned steps so Person 1 understands the hand-off points.
 */
export default function SubmitPage() {
  return (
    <main className="page-content" id="main-content">
      <div className="container submit-page">

        {/* ── Header ── */}
        <header className="submit-page__header">
          <div className="segment-tag">📍 Segment 2 — evidence submission</div>
          <h1 className="section-heading">
            Submit <span>Evidence</span>
          </h1>
          <p className="section-sub">
            This page will host the consent and file-upload workflow.
            Implementation begins in Segment 2 — the placeholder below shows the intended UX flow.
          </p>
        </header>

        {/* ── Placeholder upload area ── */}
        <div
          id="upload-placeholder"
          className="submit-placeholder"
          role="region"
          aria-label="Upload area — not yet active"
        >
          <div className="submit-placeholder__icon" aria-hidden="true">📂</div>
          <div className="submit-placeholder__title">
            Evidence upload — coming in Segment 2
          </div>
          <p className="submit-placeholder__body">
            You will be able to drag-and-drop or browse for audio files (.mp3, .wav)
            and payment screenshots (.png, .jpg, .pdf) here.
            All uploads are SHA-256 hashed immediately on the backend and never stored
            in the browser.
          </p>
          <span className="badge badge--warn" aria-label="Status: not yet implemented">
            🚧 Not yet implemented
          </span>
        </div>

        {/* ── Upcoming workflow steps ── */}
        <section className="submit-steps" aria-labelledby="steps-heading">
          <p className="submit-steps__title" id="steps-heading">
            Planned workflow
          </p>
          <ol className="submit-steps__list" aria-label="Planned workflow steps">
            {UPCOMING_STEPS.map((step, i) => (
              <li key={i} className="step-item">
                <div className="step-item__number" aria-label={`Step ${i + 1}`}>
                  {i + 1}
                </div>
                <div className="step-item__content">
                  <div className="step-item__label">{step.label}</div>
                  <div className="step-item__desc">
                    {step.desc}
                    <span className="text-muted text-xs" style={{ marginLeft: 8 }}>
                      [Segment {step.segment}]
                    </span>
                  </div>
                </div>
              </li>
            ))}
          </ol>
        </section>

      </div>
    </main>
  )
}

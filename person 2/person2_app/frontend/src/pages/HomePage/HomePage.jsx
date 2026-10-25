import './HomePage.css'

const FEATURES = [
  {
    id:    'audio',
    icon:  '🎙️',
    title: 'Audio Deepfake Detection',
    desc:  'Pretrained models identify synthetic voice patterns in suspicious call recordings.',
  },
  {
    id:    'ocr',
    icon:  '🖼️',
    title: 'Payment Screenshot OCR',
    desc:  'Extract and validate transaction fields — amount, UPI ID, timestamp — from uploaded screenshots.',
  },
  {
    id:    'reconcile',
    icon:  '⚖️',
    title: 'Payment Reconciliation',
    desc:  'Cross-check claimed payments against extracted evidence fields and rule-based scam patterns.',
  },
  {
    id:    'ledger',
    icon:  '🔒',
    title: 'Evidence Ledger',
    desc:  'SHA-256 hashes every uploaded file. An immutable audit trail is kept for each case.',
  },
  {
    id:    'decision',
    icon:  '📊',
    title: 'Transparent Decision Engine',
    desc:  'Each verdict — Low Concern, Verify First, or High Risk — shows the weighted evidence used.',
  },
  {
    id:    'export',
    icon:  '📄',
    title: 'Report Export',
    desc:  'Generate a structured PDF/JSON report of findings for regulators or consumer forums.',
  },
]

/**
 * HomePage — landing page with hero and feature overview.
 *
 * Props:
 *   onNavigate (fn: pageId→void)
 */
export default function HomePage({ onNavigate }) {
  return (
    <main className="page-content" id="main-content">
      <div className="container">

        {/* ── Hero ── */}
        <section className="hero" aria-labelledby="hero-heading">
          <div className="hero__eyebrow">
            <div className="hero__eyebrow-dot" aria-hidden="true" />
            Cybersecurity Hackathon Prototype
          </div>

          <h1 className="hero__title" id="hero-heading">
            Verify suspicious{' '}
            <span className="hero__title-accent">financial communications</span>{' '}
            with evidence-based analysis
          </h1>

          <p className="hero__subtitle">
            SatyaLens combines audio deepfake detection, OCR payment verification,
            and scam-pattern recognition to give you a transparent, auditable verdict.
          </p>

          <div className="hero__actions">
            <button
              id="cta-submit-evidence"
              className="btn btn--primary"
              onClick={() => onNavigate('submit')}
              aria-label="Submit evidence for analysis"
            >
              🔍 Submit Evidence
            </button>
            <button
              id="cta-learn-more"
              className="btn btn--ghost"
              onClick={() => onNavigate('about')}
            >
              How it works
            </button>
          </div>
        </section>

        <div className="divider" role="separator" />

        {/* ── Features ── */}
        <section className="features" aria-labelledby="features-heading">
          <h2 className="section-heading" id="features-heading">
            What <span>SatyaLens</span> analyses
          </h2>
          <p className="section-sub">
            Each analysis module runs independently and contributes weighted signals
            to the final risk verdict.
          </p>

          <div className="features__grid" role="list">
            {FEATURES.map(f => (
              <article
                key={f.id}
                id={`feature-${f.id}`}
                className="feature-card"
                role="listitem"
              >
                <div className="feature-card__icon" aria-hidden="true">{f.icon}</div>
                <h3 className="feature-card__title">{f.title}</h3>
                <p className="feature-card__desc">{f.desc}</p>
              </article>
            ))}
          </div>
        </section>

        {/* ── Disclaimer ── */}
        <div className="disclaimer" role="note" aria-label="Legal disclaimer">
          <span className="disclaimer__icon" aria-hidden="true">⚠️</span>
          <p className="disclaimer__text">
            <strong>Prototype notice:</strong>{' '}
            SatyaLens is a hackathon prototype. Verdicts are advisory and not a substitute
            for professional legal or financial advice. Never upload personally identifiable
            information beyond what is required for a specific case.
          </p>
        </div>

      </div>
    </main>
  )
}

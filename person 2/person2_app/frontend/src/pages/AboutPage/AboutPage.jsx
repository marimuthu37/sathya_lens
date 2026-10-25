import './AboutPage.css'

const OWNERSHIP_ROWS = [
  { area: 'Audio deepfake detection', owner: 'Person 1', segment: '2–3' },
  { area: 'Audio quality checks',     owner: 'Person 1', segment: '2–3' },
  { area: 'Payment screenshot OCR',   owner: 'Person 1', segment: '2–3' },
  { area: 'Field extraction',         owner: 'Person 1', segment: '2–3' },
  { area: 'Payment reconciliation',   owner: 'Person 1', segment: '2–3' },
  { area: 'Scam-context rules',       owner: 'Person 1', segment: '2–3' },
  { area: 'Test cases & metrics',     owner: 'Person 1', segment: '3–4' },
  { area: 'React + Vite frontend',    owner: 'Person 2', segment: '1–4' },
  { area: 'FastAPI backend',          owner: 'Person 2', segment: '1–4' },
  { area: 'SQLite case management',   owner: 'Person 2', segment: '2'   },
  { area: 'Consent & upload flow',    owner: 'Person 2', segment: '2'   },
  { area: 'Evidence ledger (SHA-256)',owner: 'Person 2', segment: '3'   },
  { area: 'Decision engine results',  owner: 'Person 2', segment: '3'   },
  { area: 'Report export',            owner: 'Person 2', segment: '4'   },
]

export default function AboutPage() {
  return (
    <main className="page-content" id="main-content">
      <div className="container about-page">

        <header className="about-page__header">
          <h1 className="section-heading">
            About <span>SatyaLens</span>
          </h1>
          <p className="section-sub">
            A hackathon prototype for verifying suspicious financial communications
            using open-source AI models, rule-based analysis, and a transparent evidence ledger.
          </p>
        </header>

        <section className="about-section" aria-labelledby="how-heading">
          <h2 id="how-heading">// How it works</h2>
          <p>
            A user submits a suspicious communication — typically an audio recording of
            a call and/or a screenshot of a payment confirmation — along with contextual
            details. SatyaLens hashes all uploads immediately (SHA-256) and passes them
            to independent analysis modules.
          </p>
          <p>
            Each module returns a signal. The decision engine combines those signals into
            one of three verdicts: <strong style={{color:'var(--color-ok)'}}>Low Concern</strong>,{' '}
            <strong style={{color:'var(--color-warn)'}}>Verify First</strong>, or{' '}
            <strong style={{color:'var(--color-risk)'}}>High Risk</strong> — along with
            a complete, human-readable explanation of the evidence used.
          </p>
        </section>

        <section className="about-section" aria-labelledby="ownership-heading">
          <h2 id="ownership-heading">// Module ownership</h2>
          <div className="card" style={{padding: 0, overflow: 'hidden'}}>
            <table className="ownership-table" aria-label="Module ownership by person and segment">
              <thead>
                <tr>
                  <th scope="col">Area</th>
                  <th scope="col">Owner</th>
                  <th scope="col">Segment</th>
                </tr>
              </thead>
              <tbody>
                {OWNERSHIP_ROWS.map((row, i) => (
                  <tr key={i}>
                    <td>{row.area}</td>
                    <td>
                      <span className={`badge ${row.owner === 'Person 1' ? 'badge--info' : 'badge--ok'}`}>
                        {row.owner}
                      </span>
                    </td>
                    <td className="text-muted text-mono text-sm">{row.segment}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="about-section" aria-labelledby="stack-heading">
          <h2 id="stack-heading">// Technology stack</h2>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
              gap: 'var(--space-3)',
            }}
          >
            {[
              { label: 'Frontend',  value: 'React 18 · Vite 5 · Plain CSS' },
              { label: 'Backend',   value: 'Python · FastAPI · Uvicorn'     },
              { label: 'Database',  value: 'SQLite · SQLAlchemy 2'          },
              { label: 'Validation',value: 'Pydantic v2'                    },
              { label: 'Uploads',   value: 'python-multipart'               },
              { label: 'Testing',   value: 'pytest · httpx'                 },
            ].map(s => (
              <div key={s.label} className="card" style={{padding: 'var(--space-4)'}}>
                <div className="text-xs text-muted text-mono" style={{marginBottom:4}}>{s.label}</div>
                <div className="text-sm font-medium">{s.value}</div>
              </div>
            ))}
          </div>
        </section>

      </div>
    </main>
  )
}

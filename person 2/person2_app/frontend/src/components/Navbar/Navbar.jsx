import { useState, useEffect } from 'react'
import './Navbar.css'

const NAV_ITEMS = [
  { id: 'home',     label: 'Home' },
  { id: 'history',  label: 'Case History' },
  { id: 'new-case', label: 'New Case' },
  { id: 'about',    label: 'About' },
]

/**
 * Navbar — fixed top navigation bar.
 *
 * Props:
 *   activePage  (string)            – currently active page id
 *   onNavigate  (fn: pageId→void)   – navigation callback
 *   apiStatus   ('ok'|'error'|'loading') – backend health status
 */
export default function Navbar({ activePage, onNavigate, apiStatus }) {
  const [scrolled, setScrolled] = useState(false)

  useEffect(() => {
    const handler = () => setScrolled(window.scrollY > 8)
    window.addEventListener('scroll', handler, { passive: true })
    return () => window.removeEventListener('scroll', handler)
  }, [])

  const statusLabel = {
    ok:      'API online',
    error:   'API offline',
    loading: 'Connecting…',
  }[apiStatus] ?? 'Unknown'

  return (
    <nav className={`navbar${scrolled ? ' navbar--scrolled' : ''}`} role="navigation" aria-label="Main navigation">
      <div className="navbar__inner">

        {/* Brand */}
        <button
          className="navbar__brand"
          onClick={() => onNavigate('home')}
          aria-label="Go to SatyaLens home"
          style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
        >
          <div className="navbar__logo-icon" aria-hidden="true">🔍</div>
          <div>
            <div className="navbar__brand-name">
              Satya<span>Lens</span>
            </div>
            <div className="navbar__tagline">Financial Verification System</div>
          </div>
        </button>

        {/* Desktop nav links */}
        <ul className="navbar__links" role="list">
          {NAV_ITEMS.map(item => (
            <li key={item.id}>
              <button
                id={`nav-${item.id}`}
                className={`navbar__link${activePage === item.id ? ' active' : ''}`}
                onClick={() => onNavigate(item.id)}
                aria-current={activePage === item.id ? 'page' : undefined}
              >
                {item.label}
              </button>
            </li>
          ))}
        </ul>

        {/* API status pill */}
        <div
          className="navbar__status"
          aria-label={`Backend status: ${statusLabel}`}
          title={`Backend status: ${statusLabel}`}
        >
          <div className={`navbar__status-dot${apiStatus === 'error' ? ' navbar__status-dot--error' : ''}`} />
          <span>{statusLabel}</span>
        </div>

        {/* Mobile menu button — links not shown on mobile yet */}
        <button className="navbar__menu-btn" aria-label="Open menu" aria-expanded="false">
          <span /><span /><span />
        </button>

      </div>
    </nav>
  )
}

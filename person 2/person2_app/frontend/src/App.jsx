import { useState, useEffect } from 'react'
import Navbar from './components/Navbar/Navbar'
import HomePage from './pages/HomePage/HomePage'
import SubmitPage from './pages/SubmitPage/SubmitPage'
import AboutPage from './pages/AboutPage/AboutPage'
import NewCasePage from './pages/NewCasePage/NewCasePage'
import CaseHistoryPage from './pages/CaseHistoryPage/CaseHistoryPage'
import CaseDetailPage from './pages/CaseDetailPage/CaseDetailPage'
import { fetchHealth } from './api/api'

/**
 * App — root component.
 *
 * Owns:
 *   activePage  — current page identifier
 *   selectedCaseId — case UUID when on the detail page
 *   apiStatus   — backend health-check result
 */
export default function App() {
  const [activePage,      setActivePage]      = useState('home')
  const [selectedCaseId,  setSelectedCaseId]  = useState(null)
  const [apiStatus,       setApiStatus]       = useState('loading')

  // ── Poll backend health once on mount ──────────────────────────────────────
  useEffect(() => {
    let cancelled = false
    fetchHealth()
      .then(() => { if (!cancelled) setApiStatus('ok') })
      .catch(() => { if (!cancelled) setApiStatus('error') })
    return () => { cancelled = true }
  }, [])

  // ── Navigation helpers ─────────────────────────────────────────────────────
  function navigateTo(page) {
    setActivePage(page)
    if (page !== 'case-detail') setSelectedCaseId(null)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  function openCaseDetail(caseId) {
    setSelectedCaseId(caseId)
    setActivePage('case-detail')
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  function afterCreate(caseId) {
    // After creating a case, navigate to its detail page
    openCaseDetail(caseId)
  }

  function afterDelete() {
    // After deleting, go back to history
    navigateTo('history')
  }

  // ── Page renderer ──────────────────────────────────────────────────────────
  function renderPage() {
    switch (activePage) {
      case 'new-case':
        return (
          <NewCasePage
            onNavigate={navigateTo}
            onCreated={afterCreate}
          />
        )
      case 'history':
        return (
          <CaseHistoryPage
            onNavigate={navigateTo}
            onSelectCase={openCaseDetail}
          />
        )
      case 'case-detail':
        return (
          <CaseDetailPage
            caseId={selectedCaseId}
            onNavigate={navigateTo}
            onDeleted={afterDelete}
          />
        )
      case 'submit':
        return <SubmitPage />
      case 'about':
        return <AboutPage />
      default:
        return <HomePage onNavigate={navigateTo} />
    }
  }

  return (
    <>
      <Navbar
        activePage={activePage}
        onNavigate={navigateTo}
        apiStatus={apiStatus}
      />
      {renderPage()}
    </>
  )
}

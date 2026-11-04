import { useEffect, useState } from 'react'
import {
  CircleCheck,
  FileCheck2,
  FolderKanban,
  Plus,
  ReceiptText,
  RefreshCw,
  Wallet,
} from 'lucide-react'
import PurchasesPage from './PurchasesPage'
import {
  createProject,
  getPurchaseStats,
  listProjects,
  type Project,
  type PurchaseStats,
} from './api'

type View = 'overview' | 'purchases'

export default function App() {
  const [projects, setProjects] = useState<Project[]>([])
  const [stats, setStats] = useState<PurchaseStats | null>(null)
  const [apiStatus, setApiStatus] = useState<'checking' | 'online' | 'offline'>('checking')
  const [view, setView] = useState<View>('overview')
  const [projectName, setProjectName] = useState('')
  const [projectCode, setProjectCode] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [refreshKey, setRefreshKey] = useState(0)

  async function load() {
    try {
      const [projectResponse, purchaseStats] = await Promise.all([
        listProjects(),
        getPurchaseStats(),
      ])
      setProjects(projectResponse.filter(project => project.is_active))
      setStats(purchaseStats)
      setApiStatus('online')
      setError('')
    } catch {
      setApiStatus('offline')
      setError('Cannot reach the backend. Check that the API is running, then retry.')
    }
  }

  useEffect(() => { void load() }, [])

  async function addProject(event: React.FormEvent) {
    event.preventDefault()
    if (!projectName.trim()) return
    setSaving(true)
    setError('')
    try {
      await createProject({ name: projectName, code: projectCode || null })
      setProjectName('')
      setProjectCode('')
      await load()
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : 'Unexpected error')
    } finally {
      setSaving(false)
    }
  }

  function refresh() {
    void load()
    setRefreshKey(value => value + 1)
  }

  return (
    <main className="shell">
      <aside className="sidebar">
        <div className="brand-mark"><ReceiptText size={22} /></div>
        <div className="brand-copy">
          <strong>Project Expenses</strong>
          <span>Management workspace</span>
        </div>
        <nav aria-label="Main navigation">
          <button className={view === 'overview' ? 'active' : ''} onClick={() => setView('overview')}><Wallet size={18} /> Overview</button>
          <button className={view === 'purchases' ? 'active' : ''} onClick={() => setView('purchases')}><ReceiptText size={18} /> Purchases</button>
          <button onClick={() => setView('overview')}><FolderKanban size={18} /> Projects</button>
          <button disabled><CircleCheck size={18} /> Verification <span className="soon">Soon</span></button>
        </nav>
        <div className="sidebar-foot">Self-hosted · Local workspace</div>
      </aside>

      <section className="content">
        <header className="topbar">
          <div>
            <p className="eyebrow">WORKSPACE / {view.toUpperCase()}</p>
            <h1>{view === 'overview' ? 'Good day, Bill Keeper' : 'Purchases'}</h1>
            <p className="muted">{view === 'overview' ? 'Your project expense workspace at a glance.' : 'Review submitted purchase receipts and their details.'}</p>
          </div>
          <div className={`connection ${apiStatus}`}>
            <span className="dot" /> API {apiStatus}
            <button className="icon-button" onClick={refresh} aria-label="Refresh dashboard">
              <RefreshCw size={16} />
            </button>
          </div>
        </header>

        {view === 'purchases' ? (
          <PurchasesPage refreshKey={refreshKey} />
        ) : (
          <>
            <div className="stats">
              <article className="stat-card">
                <div className="stat-icon purple"><FolderKanban size={19} /></div>
                <span>Active projects</span>
                <strong>{projects.length}</strong>
                <small>Registered projects</small>
              </article>
              <article className="stat-card">
                <div className="stat-icon blue"><ReceiptText size={19} /></div>
                <span>Total purchases</span>
                <strong>{stats?.total_purchases ?? '—'}</strong>
                <small>Receipt submissions in SQLite</small>
              </article>
              <article className="stat-card">
                <div className="stat-icon orange"><FileCheck2 size={19} /></div>
                <span>Awaiting verification</span>
                <strong>{stats?.awaiting_verification ?? '—'}</strong>
                <small>Received or under review</small>
              </article>
              <article className="stat-card">
                <div className="stat-icon green"><CircleCheck size={19} /></div>
                <span>Approved purchases</span>
                <strong>{stats?.status_counts.approved ?? '—'}</strong>
                <small>No financial totals calculated</small>
              </article>
            </div>

            <section className="panel">
              <div className="panel-heading">
                <div>
                  <h2>Project registry</h2>
                  <p className="muted">Create and manage the projects whose expenses you track.</p>
                </div>
              </div>

              <form className="project-form" onSubmit={addProject}>
                <input value={projectName} onChange={event => setProjectName(event.target.value)} placeholder="Project name (e.g. Pandora Smart Locker)" required />
                <input value={projectCode} onChange={event => setProjectCode(event.target.value)} placeholder="Short code (optional)" />
                <button disabled={saving}><Plus size={17} /> {saving ? 'Adding…' : 'Add project'}</button>
              </form>

              {error && <div className="error" role="alert">{error} <button className="text-button" onClick={() => void load()}>Retry</button></div>}

              <div className="project-list">
                {projects.length === 0 ? (
                  <div className="empty">
                    <FolderKanban size={28} />
                    <strong>No projects registered yet</strong>
                    <span>Add your first project above to get started.</span>
                  </div>
                ) : projects.map(project => (
                  <div className="project-row" key={project.id}>
                    <div className="project-avatar"><FolderKanban size={18} /></div>
                    <div className="project-info">
                      <strong>{project.name}</strong>
                      <span>{project.code || 'No project code'}</span>
                    </div>
                    <span className="active-label">Active</span>
                  </div>
                ))}
              </div>
            </section>
          </>
        )}

        <footer>Project Expense Manager · Local development environment · LKR</footer>
      </section>
    </main>
  )
}

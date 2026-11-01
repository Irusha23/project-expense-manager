import { useEffect, useState } from 'react'
import { FolderKanban, ReceiptText, Wallet, CircleCheck, Plus, RefreshCw } from 'lucide-react'

type Project = {
  id: string
  name: string
  code: string | null
  description: string | null
  is_active: boolean
}

const API = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

export default function App() {
  const [projects, setProjects] = useState<Project[]>([])
  const [apiStatus, setApiStatus] = useState<'checking' | 'online' | 'offline'>('checking')
  const [projectName, setProjectName] = useState('')
  const [projectCode, setProjectCode] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  async function load() {
    try {
      const [health, projectResponse] = await Promise.all([
        fetch(`${API}/health`),
        fetch(`${API}/api/v1/projects`),
      ])
      if (!health.ok || !projectResponse.ok) throw new Error('API request failed')
      setApiStatus('online')
      setProjects(await projectResponse.json())
      setError('')
    } catch {
      setApiStatus('offline')
      setError('Cannot reach the backend. Check that the API is running.')
    }
  }

  useEffect(() => { void load() }, [])

  async function addProject(event: React.FormEvent) {
    event.preventDefault()
    if (!projectName.trim()) return
    setSaving(true)
    setError('')
    try {
      const response = await fetch(`${API}/api/v1/projects`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: projectName, code: projectCode || null }),
      })
      if (!response.ok) {
        const body = await response.json()
        throw new Error(body.detail ?? 'Could not create project')
      }
      setProjectName('')
      setProjectCode('')
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unexpected error')
    } finally {
      setSaving(false)
    }
  }

  return (
    <main className="shell">
      <aside className="sidebar">
        <div className="brand-mark"><ReceiptText size={22} /></div>
        <div className="brand-copy">
          <strong>Project Expenses</strong>
          <span>Management workspace</span>
        </div>
        <nav>
          <a className="active"><Wallet size={18} /> Overview</a>
          <a><ReceiptText size={18} /> Purchases <span className="soon">Soon</span></a>
          <a><FolderKanban size={18} /> Projects</a>
          <a><CircleCheck size={18} /> Verification <span className="soon">Soon</span></a>
        </nav>
        <div className="sidebar-foot">Self-hosted · Phase 1</div>
      </aside>

      <section className="content">
        <header className="topbar">
          <div>
            <p className="eyebrow">WORKSPACE / OVERVIEW</p>
            <h1>Good day, Bill Keeper</h1>
            <p className="muted">Your project expense workspace is being set up.</p>
          </div>
          <div className={`connection ${apiStatus}`}>
            <span className="dot" /> API {apiStatus}
            <button className="icon-button" onClick={() => void load()} aria-label="Refresh">
              <RefreshCw size={16} />
            </button>
          </div>
        </header>

        <div className="stats">
          <article className="stat-card">
            <div className="stat-icon purple"><FolderKanban size={19} /></div>
            <span>Active projects</span>
            <strong>{projects.length}</strong>
            <small>Registered projects</small>
          </article>
          <article className="stat-card">
            <div className="stat-icon blue"><ReceiptText size={19} /></div>
            <span>Purchases</span>
            <strong>—</strong>
            <small>Coming in Phase 2</small>
          </article>
          <article className="stat-card">
            <div className="stat-icon green"><Wallet size={19} /></div>
            <span>Total expenditure</span>
            <strong>—</strong>
            <small>Available after ledger setup</small>
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
            <input value={projectName} onChange={e => setProjectName(e.target.value)} placeholder="Project name (e.g. Pandora Smart Locker)" required />
            <input value={projectCode} onChange={e => setProjectCode(e.target.value)} placeholder="Short code (optional)" />
            <button disabled={saving}><Plus size={17} /> {saving ? 'Adding…' : 'Add project'}</button>
          </form>

          {error && <div className="error">{error}</div>}

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

        <footer>Project Expense Manager · Local development environment · LKR</footer>
      </section>
    </main>
  )
}

import { useEffect, useState } from 'react'
import {
  ArrowDown,
  ArrowUp,
  ChevronLeft,
  ChevronRight,
  Download,
  ExternalLink,
  FileText,
  LoaderCircle,
  RefreshCw,
  Search,
  X,
} from 'lucide-react'
import {
  getPurchase,
  getReceiptBlob,
  listProjects,
  listPurchases,
  type Project,
  type Purchase,
  type PurchaseStatus,
} from './api'

const STATUSES: PurchaseStatus[] = [
  'received',
  'processing',
  'needs_review',
  'pending_approval',
  'approved',
  'rejected',
  'sync_pending',
  'synced',
  'sync_failed',
]

type Props = { refreshKey: number }

function formatDate(value: string | null): string {
  if (!value) return 'Not available'
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value))
}

export default function PurchasesPage({ refreshKey }: Props) {
  const [projects, setProjects] = useState<Project[]>([])
  const [purchases, setPurchases] = useState<Purchase[]>([])
  const [selected, setSelected] = useState<Purchase | null>(null)
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [totalPages, setTotalPages] = useState(0)
  const [projectId, setProjectId] = useState('')
  const [status, setStatus] = useState('')
  const [searchInput, setSearchInput] = useState('')
  const [search, setSearch] = useState('')
  const [sortBy, setSortBy] = useState('created_at')
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  async function loadProjects() {
    try {
      setProjects(await listProjects())
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Could not load projects.')
    }
  }

  async function loadPurchases() {
    setLoading(true)
    setError('')
    try {
      const result = await listPurchases({
        page,
        pageSize: 10,
        projectId,
        status,
        search,
        sortBy,
        sortOrder,
      })
      setPurchases(result.items)
      setTotal(result.total)
      setTotalPages(result.total_pages)
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Could not load purchases.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void loadProjects() }, [])
  useEffect(() => { void loadPurchases() }, [page, projectId, status, search, sortBy, sortOrder, refreshKey])

  async function openPurchase(purchaseId: string) {
    setError('')
    try {
      setSelected(await getPurchase(purchaseId))
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Could not load purchase details.')
    }
  }

  function applySearch(event: React.FormEvent) {
    event.preventDefault()
    setPage(1)
    setSearch(searchInput)
  }

  function changeSort(value: string) {
    if (sortBy === value) {
      setSortOrder(current => current === 'asc' ? 'desc' : 'asc')
    } else {
      setSortBy(value)
      setSortOrder(value === 'created_at' ? 'desc' : 'asc')
    }
    setPage(1)
  }

  return (
    <section className="panel purchases-panel">
      <div className="panel-heading">
        <div>
          <h2>Purchase submissions</h2>
          <p className="muted">{total} receipt submissions from the database.</p>
        </div>
        <button className="secondary-button" onClick={() => void loadPurchases()} disabled={loading}>
          <RefreshCw size={15} /> Refresh
        </button>
      </div>

      <div className="purchase-filters">
        <form className="search-form" onSubmit={applySearch}>
          <Search size={16} />
          <input
            aria-label="Search purchases"
            value={searchInput}
            onChange={event => setSearchInput(event.target.value)}
            placeholder="Search project, purchaser, store or receipt"
          />
          <button type="submit">Search</button>
        </form>
        <select aria-label="Filter by project" value={projectId} onChange={event => { setProjectId(event.target.value); setPage(1) }}>
          <option value="">All projects</option>
          {projects.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}
        </select>
        <select aria-label="Filter by status" value={status} onChange={event => { setStatus(event.target.value); setPage(1) }}>
          <option value="">All statuses</option>
          {STATUSES.map(value => <option key={value} value={value}>{value.replace(/_/g, ' ')}</option>)}
        </select>
        <select aria-label="Sort purchases" value={sortBy} onChange={event => changeSort(event.target.value)}>
          <option value="created_at">Submission date</option>
          <option value="purchase_date">Purchase date</option>
          <option value="project">Project</option>
          <option value="purchaser">Purchaser</option>
          <option value="status">Status</option>
          <option value="store_name">Store</option>
        </select>
        <button className="icon-button sort-direction" onClick={() => setSortOrder(current => current === 'asc' ? 'desc' : 'asc')} aria-label={`Sort ${sortOrder === 'asc' ? 'descending' : 'ascending'}`}>
          {sortOrder === 'asc' ? <ArrowUp size={16} /> : <ArrowDown size={16} />}
        </button>
      </div>

      {error && (
        <div className="error purchases-error" role="alert">
          <span>{error}</span>
          <button className="text-button" onClick={() => void loadPurchases()}>Try again</button>
        </div>
      )}

      {loading ? (
        <div className="purchase-state"><LoaderCircle className="spin" size={22} /> Loading purchases…</div>
      ) : purchases.length === 0 ? (
        <div className="empty purchase-empty">
          <FileText size={28} />
          <strong>No purchases found</strong>
          <span>{search || projectId || status ? 'Try changing or clearing the filters.' : 'Submitted receipts will appear here.'}</span>
        </div>
      ) : (
        <>
          <div className="purchase-table-wrap">
            <table className="purchase-table">
              <thead>
                <tr>
                  <th><button onClick={() => changeSort('project')}>Project</button></th>
                  <th><button onClick={() => changeSort('purchaser')}>Purchaser</button></th>
                  <th>Receipt</th>
                  <th><button onClick={() => changeSort('created_at')}>Submitted</button></th>
                  <th><button onClick={() => changeSort('status')}>Status</button></th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {purchases.map(purchase => (
                  <tr key={purchase.id}>
                    <td data-label="Project"><strong>{purchase.project_name}</strong></td>
                    <td data-label="Purchaser">{purchase.purchaser_name}</td>
                    <td data-label="Receipt">{purchase.receipts[0]?.original_filename ?? 'No receipt file'}</td>
                    <td data-label="Submitted">{formatDate(purchase.created_at)}</td>
                    <td data-label="Status"><span className={`status-pill status-${purchase.status}`}>{purchase.status.replace(/_/g, ' ')}</span></td>
                    <td data-label="Action"><button className="row-action" onClick={() => void openPurchase(purchase.id)}>View <ExternalLink size={14} /></button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="pagination">
            <span>Page {page} of {Math.max(totalPages, 1)}</span>
            <div>
              <button className="secondary-button" aria-label="Previous page" disabled={page <= 1} onClick={() => setPage(current => current - 1)}><ChevronLeft size={16} /> Previous</button>
              <button className="secondary-button" aria-label="Next page" disabled={page >= totalPages} onClick={() => setPage(current => current + 1)}>Next <ChevronRight size={16} /></button>
            </div>
          </div>
        </>
      )}

      {selected && <PurchaseDetails purchase={selected} onClose={() => setSelected(null)} />}
    </section>
  )
}

function PurchaseDetails({ purchase, onClose }: { purchase: Purchase; onClose: () => void }) {
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onClose() }}>
      <section className="purchase-modal" role="dialog" aria-modal="true" aria-labelledby="purchase-detail-title">
        <div className="modal-heading">
          <div><p className="eyebrow">PURCHASE DETAILS</p><h2 id="purchase-detail-title">{purchase.project_name}</h2></div>
          <button className="icon-button" onClick={onClose} aria-label="Close details"><X size={20} /></button>
        </div>
        <div className="detail-grid">
          <DetailItem label="Project" value={purchase.project_name} />
          <DetailItem label="Purchaser" value={purchase.purchaser_name} />
          <DetailItem label="Submitted" value={formatDate(purchase.created_at)} />
          <DetailItem label="Purchase date" value={purchase.purchase_date ?? 'Not available'} />
          <DetailItem label="Status" value={purchase.status.replace(/_/g, ' ')} />
          <DetailItem label="Store" value={purchase.store_name ?? 'Not available'} />
        </div>
        <h3 className="receipt-heading">Receipt files <span>{purchase.receipt_count}</span></h3>
        {purchase.receipts.length === 0 ? <p className="muted">No receipt metadata is available.</p> : (
          <div className="receipt-detail-list">
            {purchase.receipts.map(receipt => <ReceiptDetail key={receipt.id} receipt={receipt} />)}
          </div>
        )}
      </section>
    </div>
  )
}

function DetailItem({ label, value }: { label: string; value: string }) {
  return <div className="detail-item"><span>{label}</span><strong>{value}</strong></div>
}

function ReceiptDetail({ receipt }: { receipt: Purchase['receipts'][number] }) {
  const [previewUrl, setPreviewUrl] = useState('')
  const [previewError, setPreviewError] = useState('')
  const [downloading, setDownloading] = useState(false)
  const isPreviewable = receipt.mime_type === 'application/pdf' || receipt.mime_type.startsWith('image/')

  useEffect(() => {
    let objectUrl = ''
    let cancelled = false
    if (isPreviewable) {
      void getReceiptBlob(receipt.preview_url).then(blob => {
        if (!cancelled) {
          objectUrl = URL.createObjectURL(blob)
          setPreviewUrl(objectUrl)
        }
      }).catch(error => {
        if (!cancelled) setPreviewError(error instanceof Error ? error.message : 'Could not load receipt preview.')
      })
    }
    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [receipt.id, receipt.preview_url, isPreviewable])

  async function download() {
    setDownloading(true)
    setPreviewError('')
    try {
      const blob = await getReceiptBlob(receipt.download_url)
      const objectUrl = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = objectUrl
      anchor.download = receipt.original_filename
      anchor.click()
      URL.revokeObjectURL(objectUrl)
    } catch (error) {
      setPreviewError(error instanceof Error ? error.message : 'Could not download receipt.')
    } finally {
      setDownloading(false)
    }
  }

  return (
    <article className="receipt-detail">
      <div className="receipt-file-heading">
        <div className="project-avatar"><FileText size={17} /></div>
        <div><strong>{receipt.original_filename}</strong><span>{receipt.mime_type} · SHA-256 {receipt.sha256.slice(0, 12)}…</span></div>
        <button className="secondary-button download-button" disabled={downloading} onClick={() => void download()}><Download size={15} /> {downloading ? 'Downloading…' : 'Download'}</button>
      </div>
      {previewError && <p className="error" role="alert">{previewError}</p>}
      {previewUrl && receipt.mime_type.startsWith('image/') && <img className="receipt-preview" src={previewUrl} alt={`Receipt ${receipt.original_filename}`} />}
      {previewUrl && receipt.mime_type === 'application/pdf' && <iframe className="receipt-preview pdf-preview" src={previewUrl} title={`Receipt ${receipt.original_filename}`} />}
      {!isPreviewable && <p className="muted">Preview is not available for this file type.</p>}
    </article>
  )
}

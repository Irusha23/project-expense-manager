const API = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

export type Project = {
  id: string
  name: string
  code: string | null
  description: string | null
  is_active: boolean
}

export type PurchaseStatus =
  | 'received'
  | 'processing'
  | 'needs_review'
  | 'pending_approval'
  | 'approved'
  | 'rejected'
  | 'sync_pending'
  | 'synced'
  | 'sync_failed'

export type Receipt = {
  id: string
  original_filename: string
  mime_type: string
  sha256: string
  page_number: number | null
  created_at: string
  preview_url: string
  download_url: string
}

export type Purchase = {
  id: string
  project_id: string
  project_name: string
  purchaser_id: string
  purchaser_name: string
  purchase_date: string | null
  created_at: string
  store_name: string | null
  status: PurchaseStatus
  receipt_count: number
  receipts: Receipt[]
}

export type PurchasePage = {
  items: Purchase[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

export type PurchaseStats = {
  total_purchases: number
  awaiting_verification: number
  status_counts: Record<PurchaseStatus, number>
}

export type PurchaseFilters = {
  page: number
  pageSize: number
  projectId: string
  status: string
  search: string
  sortBy: string
  sortOrder: 'asc' | 'desc'
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, init)
  if (!response.ok) {
    let message = `Request failed (${response.status})`
    try {
      const body: { detail?: string } = await response.json()
      if (body.detail) message = body.detail
    } catch {
      // Keep the HTTP status message when the response is not JSON.
    }
    throw new Error(message)
  }
  return response.json() as Promise<T>
}

export function listProjects(): Promise<Project[]> {
  return request<Project[]>('/api/v1/projects?active_only=false')
}

export function createProject(payload: { name: string; code: string | null }): Promise<Project> {
  return request<Project>('/api/v1/projects', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function getPurchaseStats(): Promise<PurchaseStats> {
  return request<PurchaseStats>('/api/v1/purchases/stats')
}

export function listPurchases(filters: PurchaseFilters): Promise<PurchasePage> {
  const query = new URLSearchParams({
    page: String(filters.page),
    page_size: String(filters.pageSize),
    sort_by: filters.sortBy,
    sort_order: filters.sortOrder,
  })
  if (filters.projectId) query.set('project_id', filters.projectId)
  if (filters.status) query.set('status', filters.status)
  if (filters.search.trim()) query.set('search', filters.search.trim())
  return request<PurchasePage>(`/api/v1/purchases?${query.toString()}`)
}

export function getPurchase(purchaseId: string): Promise<Purchase> {
  return request<Purchase>(`/api/v1/purchases/${encodeURIComponent(purchaseId)}`)
}

export async function getReceiptBlob(receiptUrl: string): Promise<Blob> {
  const response = await fetch(new URL(receiptUrl, API))
  if (!response.ok) {
    throw new Error(
      response.status === 404
        ? 'This receipt file is no longer available.'
        : `Receipt request failed (${response.status})`,
    )
  }
  return response.blob()
}

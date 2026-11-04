# Project Expense Manager

Self-hosted project expense management system.

## MVP decisions
- Multiple projects
- One dashboard administrator (bill keeper)
- Telegram receipt intake (images and PDFs) — Phase 2
- FastAPI backend
- React + TypeScript + Vite frontend
- SQLite operational database
- Google Drive receipt archive and Google Sheets ledger — later phases
- PaddleOCR and optional Ollama — later phases

## Architecture
The SQLite database is the operational source of truth. Google Sheets is a synchronized reporting ledger, not the primary database. Approved purchases are synchronized through an idempotent outbox in a later phase.

Money is stored as integer minor units (LKR cents). Quantities use fixed precision.

## Requirements
- Docker Desktop with Docker Compose
- Or Python 3.11+ and Node.js 20+

## Run with Docker
1. Copy `.env.example` to `.env`.
2. Set a strong `APP_SECRET_KEY`.
3. Run:

   ```bash
   docker compose up --build
   ```

4. Backend: http://localhost:8000
5. API docs: http://localhost:8000/docs
6. Frontend: http://localhost:5173

## Run backend locally
```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Run frontend locally
```bash
cd frontend
npm install
npm run dev
```

## Database model (Phase 1)
- Project: projects
- Member: members (Telegram identity will be linked in Phase 2)
- Component: standardized component catalogue
- Purchase: one record per receipt
- PurchaseItem: one record per line item
- ReceiptFile: original receipt metadata and Drive reference
- ProcessingJob: asynchronous receipt processing state
- AuditLog: append-only record of important actions
- SyncOutbox: reliable Google Sheets/Drive synchronization queue

## Important safeguards
- Never commit `.env`, bot tokens, Google credentials, or database files.
- Approved purchases must not be hard-deleted.
- A purchase is not approved merely because OCR completed.
- Receipt totals must be reconciled before approval.
- Google Sheets sync must be idempotent using `purchase_id`.
- Back up `storage/expense_manager.db` and receipt files regularly.

## Purchases dashboard API (Phase 2.6)

The local dashboard reads purchases and receipt metadata from SQLite through:

- `GET /api/v1/purchases` (filters, sorting, and pagination)
- `GET /api/v1/purchases/{purchase_id}`
- `GET /api/v1/purchases/stats`
- `GET /api/v1/purchases/{purchase_id}/receipts/{receipt_id}` (inline preview)
- `GET /api/v1/purchases/{purchase_id}/receipts/{receipt_id}/download`

Receipt paths are resolved only beneath `STORAGE_ROOT/receipts`; the backend and
Telegram bot share `./storage` as `/app/storage` in Docker Compose. The API
currently follows the existing trusted local-development access model and has
no dashboard authentication or authorization. Do not expose it to untrusted
networks until authentication and access controls are implemented.

## Development-only Telegram session schema reset

`Base.metadata.create_all()` does not add columns to an existing table. If a
development database has an older `purchase_sessions` table without
`callback_token`, stop both database users and reset only that table; projects,
members, and all other records are preserved. This discards in-progress
Telegram purchase sessions:

```powershell
docker compose stop backend telegram-bot
python -c "import sqlite3; source=sqlite3.connect('storage/expense_manager.db'); backup=sqlite3.connect('storage/expense_manager.db.backup'); source.backup(backup); backup.close(); source.execute('DROP TABLE IF EXISTS purchase_sessions'); source.commit(); source.close()"
docker compose up --build -d backend telegram-bot
```

The backend recreates `purchase_sessions` with its current schema on startup.
Do not run this reset against production data.

## Current scope
The project currently provides project management, Telegram receipt submission
and local persistence, and a purchases dashboard. OCR, approval UI, Google APIs,
financial reporting, and dashboard authentication are scheduled for later phases.

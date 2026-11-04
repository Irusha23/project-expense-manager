Install the backend and test dependencies, then run the test suite from `backend`:

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest
```

Telegram project-selection and receipt-upload tests use isolated SQLite
databases and temporary receipt directories. They do not read or modify
`storage/expense_manager.db`.

Receipt uploads accept Telegram photos and JPEG, PNG, WEBP, and PDF documents.
The maximum upload size is configurable with `MAX_RECEIPT_UPLOAD_SIZE_MB`
(default: 10 MB). Stored receipt files are shared with the backend under
`storage/receipts/`; the database contains relative paths and SHA-256 checksums.

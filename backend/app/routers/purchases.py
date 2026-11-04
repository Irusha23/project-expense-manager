import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.config import settings
from app.database import get_db
from app.models import Member, Project, Purchase, PurchaseStatus, ReceiptFile
from app.schemas import (
    PurchasePageRead,
    PurchaseRead,
    PurchaseStatsRead,
    ReceiptMetadataRead,
)

router = APIRouter(prefix="/api/v1/purchases", tags=["purchases"])

STORAGE_ROOT = Path(settings.storage_root)
RECEIPT_MIME_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
}
SORT_COLUMNS = {
    "created_at": Purchase.created_at,
    "purchase_date": Purchase.purchase_date,
    "project": Project.name,
    "purchaser": Member.full_name,
    "status": Purchase.status,
    "store_name": Purchase.store_name,
}
AWAITING_VERIFICATION_STATUSES = {
    PurchaseStatus.RECEIVED,
    PurchaseStatus.PROCESSING,
    PurchaseStatus.NEEDS_REVIEW,
    PurchaseStatus.PENDING_APPROVAL,
}


def purchase_query_filters(
    *,
    project_id: str | None,
    status: PurchaseStatus | None,
    search: str | None,
):
    query = select(Purchase).join(Project).join(Member)
    if project_id:
        query = query.where(Purchase.project_id == project_id)
    if status:
        query = query.where(Purchase.status == status)
    if search:
        pattern = f"%{search.strip()}%"
        receipt_match = select(ReceiptFile.id).where(
            ReceiptFile.purchase_id == Purchase.id,
            ReceiptFile.original_filename.ilike(pattern),
        ).exists()
        query = query.where(
            or_(
                Project.name.ilike(pattern),
                Member.full_name.ilike(pattern),
                Purchase.store_name.ilike(pattern),
                receipt_match,
            )
        )
    return query


def serialize_purchase(purchase: Purchase) -> PurchaseRead:
    receipts = [
        ReceiptMetadataRead(
            id=receipt.id,
            original_filename=receipt.original_filename,
            mime_type=receipt.mime_type,
            sha256=receipt.sha256,
            page_number=receipt.page_number,
            created_at=receipt.created_at,
            preview_url=(
                f"/api/v1/purchases/{purchase.id}/receipts/{receipt.id}"
            ),
            download_url=(
                f"/api/v1/purchases/{purchase.id}/receipts/"
                f"{receipt.id}/download"
            ),
        )
        for receipt in purchase.receipt_files
    ]
    return PurchaseRead(
        id=purchase.id,
        project_id=purchase.project_id,
        project_name=purchase.project.name,
        purchaser_id=purchase.purchaser_id,
        purchaser_name=purchase.purchaser.full_name,
        purchase_date=purchase.purchase_date,
        created_at=purchase.created_at,
        store_name=purchase.store_name,
        status=purchase.status.value,
        receipt_count=len(receipts),
        receipts=receipts,
    )


@router.get("", response_model=PurchasePageRead)
def list_purchases(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    project_id: str | None = None,
    status: PurchaseStatus | None = None,
    search: str | None = Query(default=None, max_length=200),
    sort_by: str = Query(default="created_at"),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db),
):
    sort_column = SORT_COLUMNS.get(sort_by)
    if sort_column is None:
        raise HTTPException(status_code=422, detail="Invalid sort_by value.")

    filters = purchase_query_filters(
        project_id=project_id,
        status=status,
        search=search,
    )
    total = db.scalar(
        select(func.count()).select_from(filters.order_by(None).subquery())
    ) or 0
    order_column = sort_column.asc() if sort_order == "asc" else sort_column.desc()
    purchases = db.scalars(
        filters.options(selectinload(Purchase.receipt_files))
        .options(
            joinedload(Purchase.project),
            joinedload(Purchase.purchaser),
        )
        .order_by(order_column, Purchase.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    total_pages = (total + page_size - 1) // page_size
    return PurchasePageRead(
        items=[serialize_purchase(purchase) for purchase in purchases],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get("/stats", response_model=PurchaseStatsRead)
def purchase_stats(db: Session = Depends(get_db)):
    rows = db.execute(
        select(Purchase.status, func.count(Purchase.id)).group_by(Purchase.status)
    ).all()
    status_counts = {status.value: 0 for status in PurchaseStatus}
    status_counts.update({status.value: count for status, count in rows})
    total = sum(status_counts.values())
    awaiting = sum(
        status_counts[status.value] for status in AWAITING_VERIFICATION_STATUSES
    )
    return PurchaseStatsRead(
        total_purchases=total,
        awaiting_verification=awaiting,
        status_counts=status_counts,
    )


@router.get("/{purchase_id}", response_model=PurchaseRead)
def get_purchase(purchase_id: str, db: Session = Depends(get_db)):
    purchase = db.scalar(
        select(Purchase)
        .where(Purchase.id == purchase_id)
        .options(
            selectinload(Purchase.receipt_files),
            selectinload(Purchase.project),
            selectinload(Purchase.purchaser),
        )
    )
    if purchase is None:
        raise HTTPException(status_code=404, detail="Purchase not found.")
    return serialize_purchase(purchase)


def resolve_receipt(receipt: ReceiptFile) -> tuple[Path, str]:
    if receipt.local_path is None:
        raise HTTPException(status_code=404, detail="Receipt file not found.")
    if receipt.mime_type not in RECEIPT_MIME_TYPES:
        raise HTTPException(status_code=404, detail="Receipt file not found.")

    storage_root = STORAGE_ROOT.resolve()
    receipts_root = (storage_root / "receipts").resolve()
    relative_path = Path(receipt.local_path)
    if relative_path.is_absolute():
        raise HTTPException(status_code=404, detail="Receipt file not found.")
    try:
        resolved_path = (storage_root / relative_path).resolve(strict=True)
        resolved_path.relative_to(receipts_root)
    except (OSError, RuntimeError, ValueError):
        raise HTTPException(status_code=404, detail="Receipt file not found.")
    if not resolved_path.is_file():
        raise HTTPException(status_code=404, detail="Receipt file not found.")

    filename = receipt.original_filename.replace("\\", "/").rsplit("/", 1)[-1]
    filename = re.sub(r"[\x00-\x1f\x7f]", "", filename).strip()
    return resolved_path, filename or "receipt"


def serve_receipt(
    purchase_id: str,
    receipt_id: str,
    db: Session,
    *,
    download: bool,
):
    receipt = db.scalar(
        select(ReceiptFile).where(
            ReceiptFile.id == receipt_id,
            ReceiptFile.purchase_id == purchase_id,
        )
    )
    if receipt is None:
        raise HTTPException(status_code=404, detail="Receipt not found.")
    path, filename = resolve_receipt(receipt)
    return FileResponse(
        path,
        media_type=receipt.mime_type,
        filename=filename,
        content_disposition_type="attachment" if download else "inline",
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.get("/{purchase_id}/receipts/{receipt_id}")
def preview_receipt(
    purchase_id: str,
    receipt_id: str,
    db: Session = Depends(get_db),
):
    return serve_receipt(purchase_id, receipt_id, db, download=False)


@router.get("/{purchase_id}/receipts/{receipt_id}/download")
def download_receipt(
    purchase_id: str,
    receipt_id: str,
    db: Session = Depends(get_db),
):
    return serve_receipt(purchase_id, receipt_id, db, download=True)

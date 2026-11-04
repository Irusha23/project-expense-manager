from datetime import date, datetime
from pydantic import BaseModel, ConfigDict, Field


class ProjectCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    code: str | None = Field(default=None, max_length=32)
    description: str | None = None


class ProjectRead(BaseModel):
    id: str
    name: str
    code: str | None
    description: str | None
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ReceiptMetadataRead(BaseModel):
    id: str
    original_filename: str
    mime_type: str
    sha256: str
    page_number: int | None
    created_at: datetime
    preview_url: str
    download_url: str

    model_config = ConfigDict(from_attributes=True)


class PurchaseRead(BaseModel):
    id: str
    project_id: str
    project_name: str
    purchaser_id: str
    purchaser_name: str
    purchase_date: date | None
    created_at: datetime
    store_name: str | None
    status: str
    receipt_count: int
    receipts: list[ReceiptMetadataRead]


class PurchasePageRead(BaseModel):
    items: list[PurchaseRead]
    total: int
    page: int
    page_size: int
    total_pages: int


class PurchaseStatsRead(BaseModel):
    total_purchases: int
    awaiting_verification: int
    status_counts: dict[str, int]

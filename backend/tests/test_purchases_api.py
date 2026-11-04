import asyncio
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.database import get_db
from app.models import PurchaseStatus
from app.routers import purchases as purchases_router

app = FastAPI()
app.include_router(purchases_router.router)


@pytest.fixture
def api_setup(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    monkeypatch.setattr(purchases_router, "STORAGE_ROOT", storage_root)

    def override_get_db():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield factory, storage_root
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


async def send_request(method: str, url: str, **kwargs):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        return await client.request(method, url, **kwargs)


def request(method: str, url: str, **kwargs):
    return asyncio.run(send_request(method, url, **kwargs))


def add_purchase(
    factory,
    *,
    project_name: str,
    member_name: str,
    filename: str = "receipt.pdf",
    status: PurchaseStatus = PurchaseStatus.RECEIVED,
    created_at: datetime | None = None,
    local_path: str | None = None,
):
    with factory() as db:
        project = models.Project(name=project_name)
        member = models.Member(full_name=member_name)
        db.add_all([project, member])
        db.flush()
        purchase = models.Purchase(
            project_id=project.id,
            purchaser_id=member.id,
            purchase_date=date(2026, 10, 15),
            store_name="Sample Store",
            status=status,
            created_at=created_at or datetime.now(timezone.utc),
        )
        db.add(purchase)
        db.flush()
        receipt = models.ReceiptFile(
            purchase_id=purchase.id,
            original_filename=filename,
            mime_type="application/pdf",
            local_path=local_path or f"receipts/{project.id}/{purchase.id}/receipt.pdf",
            sha256="a" * 64,
        )
        db.add(receipt)
        db.commit()
        return purchase.id, project.id, receipt.id, receipt.local_path


def write_receipt(storage_root: Path, relative_path: str, content=b"%PDF-1.7\nreceipt"):
    file_path = storage_root / relative_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(content)
    return file_path


def test_purchase_listing_includes_associations_and_receipt_metadata(api_setup):
    factory, _ = api_setup
    purchase_id, project_id, receipt_id, _ = add_purchase(
        factory,
        project_name="Community Hall",
        member_name="Alex Member",
        filename="hall-receipt.pdf",
    )

    response = request("GET", "/api/v1/purchases")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["page"] == 1
    assert payload["page_size"] == 20
    assert payload["total_pages"] == 1
    purchase = payload["items"][0]
    assert purchase["id"] == purchase_id
    assert purchase["project_id"] == project_id
    assert purchase["project_name"] == "Community Hall"
    assert purchase["purchaser_name"] == "Alex Member"
    assert purchase["purchase_date"] == "2026-10-15"
    assert purchase["store_name"] == "Sample Store"
    assert purchase["status"] == "received"
    assert purchase["receipt_count"] == 1
    assert purchase["receipts"][0]["id"] == receipt_id
    assert purchase["receipts"][0]["original_filename"] == "hall-receipt.pdf"
    assert purchase["receipts"][0]["preview_url"].endswith(receipt_id)
    assert purchase["receipts"][0]["download_url"].endswith(f"{receipt_id}/download")


def test_purchase_pagination(api_setup):
    factory, _ = api_setup
    for index in range(3):
        add_purchase(
            factory,
            project_name=f"Project {index}",
            member_name=f"Member {index}",
            created_at=datetime(2026, 1, index + 1, tzinfo=timezone.utc),
        )

    response = request("GET", "/api/v1/purchases?page=2&page_size=2")

    assert response.status_code == 200
    assert len(response.json()["items"]) == 1
    assert response.json()["total"] == 3
    assert response.json()["page"] == 2
    assert response.json()["page_size"] == 2
    assert response.json()["total_pages"] == 2


def test_purchase_filters_by_project_and_status(api_setup):
    factory, _ = api_setup
    target_id, target_project, _, _ = add_purchase(
        factory,
        project_name="Target project",
        member_name="First",
        status=PurchaseStatus.RECEIVED,
    )
    add_purchase(
        factory,
        project_name="Other project",
        member_name="Second",
        status=PurchaseStatus.APPROVED,
    )

    project_response = request(
        "GET",
        f"/api/v1/purchases?project_id={target_project}",
    )
    status_response = request("GET", "/api/v1/purchases?status=approved")

    assert [item["id"] for item in project_response.json()["items"]] == [target_id]
    assert len(status_response.json()["items"]) == 1
    assert status_response.json()["items"][0]["status"] == "approved"


def test_purchase_search_matches_project_member_store_and_filename(api_setup):
    factory, _ = api_setup
    add_purchase(
        factory,
        project_name="Searchable project",
        member_name="Searchable person",
        filename="unique-receipt.pdf",
    )

    for term in ("searchable project", "searchable person", "sample store", "unique-receipt"):
        response = request("GET", "/api/v1/purchases", params={"search": term})
        assert response.status_code == 200
        assert response.json()["total"] == 1


def test_purchase_sorting_and_invalid_sort_inputs(api_setup):
    factory, _ = api_setup
    oldest, _, _, _ = add_purchase(
        factory,
        project_name="Alpha",
        member_name="Member A",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    newest, _, _, _ = add_purchase(
        factory,
        project_name="Beta",
        member_name="Member B",
        created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )

    descending = request("GET", "/api/v1/purchases?sort_by=created_at&sort_order=desc")
    ascending = request("GET", "/api/v1/purchases?sort_by=project&sort_order=asc")
    invalid_sort = request("GET", "/api/v1/purchases?sort_by=local_path")
    invalid_order = request("GET", "/api/v1/purchases?sort_order=random")

    assert [item["id"] for item in descending.json()["items"]] == [newest, oldest]
    assert [item["project_name"] for item in ascending.json()["items"]] == ["Alpha", "Beta"]
    assert invalid_sort.status_code == 422
    assert invalid_order.status_code == 422


def test_purchase_detail_and_stats(api_setup):
    factory, _ = api_setup
    purchase_id, _, _, _ = add_purchase(
        factory,
        project_name="Details project",
        member_name="Details member",
        status=PurchaseStatus.RECEIVED,
    )

    detail = request("GET", f"/api/v1/purchases/{purchase_id}")
    stats = request("GET", "/api/v1/purchases/stats")

    assert detail.status_code == 200
    assert detail.json()["id"] == purchase_id
    assert detail.json()["project_name"] == "Details project"
    assert detail.json()["purchaser_name"] == "Details member"
    assert stats.json()["total_purchases"] == 1
    assert stats.json()["awaiting_verification"] == 1
    assert stats.json()["status_counts"]["received"] == 1


def test_nonexistent_purchase_returns_404(api_setup):
    response = request("GET", "/api/v1/purchases/not-a-purchase")

    assert response.status_code == 404


@pytest.mark.parametrize(
    ("endpoint", "disposition"),
    [
        ("", "inline"),
        ("/download", "attachment"),
    ],
)
def test_receipt_preview_and_download_metadata(api_setup, endpoint, disposition):
    factory, storage_root = api_setup
    purchase_id, _, receipt_id, local_path = add_purchase(
        factory,
        project_name="Receipt project",
        member_name="Receipt member",
        filename='original "receipt".pdf',
    )
    content = b"%PDF-1.7\noriginal bytes"
    write_receipt(storage_root, local_path, content)

    response = request(
        "GET",
        f"/api/v1/purchases/{purchase_id}/receipts/{receipt_id}{endpoint}",
    )

    assert response.status_code == 200
    assert response.content == content
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"].startswith(disposition)
    assert "original" in response.headers["content-disposition"]
    assert str(storage_root.resolve()) not in response.headers["content-disposition"]


def test_image_receipt_preview_uses_image_content_type(api_setup):
    factory, storage_root = api_setup
    with factory() as db:
        project = models.Project(name="Image receipt project")
        member = models.Member(full_name="Image receipt member")
        db.add_all([project, member])
        db.flush()
        purchase = models.Purchase(
            project_id=project.id,
            purchaser_id=member.id,
            status=PurchaseStatus.RECEIVED,
        )
        db.add(purchase)
        db.flush()
        relative_path = f"receipts/{project.id}/{purchase.id}/photo.jpg"
        receipt = models.ReceiptFile(
            purchase_id=purchase.id,
            original_filename="photo.jpg",
            mime_type="image/jpeg",
            local_path=relative_path,
            sha256="b" * 64,
        )
        db.add(receipt)
        db.commit()
        purchase_id = purchase.id
        receipt_id = receipt.id
    content = b"\xff\xd8\xffjpeg-bytes"
    write_receipt(storage_root, relative_path, content)

    response = request(
        "GET",
        f"/api/v1/purchases/{purchase_id}/receipts/{receipt_id}",
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == content


def test_nonexistent_receipt_returns_404(api_setup):
    factory, _ = api_setup
    purchase_id, _, _, _ = add_purchase(
        factory,
        project_name="Missing receipt project",
        member_name="Missing receipt member",
    )

    response = request(
        "GET",
        f"/api/v1/purchases/{purchase_id}/receipts/missing",
    )

    assert response.status_code == 404


def test_receipt_for_another_purchase_returns_404(api_setup):
    factory, storage_root = api_setup
    first_purchase, _, first_receipt, _ = add_purchase(
        factory,
        project_name="First receipt project",
        member_name="First receipt member",
    )
    second_purchase, _, _, _ = add_purchase(
        factory,
        project_name="Second receipt project",
        member_name="Second receipt member",
    )
    response = request(
        "GET",
        f"/api/v1/purchases/{second_purchase}/receipts/{first_receipt}",
    )

    assert response.status_code == 404
    assert not any(storage_root.rglob("*.pdf"))
    assert first_purchase != second_purchase


def test_missing_physical_receipt_returns_404(api_setup):
    factory, _ = api_setup
    purchase_id, _, receipt_id, _ = add_purchase(
        factory,
        project_name="Deleted file project",
        member_name="Deleted file member",
    )

    response = request(
        "GET",
        f"/api/v1/purchases/{purchase_id}/receipts/{receipt_id}",
    )

    assert response.status_code == 404


@pytest.mark.parametrize("unsafe_path", ["../outside.pdf", "receipts/../../outside.pdf"])
def test_path_traversal_receipt_path_is_rejected(api_setup, unsafe_path):
    factory, storage_root = api_setup
    purchase_id, _, receipt_id, _ = add_purchase(
        factory,
        project_name=f"Traversal project {unsafe_path}",
        member_name="Traversal member",
        local_path=unsafe_path,
    )
    outside_file = storage_root.parent / "outside.pdf"
    outside_file.write_bytes(b"%PDF-1.7\nprivate")

    response = request(
        "GET",
        f"/api/v1/purchases/{purchase_id}/receipts/{receipt_id}",
    )

    assert response.status_code == 404
    assert outside_file.read_bytes() == b"%PDF-1.7\nprivate"

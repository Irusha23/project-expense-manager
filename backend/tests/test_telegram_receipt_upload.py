import asyncio
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.bot import handlers


class FakeBot:
    def __init__(
        self,
        content: bytes,
        *,
        download_error: Exception | None = None,
        reported_size: int | None = None,
    ):
        self.content = content
        self.download_error = download_error
        self.reported_size = reported_size
        self.get_file_calls = 0
        self.download_calls = 0

    async def get_file(self, file_id):
        self.get_file_calls += 1
        file_size = (
            self.reported_size
            if self.reported_size is not None
            else len(self.content)
        )
        return SimpleNamespace(file_path="telegram-file-path", file_size=file_size)

    async def download_file(self, file_path, destination):
        self.download_calls += 1
        if self.download_error is not None:
            raise self.download_error
        destination.write(self.content)
        return destination


class FakeMessage:
    def __init__(
        self,
        user_id: int,
        *,
        photo=None,
        document=None,
    ):
        self.from_user = SimpleNamespace(id=user_id)
        self.photo = photo or []
        self.document = document
        self.answers = []

    async def answer(self, text):
        self.answers.append(text)


@pytest.fixture
def db_factory(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(handlers, "SessionLocal", factory)
    monkeypatch.setattr(
        handlers,
        "RECEIPT_STORAGE_DIR",
        tmp_path / "storage" / "receipts",
    )
    try:
        yield factory
    finally:
        engine.dispose()


def add_member(db_factory, telegram_user_id: int, is_active: bool = True):
    with db_factory() as db:
        member = models.Member(
            full_name=f"Member {telegram_user_id}",
            telegram_user_id=telegram_user_id,
            is_active=is_active,
        )
        db.add(member)
        db.commit()
        return member.id


def add_project(db_factory, *, is_active: bool = True):
    with db_factory() as db:
        project = models.Project(name=f"Project {models.new_id()}", is_active=is_active)
        db.add(project)
        db.commit()
        return project.id, project.name


def add_purchase_session(
    db_factory,
    member_id: str,
    *,
    project_id: str | None,
    status: str = "awaiting_receipt",
):
    with db_factory() as db:
        purchase_session = models.PurchaseSession(
            member_id=member_id,
            project_id=project_id,
            status=status,
        )
        db.add(purchase_session)
        db.commit()
        return purchase_session.id


def photo_message(user_id: int, *, file_size: int | None = None):
    photo = SimpleNamespace(
        file_id="photo-file-id",
        file_unique_id="photo-unique-id",
        file_size=file_size,
    )
    return FakeMessage(user_id, photo=[photo])


def document_message(
    user_id: int,
    filename: str,
    mime_type: str,
    *,
    file_size: int | None = None,
):
    document = SimpleNamespace(
        file_id="document-file-id",
        file_unique_id="document-unique-id",
        file_name=filename,
        mime_type=mime_type,
        file_size=file_size,
    )
    return FakeMessage(user_id, document=document)


def submit(message, bot, update_id: int):
    asyncio.run(
        handlers.receipt_upload_handler(
            message,
            bot,
            SimpleNamespace(update_id=update_id),
        )
    )


def create_ready_member(db_factory, telegram_user_id: int, *, project_active=True):
    member_id = add_member(db_factory, telegram_user_id)
    project_id, project_name = add_project(db_factory, is_active=project_active)
    session_id = add_purchase_session(
        db_factory,
        member_id,
        project_id=project_id,
    )
    return member_id, project_id, project_name, session_id


def assert_saved_receipt(db_factory, content: bytes, session_id: str):
    with db_factory() as db:
        purchase = db.scalar(select(models.Purchase))
        receipt = db.scalar(select(models.ReceiptFile))
        purchase_session = db.get(models.PurchaseSession, session_id)
        assert purchase is not None
        assert receipt is not None
        assert purchase.status == models.PurchaseStatus.RECEIVED
        assert purchase.project_id
        assert purchase.purchaser_id
        assert purchase.source_telegram_update_id is not None
        assert purchase.receipt_sha256 == hashlib.sha256(content).hexdigest()
        assert receipt.purchase_id == purchase.id
        assert receipt.sha256 == hashlib.sha256(content).hexdigest()
        assert Path(receipt.local_path).is_absolute() is False
        assert purchase_session.status == "completed"
        stored_path = handlers.RECEIPT_STORAGE_DIR.parent / receipt.local_path
        assert stored_path.read_bytes() == content
        return purchase, receipt


def assert_no_receipt_artifacts(db_factory):
    with db_factory() as db:
        assert db.scalar(select(models.Purchase)) is None
        assert db.scalar(select(models.ReceiptFile)) is None
    storage_dir = handlers.RECEIPT_STORAGE_DIR
    assert not storage_dir.exists() or not any(
        path.is_file() for path in storage_dir.rglob("*")
    )


def receipt_file_count():
    storage_dir = handlers.RECEIPT_STORAGE_DIR
    return (
        sum(path.is_file() for path in storage_dir.rglob("*"))
        if storage_dir.exists()
        else 0
    )


def test_successful_photo_upload_creates_purchase_and_completes_session(db_factory):
    content = b"\xff\xd8\xff" + b"jpeg receipt bytes"
    _, project_id, project_name, session_id = create_ready_member(db_factory, 201)
    message = photo_message(201)
    bot = FakeBot(content)

    submit(message, bot, 9001)

    purchase, receipt = assert_saved_receipt(db_factory, content, session_id)
    assert purchase.project_id == project_id
    assert receipt.mime_type == "image/jpeg"
    assert receipt.original_filename == "receipt-photo.jpg"
    assert project_name in message.answers[0]
    assert "awaiting further processing or verification" in message.answers[0]
    with db_factory() as db:
        assert len(db.scalars(select(models.Purchase)).all()) == 1
        assert len(db.scalars(select(models.ReceiptFile)).all()) == 1
    assert receipt_file_count() == 1


def test_successful_pdf_upload_preserves_original_bytes(db_factory):
    content = b"%PDF-1.7\nreceipt contents"
    _, _, _, session_id = create_ready_member(db_factory, 202)
    message = document_message(202, r"..\..\march-receipt.pdf", "application/pdf")

    submit(message, FakeBot(content), 9002)

    _, receipt = assert_saved_receipt(db_factory, content, session_id)
    assert receipt.original_filename == "march-receipt.pdf"
    assert receipt.mime_type == "application/pdf"


def test_supported_name_with_unsupported_file_content_is_rejected(db_factory):
    create_ready_member(db_factory, 219)
    message = document_message(219, "receipt.jpg", "image/jpeg")

    submit(message, FakeBot(b"not an image"), 9019)

    assert "Unsupported file format" in message.answers[0]
    with db_factory() as db:
        assert db.scalar(select(models.Purchase)) is None


def test_supported_image_document_upload(db_factory):
    content = b"\x89PNG\r\n\x1a\n" + b"png receipt bytes"
    _, _, _, session_id = create_ready_member(db_factory, 203)
    message = document_message(203, "receipt.png", "image/png")

    submit(message, FakeBot(content), 9003)

    _, receipt = assert_saved_receipt(db_factory, content, session_id)
    assert receipt.mime_type == "image/png"


def test_unsupported_document_is_rejected_without_download(db_factory):
    create_ready_member(db_factory, 204)
    message = document_message(204, "receipt.exe", "application/octet-stream")
    bot = FakeBot(b"not a receipt")

    submit(message, bot, 9004)

    assert "Unsupported file format" in message.answers[0]
    assert bot.download_calls == 0


def test_oversized_upload_is_rejected_without_download(db_factory):
    create_ready_member(db_factory, 205)
    message = photo_message(
        205,
        file_size=handlers.settings.max_receipt_upload_size_bytes + 1,
    )
    bot = FakeBot(b"\xff\xd8\xffreceipt")

    submit(message, bot, 9005)

    assert "too large" in message.answers[0]
    assert bot.download_calls == 0


def test_downloaded_bytes_are_also_checked_against_size_limit(db_factory, monkeypatch):
    _, _, _, _ = create_ready_member(db_factory, 206)
    monkeypatch.setattr(handlers.settings, "max_receipt_upload_size_mb", 1)
    message = photo_message(206, file_size=1)
    bot = FakeBot(
        b"\xff\xd8\xff" + b"x" * (1024 * 1024),
        reported_size=1,
    )

    submit(message, bot, 9006)

    assert "too large" in message.answers[0]
    assert bot.download_calls == 1


def test_unregistered_member_is_rejected_before_download(db_factory):
    message = photo_message(207)
    bot = FakeBot(b"\xff\xd8\xffreceipt")

    submit(message, bot, 9007)

    assert "registered active member" in message.answers[0]
    assert bot.download_calls == 0


def test_inactive_member_is_rejected(db_factory):
    member_id = add_member(db_factory, 208, is_active=False)
    project_id, _ = add_project(db_factory)
    add_purchase_session(db_factory, member_id, project_id=project_id)
    message = photo_message(208)
    bot = FakeBot(b"\xff\xd8\xffreceipt")

    submit(message, bot, 9008)

    assert "registered active member" in message.answers[0]
    assert bot.download_calls == 0


def test_missing_purchase_session_is_rejected(db_factory):
    add_member(db_factory, 209)
    message = photo_message(209)
    bot = FakeBot(b"\xff\xd8\xffreceipt")

    submit(message, bot, 9009)

    assert "using /newpurchase" in message.answers[0]
    assert bot.get_file_calls == 0
    assert bot.download_calls == 0
    assert_no_receipt_artifacts(db_factory)


def test_session_not_awaiting_receipt_is_rejected(db_factory):
    member_id = add_member(db_factory, 210)
    project_id, _ = add_project(db_factory)
    add_purchase_session(
        db_factory,
        member_id,
        project_id=project_id,
        status="awaiting_project",
    )
    message = photo_message(210)
    bot = FakeBot(b"\xff\xd8\xffreceipt")

    submit(message, bot, 9010)

    assert "using /newpurchase" in message.answers[0]
    assert bot.download_calls == 0


def test_newer_unfinished_session_takes_precedence(db_factory):
    member_id = add_member(db_factory, 221)
    project_id, _ = add_project(db_factory)
    with db_factory() as db:
        db.add_all(
            [
                models.PurchaseSession(
                    member_id=member_id,
                    project_id=project_id,
                    status="awaiting_receipt",
                    created_at=models.utc_now() - timedelta(minutes=1),
                ),
                models.PurchaseSession(
                    member_id=member_id,
                    status="awaiting_project",
                    created_at=models.utc_now(),
                ),
            ]
        )
        db.commit()
    message = photo_message(221)
    bot = FakeBot(b"\xff\xd8\xffreceipt")

    submit(message, bot, 9021)

    assert "using /newpurchase" in message.answers[0]
    assert bot.download_calls == 0


@pytest.mark.parametrize("project_active", [False, None])
def test_invalid_or_inactive_project_is_rejected(db_factory, project_active):
    member_id = add_member(db_factory, 211)
    project_id = None
    if project_active is not None:
        project_id, _ = add_project(db_factory, is_active=project_active)
    add_purchase_session(db_factory, member_id, project_id=project_id)
    message = photo_message(211)
    bot = FakeBot(b"\xff\xd8\xffreceipt")

    submit(message, bot, 9011)

    assert "using /newpurchase" in message.answers[0]
    assert bot.download_calls == 0


def test_file_download_failure_keeps_session_available(db_factory):
    _, _, _, session_id = create_ready_member(db_factory, 212)
    message = photo_message(212)
    bot = FakeBot(b"", download_error=OSError("download unavailable"))

    submit(message, bot, 9012)

    assert "could not be downloaded" in message.answers[0]
    with db_factory() as db:
        assert db.get(models.PurchaseSession, session_id).status == "awaiting_receipt"
        assert db.scalar(select(models.Purchase)) is None


def test_storage_failure_keeps_session_available(db_factory, monkeypatch):
    _, _, _, session_id = create_ready_member(db_factory, 213)
    message = photo_message(213)

    def fail_store(*_args):
        raise OSError("disk unavailable")

    monkeypatch.setattr(handlers, "store_receipt", fail_store)
    submit(message, FakeBot(b"\xff\xd8\xffreceipt"), 9013)

    assert "could not be saved to storage" in message.answers[0]
    with db_factory() as db:
        assert db.get(models.PurchaseSession, session_id).status == "awaiting_receipt"
        assert db.scalar(select(models.Purchase)) is None


def test_database_failure_removes_file_and_keeps_session_available(db_factory):
    _, _, _, session_id = create_ready_member(db_factory, 214)
    message = photo_message(214)
    content = b"\xff\xd8\xffreceipt"

    def fail_receipt_insert(*_args):
        raise SQLAlchemyError("database unavailable")

    event.listen(models.ReceiptFile, "before_insert", fail_receipt_insert)
    try:
        submit(message, FakeBot(content), 9014)
    finally:
        event.remove(models.ReceiptFile, "before_insert", fail_receipt_insert)

    assert "could not be recorded" in message.answers[0]
    with db_factory() as db:
        assert db.get(models.PurchaseSession, session_id).status == "awaiting_receipt"
    assert_no_receipt_artifacts(db_factory)


def test_duplicate_telegram_update_does_not_create_another_purchase(db_factory):
    content = b"%PDF-1.4\nreceipt"
    _, _, _, session_id = create_ready_member(db_factory, 215)
    message = document_message(215, "receipt.pdf", "application/pdf")
    bot = FakeBot(content)

    submit(message, bot, 9015)
    submit(message, bot, 9015)

    assert "already been submitted" in message.answers[1]
    assert bot.download_calls == 1
    assert receipt_file_count() == 1
    with db_factory() as db:
        assert len(db.scalars(select(models.Purchase)).all()) == 1
        assert len(db.scalars(select(models.ReceiptFile)).all()) == 1
        assert db.get(models.PurchaseSession, session_id).status == "completed"


def test_member_cannot_submit_against_another_members_session(db_factory):
    owner_id = add_member(db_factory, 216)
    other_member_id = add_member(db_factory, 217)
    project_id, _ = add_project(db_factory)
    session_id = add_purchase_session(
        db_factory,
        owner_id,
        project_id=project_id,
    )
    message = photo_message(217)
    bot = FakeBot(b"\xff\xd8\xffreceipt")

    submit(message, bot, 9016)

    assert "using /newpurchase" in message.answers[0]
    assert bot.get_file_calls == 0
    assert bot.download_calls == 0
    with db_factory() as db:
        assert db.get(models.PurchaseSession, session_id).status == "awaiting_receipt"
    assert_no_receipt_artifacts(db_factory)
    assert owner_id != other_member_id


def test_completed_session_cannot_accept_another_receipt(db_factory):
    _, _, _, session_id = create_ready_member(db_factory, 218)
    first_message = photo_message(218)
    second_message = photo_message(218)
    content = b"\xff\xd8\xffreceipt"

    submit(first_message, FakeBot(content), 9017)
    initial_file_count = receipt_file_count()
    second_bot = FakeBot(content)
    submit(second_message, second_bot, 9018)

    assert "using /newpurchase" in second_message.answers[0]
    assert second_bot.get_file_calls == 0
    assert second_bot.download_calls == 0
    assert receipt_file_count() == initial_file_count == 1
    with db_factory() as db:
        assert len(db.scalars(select(models.Purchase)).all()) == 1
        assert db.get(models.PurchaseSession, session_id).status == "completed"


def test_concurrent_duplicate_updates_create_one_purchase(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'concurrent-receipts.db'}",
        connect_args={"timeout": 5},
    )
    models.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    storage_dir = tmp_path / "storage" / "receipts"
    monkeypatch.setattr(handlers, "SessionLocal", factory)
    monkeypatch.setattr(handlers, "RECEIPT_STORAGE_DIR", storage_dir)
    with factory() as db:
        member = models.Member(full_name="Concurrent member", telegram_user_id=220)
        project = models.Project(name="Concurrent receipt project")
        db.add_all([member, project])
        db.flush()
        purchase_session = models.PurchaseSession(
            member_id=member.id,
            project_id=project.id,
            status="awaiting_receipt",
        )
        db.add(purchase_session)
        db.commit()
        session_id = purchase_session.id

    content = b"%PDF-1.4\nconcurrent receipt"

    def submit_duplicate(_):
        message = document_message(220, "receipt.pdf", "application/pdf")
        submit(message, FakeBot(content), 9020)
        return message.answers

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(submit_duplicate, range(2)))

        assert all(len(answers) == 1 for answers in results)
        with factory() as db:
            assert len(db.scalars(select(models.Purchase)).all()) == 1
            assert len(db.scalars(select(models.ReceiptFile)).all()) == 1
            assert db.get(models.PurchaseSession, session_id).status == "completed"
        assert sum(path.is_file() for path in storage_dir.rglob("*")) == 1
    finally:
        engine.dispose()


def test_cancelled_upload_cleans_up_file_written_in_worker(db_factory, monkeypatch):
    _, _, _, session_id = create_ready_member(db_factory, 222)
    write_started = Event()
    allow_write_to_finish = Event()
    original_store = handlers.store_receipt

    def delayed_store(*args):
        write_started.set()
        if not allow_write_to_finish.wait(timeout=5):
            raise TimeoutError("test did not release receipt storage")
        return original_store(*args)

    monkeypatch.setattr(handlers, "store_receipt", delayed_store)
    message = photo_message(222)
    bot = FakeBot(b"\xff\xd8\xffreceipt")

    async def cancel_during_store():
        task = asyncio.create_task(
            handlers.receipt_upload_handler(
                message,
                bot,
                SimpleNamespace(update_id=9022),
            )
        )
        started = await asyncio.to_thread(write_started.wait, 5)
        assert started
        task.cancel()
        allow_write_to_finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_during_store())

    assert receipt_file_count() == 0
    with db_factory() as db:
        assert db.get(models.PurchaseSession, session_id).status == "awaiting_receipt"
        assert db.scalar(select(models.Purchase)) is None
        assert db.scalar(select(models.ReceiptFile)) is None

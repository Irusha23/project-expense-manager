
import asyncio
import hashlib
import json
import logging
import os
import re
import tempfile
import uuid
from io import BytesIO
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message, Update
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from app.bot.keyboard import project_selection_keyboard
from app.config import settings
from app.database import SessionLocal
from app.models import (
    AuditLog,
    Member,
    Project,
    Purchase,
    PurchaseSession,
    PurchaseStatus,
    ReceiptFile,
    utc_now,
)

router = Router()

logger = logging.getLogger(__name__)
CALLBACK_TOKEN_PATTERN = re.compile(r"[0-9a-f]{12}\Z")
RECEIPT_STORAGE_DIR = Path(settings.storage_root) / "receipts"
RECEIPT_DOCUMENT_TYPES = {
    ".jpg": ("image/jpeg", ".jpg"),
    ".jpeg": ("image/jpeg", ".jpg"),
    ".png": ("image/png", ".png"),
    ".webp": ("image/webp", ".webp"),
    ".pdf": ("application/pdf", ".pdf"),
}
RECEIPT_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "application/pdf",
}


def receipt_file_format(content: bytes) -> tuple[str, str] | None:
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp", ".webp"
    if content.startswith(b"%PDF-"):
        return "application/pdf", ".pdf"
    return None


def safe_original_filename(filename: str | None, fallback: str) -> str:
    basename = (filename or "").replace("\\", "/").rsplit("/", maxsplit=1)[-1]
    basename = "".join(character for character in basename if character.isprintable())
    return (basename.strip() or fallback)[:255]


def receipt_context(db, telegram_user_id: int, update_id: int):
    member = db.scalar(
        select(Member).where(
            Member.telegram_user_id == telegram_user_id,
            Member.is_active.is_(True),
        )
    )
    if member is None:
        return "unauthorized", None, None, None

    existing_purchase = db.scalar(
        select(Purchase).where(
            Purchase.source_telegram_update_id == update_id,
        )
    )
    if existing_purchase is not None:
        if existing_purchase.purchaser_id == member.id:
            return "duplicate", member.id, None, None
        logger.error("Telegram update ID is already associated with another member")
        return "duplicate_conflict", None, None, None

    purchase_session = db.scalar(
        select(PurchaseSession)
        .where(
            PurchaseSession.member_id == member.id,
            PurchaseSession.status.in_(["awaiting_project", "awaiting_receipt"]),
        )
        .order_by(PurchaseSession.created_at.desc())
    )
    if purchase_session is None or purchase_session.status != "awaiting_receipt":
        return "no_session", member.id, None, None

    if not purchase_session.project_id:
        return "invalid_project", member.id, None, None
    project = db.get(Project, purchase_session.project_id)
    if project is None or not project.is_active:
        return "invalid_project", member.id, None, None
    return "ready", member.id, (purchase_session.id, project.id), project.name


def store_receipt(content: bytes, project_id: str, purchase_id: str, extension: str):
    project_directory = str(uuid.UUID(project_id))
    purchase_directory = str(uuid.UUID(purchase_id))
    target_directory = RECEIPT_STORAGE_DIR / project_directory / purchase_directory
    target_directory.mkdir(parents=True, exist_ok=True)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".receipt-",
        suffix=".tmp",
        dir=target_directory,
    )
    temporary_path = Path(temporary_name)
    created_path = None
    try:
        with os.fdopen(descriptor, "wb") as temporary_file:
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        for _ in range(3):
            filename = f"{uuid.uuid4().hex}{extension}"
            final_path = target_directory / filename
            try:
                os.link(temporary_path, final_path)
            except FileExistsError:
                continue
            created_path = final_path
            temporary_path.unlink()
            relative_path = (
                Path("receipts") / project_directory / purchase_directory / filename
            ).as_posix()
            return final_path, relative_path
        raise FileExistsError("Could not allocate a unique receipt filename")
    except Exception:
        if created_path is not None:
            created_path.unlink(missing_ok=True)
        temporary_path.unlink(missing_ok=True)
        raise


def remove_stored_receipt(path: Path) -> None:
    path.unlink(missing_ok=True)


async def cleanup_stored_receipt(path: Path) -> None:
    try:
        await asyncio.to_thread(remove_stored_receipt, path)
    except OSError:
        logger.exception("Could not remove receipt file after failed persistence")


async def store_receipt_without_orphan(
    content: bytes,
    project_id: str,
    purchase_id: str,
    extension: str,
) -> tuple[Path, str]:
    storage_task = asyncio.create_task(
        asyncio.to_thread(
            store_receipt,
            content,
            project_id,
            purchase_id,
            extension,
        )
    )
    try:
        return await asyncio.shield(storage_task)
    except asyncio.CancelledError:
        try:
            stored_path, _ = await storage_task
        except (OSError, ValueError):
            logger.exception("Receipt storage failed while upload was cancelled")
        else:
            await asyncio.shield(cleanup_stored_receipt(stored_path))
        raise


def begin_write_transaction(db) -> None:
    if db.bind.dialect.name == "sqlite":
        db.execute(text("BEGIN IMMEDIATE"))


def parse_project_callback(data: str | None) -> tuple[str, str] | None:
    if not isinstance(data, str):
        return None

    parts = data.split(":")
    if (
        len(parts) != 3
        or parts[0] != "p"
        or CALLBACK_TOKEN_PATTERN.fullmatch(parts[1]) is None
        or not parts[2]
        or len(parts[2]) > 36
    ):
        return None

    return parts[1], parts[2]


def parse_cancel_callback(data: str | None) -> str | None:
    if not isinstance(data, str):
        return None

    parts = data.split(":")
    if (
        len(parts) != 2
        or parts[0] != "c"
        or CALLBACK_TOKEN_PATTERN.fullmatch(parts[1]) is None
    ):
        return None

    return parts[1]


def is_admin(message: Message) -> bool:
    """Check whether the sender is the configured administrator."""

    return (
        message.from_user is not None
        and settings.telegram_admin_id is not None
        and message.from_user.id == settings.telegram_admin_id
    )


def record_audit(
    db,
    actor: str,
    action: str,
    entity_id: str,
    details: dict,
):
    """Record an administrative action."""

    db.add(
        AuditLog(
            actor=actor,
            action=action,
            entity_type="member",
            entity_id=entity_id,
            details_json=json.dumps(details),
        )
    )


@router.message(CommandStart())
async def start_handler(message: Message):
    user = message.from_user

    if user is None:
        return

    if is_admin(message):
        await message.answer(
            "Welcome, Administrator! 👋\n\n"
            "Project Expense Manager is ready.\n\n"
            "Available commands:\n"
            "/myid - View your Telegram ID\n"
            "/register - Register a team member\n"
            "/members - List registered members\n"
            "/deactivate - Deactivate a member\n"
            "/activate - Reactivate a member\n"
            "/newpurchase - Start a purchase submission"
        )
        return

    with SessionLocal() as db:
        member = db.scalar(
            select(Member).where(
                Member.telegram_user_id == user.id
            )
        )

        if member and member.is_active:
            await message.answer(
                f"Welcome, {member.full_name}! 👋\n\n"
                "You are registered with Project Expense Manager.\n\n"
                "Use /newpurchase to submit a purchase receipt."
            )
            return

    await message.answer(
        "Hello! 👋\n\n"
        "You are not currently registered with "
        "Project Expense Manager.\n\n"
        "Please contact the project administrator "
        "to request access."
    )


@router.message(Command("myid"))
async def my_id_handler(message: Message):
    user = message.from_user

    if user is None:
        return

    await message.answer(
        "Your Telegram account information:\n\n"
        f"Name: {user.full_name}\n"
        f"Username: @{user.username or 'Not set'}\n"
        f"Telegram ID: `{user.id}`"
    )


@router.message(Command("register"))
async def register_handler(message: Message):

    if not is_admin(message):
        await message.answer("⛔ You are not authorized to do that.")
        return

    parts = (message.text or "").split(maxsplit=2)

    if len(parts) < 3:
        await message.answer(
            "Invalid command.\n\n"
            "Usage:\n"
            "/register TELEGRAM_ID FULL_NAME\n\n"
            "Example:\n"
            "/register 123456789 John Doe"
        )
        return

    try:
        telegram_id = int(parts[1])
        full_name = parts[2].strip()

        if telegram_id <= 0 or not full_name:
            raise ValueError

    except ValueError:
        await message.answer(
            "Invalid Telegram ID or member name."
        )
        return

    actor = str(message.from_user.id)

    with SessionLocal() as db:
        existing = db.scalar(
            select(Member).where(
                Member.telegram_user_id == telegram_id
            )
        )

        if existing:
            await message.answer(
                f"⚠️ This Telegram ID is already registered "
                f"to {existing.full_name}.\n\n"
                "Use /activate if the member is inactive."
            )
            return

        member = Member(
            full_name=full_name,
            telegram_user_id=telegram_id,
            is_active=True,
        )

        db.add(member)
        db.flush()

        record_audit(
            db=db,
            actor=actor,
            action="member_registered",
            entity_id=member.id,
            details={
                "full_name": full_name,
                "telegram_user_id": telegram_id,
            },
        )

        db.commit()

    await message.answer(
        "✅ Team member registered successfully!\n\n"
        f"Name: {full_name}\n"
        f"Telegram ID: {telegram_id}\n"
        "Status: Active"
    )


@router.message(Command("members"))
async def members_handler(message: Message):

    if not is_admin(message):
        await message.answer("⛔ You are not authorized to do that.")
        return

    with SessionLocal() as db:
        members = db.scalars(
            select(Member).order_by(Member.full_name.asc())
        ).all()

        if not members:
            await message.answer("No registered members found.")
            return

        lines = ["👥 Registered Team Members\n"]

        for member in members:
            status = "Active" if member.is_active else "Inactive"

            lines.append(
                f"• {member.full_name}\n"
                f"  ID: {member.telegram_user_id}\n"
                f"  Status: {status}\n"
            )

    await message.answer("\n".join(lines))


async def update_member_status(
    message: Message,
    telegram_id: int,
    active: bool,
):
    if not is_admin(message):
        await message.answer("⛔ You are not authorized to do that.")
        return

    with SessionLocal() as db:
        member = db.scalar(
            select(Member).where(
                Member.telegram_user_id == telegram_id
            )
        )

        if not member:
            await message.answer("Member not found.")
            return

        if member.is_active == active:
            await message.answer(
                "Member is already in that status."
            )
            return

        member.is_active = active

        record_audit(
            db=db,
            actor=str(message.from_user.id),
            action="member_activated" if active else "member_deactivated",
            entity_id=member.id,
            details={
                "telegram_user_id": telegram_id,
                "full_name": member.full_name,
            },
        )

        db.commit()

        name = member.full_name

    status = "activated" if active else "deactivated"

    await message.answer(
        f"✅ {name} has been {status}."
    )


@router.message(Command("deactivate"))
async def deactivate_handler(message: Message):

    parts = (message.text or "").split()

    if len(parts) != 2:
        await message.answer(
            "Usage: /deactivate TELEGRAM_ID"
        )
        return

    try:
        telegram_id = int(parts[1])
        if telegram_id <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Invalid Telegram ID.")
        return

    await update_member_status(
        message,
        telegram_id,
        active=False,
    )


@router.message(Command("activate"))
async def activate_handler(message: Message):

    parts = (message.text or "").split()

    if len(parts) != 2:
        await message.answer(
            "Usage: /activate TELEGRAM_ID"
        )
        return

    try:
        telegram_id = int(parts[1])
        if telegram_id <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Invalid Telegram ID.")
        return

    await update_member_status(
        message,
        telegram_id,
        active=True,
    )

@router.message(Command("newpurchase"))
async def new_purchase_handler(message: Message):
    user = message.from_user
    if user is None:
        return

    try:
        with SessionLocal() as db:
            begin_write_transaction(db)
            member = db.scalar(
                select(Member).where(
                    Member.telegram_user_id == user.id,
                    Member.is_active.is_(True),
                )
            )
            if member is None:
                result = "unauthorized"
            else:
                projects = db.scalars(
                    select(Project)
                    .where(Project.is_active.is_(True))
                    .order_by(Project.name.asc())
                ).all()

                if not projects:
                    result = "no_projects"
                else:
                    previous_sessions = db.scalars(
                        select(PurchaseSession).where(
                            PurchaseSession.member_id == member.id,
                            PurchaseSession.status.in_(
                                ["awaiting_project", "awaiting_receipt"]
                            ),
                        )
                    ).all()
                    for previous_session in previous_sessions:
                        previous_session.status = "cancelled"
                        previous_session.updated_at = utc_now()

                    session = PurchaseSession(
                        member_id=member.id,
                        status="awaiting_project",
                    )
                    db.add(session)
                    db.flush()
                    keyboard = project_selection_keyboard(
                        projects,
                        session.callback_token,
                    )
                    db.commit()
                    result = "started"
    except (SQLAlchemyError, ValueError):
        logger.exception("Could not start Telegram purchase session")
        await message.answer(
            "The purchase submission could not be started. Please try again later."
        )
        return

    if result == "unauthorized":
        await message.answer(
            "⛔ You are not authorized to submit purchases.\n\n"
            "Please contact the project administrator."
        )
    elif result == "no_projects":
        await message.answer(
            "No active projects are currently available.\n\n"
            "Please contact the administrator."
        )
    else:
        await message.answer(
            "🧾 Let's record a new purchase!\n\n"
            "Which project does this purchase belong to?\n\n"
            "Select a project below:",
            reply_markup=keyboard,
        )


@router.callback_query(F.data.startswith("p:"))
async def project_selected_handler(callback: CallbackQuery):
    user = callback.from_user
    parsed_callback = parse_project_callback(callback.data)

    if user is None or parsed_callback is None:
        await callback.answer("Invalid selection.", show_alert=True)
        return

    callback_token, project_id = parsed_callback
    result = "expired"
    project_name = None

    try:
        with SessionLocal() as db:
            begin_write_transaction(db)
            member = db.scalar(
                select(Member).where(
                    Member.telegram_user_id == user.id,
                    Member.is_active.is_(True),
                )
            )
            if member is None:
                result = "unauthorized"
            else:
                session = db.scalar(
                    select(PurchaseSession).where(
                        PurchaseSession.callback_token == callback_token,
                        PurchaseSession.member_id == member.id,
                        PurchaseSession.status == "awaiting_project",
                    )
                )
                if session is not None:
                    project = db.scalar(
                        select(Project).where(
                            Project.id == project_id,
                            Project.is_active.is_(True),
                        )
                    )
                    if project is None:
                        result = "inactive_project"
                    else:
                        session.project_id = project.id
                        session.status = "awaiting_receipt"
                        session.updated_at = utc_now()
                        project_name = project.name
                        db.commit()
                        result = "selected"
    except SQLAlchemyError:
        logger.exception("Could not process Telegram project selection")
        await callback.answer(
            "The selection could not be saved. Please try again later.",
            show_alert=True,
        )
        return

    if result == "unauthorized":
        await callback.answer("You are not authorized.", show_alert=True)
        return
    if result == "inactive_project":
        await callback.answer(
            "This project is no longer available.",
            show_alert=True,
        )
        return
    if result != "selected":
        await callback.answer(
            "This submission has expired. Start again.",
            show_alert=True,
        )
        return

    await callback.answer("Project selected!")
    if isinstance(callback.message, Message):
        try:
            await callback.message.edit_text(
                f"✅ Project selected: {project_name}\n\n"
                "Now send your purchase receipt as a "
                "photograph or PDF.\n\n"
                "Your selection has been saved."
            )
        except TelegramAPIError:
            logger.exception("Could not edit Telegram project selection message")


@router.callback_query(F.data.startswith("c:"))
async def cancel_purchase_handler(callback: CallbackQuery):
    user = callback.from_user
    callback_token = parse_cancel_callback(callback.data)
    if user is None or callback_token is None:
        await callback.answer("Invalid request.", show_alert=True)
        return

    result = "inactive"
    try:
        with SessionLocal() as db:
            begin_write_transaction(db)
            member = db.scalar(
                select(Member).where(
                    Member.telegram_user_id == user.id,
                    Member.is_active.is_(True),
                )
            )
            if member is None:
                result = "unknown_member"
            else:
                session = db.scalar(
                    select(PurchaseSession).where(
                        PurchaseSession.callback_token == callback_token,
                        PurchaseSession.member_id == member.id,
                        PurchaseSession.status.in_(
                            ["awaiting_project", "awaiting_receipt"]
                        ),
                    )
                )
                if session is not None:
                    session.status = "cancelled"
                    session.updated_at = utc_now()
                    db.commit()
                    result = "cancelled"
    except SQLAlchemyError:
        logger.exception("Could not cancel Telegram purchase session")
        await callback.answer(
            "The submission could not be cancelled. Please try again later.",
            show_alert=True,
        )
        return

    if result == "unknown_member":
        await callback.answer("You are not authorized.", show_alert=True)
        return
    if result != "cancelled":
        await callback.answer(
            "This submission is no longer active.",
            show_alert=True,
        )
        return

    await callback.answer("Submission cancelled.")
    if isinstance(callback.message, Message):
        try:
            await callback.message.edit_text(
                "❌ Purchase submission cancelled.\n\n"
                "Use /newpurchase to start again."
            )
        except TelegramAPIError:
            logger.exception("Could not edit Telegram cancellation message")


@router.message(F.photo | F.document)
async def receipt_upload_handler(
    message: Message,
    bot: Bot,
    event_update: Update,
):
    user = message.from_user
    if user is None:
        return

    if message.photo:
        telegram_file = message.photo[-1]
        candidate_name = "receipt-photo.jpg"
        declared_mime = None
        unsupported_document = False
    elif message.document is not None:
        telegram_file = message.document
        candidate_name = telegram_file.file_name
        declared_mime = telegram_file.mime_type
        extension = Path((candidate_name or "").replace("\\", "/")).suffix.lower()
        unsupported_document = (
            extension not in RECEIPT_DOCUMENT_TYPES
            and declared_mime not in RECEIPT_MIME_TYPES
        )
    else:
        return

    update_id = event_update.update_id
    try:
        with SessionLocal() as db:
            result, _, session_data, project_name = receipt_context(
                db,
                user.id,
                update_id,
            )
    except SQLAlchemyError:
        logger.exception("Could not validate Telegram receipt submission")
        await message.answer(
            "We could not check your purchase session. Please try again later."
        )
        return

    if result != "ready":
        await respond_to_receipt_context(message, result)
        return

    if unsupported_document:
        await message.answer(
            "Unsupported file format. Please send a JPEG, PNG, WEBP image, or PDF."
        )
        return

    if (
        telegram_file.file_size is not None
        and telegram_file.file_size > settings.max_receipt_upload_size_bytes
    ):
        await message.answer(
            f"This file is too large. The maximum receipt size is "
            f"{settings.max_receipt_upload_size_mb} MB."
        )
        return

    try:
        remote_file = await bot.get_file(telegram_file.file_id)
        if not remote_file.file_path:
            raise OSError("Telegram returned no file path")
        if (
            remote_file.file_size is not None
            and remote_file.file_size > settings.max_receipt_upload_size_bytes
        ):
            await message.answer(
                f"This file is too large. The maximum receipt size is "
                f"{settings.max_receipt_upload_size_mb} MB."
            )
            return

        downloaded = await bot.download_file(
            remote_file.file_path,
            destination=BytesIO(),
        )
        content = downloaded.getvalue() if downloaded is not None else b""
    except (TelegramAPIError, OSError):
        logger.exception("Could not download Telegram receipt")
        await message.answer(
            "The receipt could not be downloaded. Please try sending it again."
        )
        return

    if len(content) > settings.max_receipt_upload_size_bytes:
        await message.answer(
            f"This file is too large. The maximum receipt size is "
            f"{settings.max_receipt_upload_size_mb} MB."
        )
        return

    detected_format = await asyncio.to_thread(receipt_file_format, content)
    if detected_format is None:
        await message.answer(
            "Unsupported file format. Please send a JPEG, PNG, WEBP image, or PDF."
        )
        return
    mime_type, extension = detected_format
    original_filename = safe_original_filename(
        candidate_name,
        f"receipt{extension}",
    )
    checksum = await asyncio.to_thread(lambda: hashlib.sha256(content).hexdigest())

    stored_path = None
    try:
        with SessionLocal() as db:
            begin_write_transaction(db)
            result, member_id, session_data, project_name = receipt_context(
                db,
                user.id,
                update_id,
            )
            if result == "ready":
                session_id, project_id = session_data
                purchase = Purchase(
                    project_id=project_id,
                    purchaser_id=member_id,
                    status=PurchaseStatus.RECEIVED,
                    source_telegram_update_id=update_id,
                    receipt_sha256=checksum,
                )
                db.add(purchase)
                db.flush()
                stored_path, relative_path = await store_receipt_without_orphan(
                    content,
                    project_id,
                    purchase.id,
                    extension,
                )
                db.add(
                    ReceiptFile(
                        purchase_id=purchase.id,
                        original_filename=original_filename,
                        mime_type=mime_type,
                        local_path=relative_path,
                        sha256=checksum,
                    )
                )
                purchase_session = db.get(PurchaseSession, session_id)
                purchase_session.status = "completed"
                purchase_session.updated_at = utc_now()
                db.commit()
    except (OSError, ValueError):
        logger.exception("Could not store Telegram receipt")
        if stored_path is not None:
            await cleanup_stored_receipt(stored_path)
        await message.answer(
            "The receipt could not be saved to storage. Please try again later."
        )
        return
    except SQLAlchemyError:
        logger.exception("Could not persist Telegram receipt records")
        if stored_path is not None:
            await cleanup_stored_receipt(stored_path)
        await message.answer(
            "The receipt could not be recorded. Your purchase session is still available; "
            "please try again later."
        )
        return

    if result == "duplicate":
        await message.answer(
            "This receipt has already been submitted successfully. "
            "Use /newpurchase to start another purchase."
        )
        return
    if result != "ready":
        await respond_to_receipt_context(message, result)
        return

    await message.answer(
        "✅ Receipt submitted successfully!\n\n"
        f"Project: {project_name}\n"
        f"Receipt: {original_filename}\n\n"
        "Your receipt has been saved and is awaiting further processing or verification.\n"
        "Use /newpurchase to submit another purchase."
    )


async def respond_to_receipt_context(message: Message, result: str) -> None:
    if result == "unauthorized":
        text = (
            "⛔ You must be a registered active member to submit a receipt. "
            "Please contact the project administrator."
        )
    elif result in {"no_session", "invalid_project"}:
        text = (
            "There is no valid active purchase session for this receipt. "
            "Please start again using /newpurchase."
        )
    elif result == "duplicate":
        text = (
            "This receipt has already been submitted successfully. "
            "Use /newpurchase to start another purchase."
        )
    else:
        text = "This receipt could not be processed. Please start again using /newpurchase."
    await message.answer(text)


@router.callback_query()
async def unknown_callback_handler(callback: CallbackQuery):
    await callback.answer("This action is invalid or has expired.", show_alert=True)
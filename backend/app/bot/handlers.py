
import json
import logging

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import Member, AuditLog

router = Router()

logger = logging.getLogger(__name__)


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
            "/activate - Reactivate a member"
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
                "You are registered with Project Expense Manager.\n"
                "You can now use the expense submission service."
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

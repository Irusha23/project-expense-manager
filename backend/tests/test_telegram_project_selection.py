import asyncio
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from aiogram.types import Chat, Message
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.bot import handlers
from app.bot.keyboard import project_selection_keyboard


class FakeUser:
    def __init__(self, user_id: int):
        self.id = user_id


class FakeMessage:
    def __init__(self, user_id: int):
        self.from_user = FakeUser(user_id)
        self.answers = []

    async def answer(self, text: str, reply_markup=None):
        self.answers.append((text, reply_markup))


class FakeCallback:
    def __init__(self, user_id: int, data: str, message=None):
        self.from_user = FakeUser(user_id)
        self.data = data
        self.message = message
        self.answers = []

    async def answer(self, text: str | None = None, show_alert: bool = False):
        self.answers.append((text, show_alert))


@pytest.fixture
def db_factory(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(handlers, "SessionLocal", factory)
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


def add_project(db_factory, name: str, is_active: bool = True):
    with db_factory() as db:
        project = models.Project(name=name, is_active=is_active)
        db.add(project)
        db.commit()
        return project.id


def add_purchase_session(
    db_factory,
    member_id: str,
    status: str = "awaiting_project",
):
    with db_factory() as db:
        session = models.PurchaseSession(member_id=member_id, status=status)
        db.add(session)
        db.commit()
        return session.id, session.callback_token


def get_purchase_session(db_factory, session_id: str):
    with db_factory() as db:
        return db.scalar(
            select(models.PurchaseSession).where(
                models.PurchaseSession.id == session_id
            )
        )


def make_telegram_message():
    return Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat(id=1, type="private"),
    )


def test_keyboard_callbacks_are_within_telegram_limit():
    project = models.Project(id="a" * 36, name="Long ID project")

    keyboard = project_selection_keyboard([project], "a1b2c3d4e5f6")
    callback_data = [
        row[0].callback_data for row in keyboard.inline_keyboard
    ]

    assert len(callback_data) == 2
    assert callback_data[0] == f"p:a1b2c3d4e5f6:{'a' * 36}"
    assert callback_data[1] == "c:a1b2c3d4e5f6"
    assert all(len(value.encode("utf-8")) <= 64 for value in callback_data)


def test_session_callback_tokens_are_unique(db_factory):
    member_id = add_member(db_factory, 101)
    with db_factory() as db:
        sessions = [
            models.PurchaseSession(member_id=member_id)
            for _ in range(50)
        ]
        db.add_all(sessions)
        db.commit()

    tokens = [session.callback_token for session in sessions]
    assert all(re.fullmatch(r"[0-9a-f]{12}", token) for token in tokens)
    assert len(tokens) == len(set(tokens))
    callback_indexes = inspect(db_factory.kw["bind"]).get_indexes(
        "purchase_sessions"
    )
    assert any(
        index["unique"] and index["column_names"] == ["callback_token"]
        for index in callback_indexes
    )


def test_registered_member_can_start_purchase_and_get_dynamic_projects(db_factory):
    add_member(db_factory, 102)
    add_project(db_factory, "Active project")
    add_project(db_factory, "Inactive project", is_active=False)
    message = FakeMessage(102)

    asyncio.run(handlers.new_purchase_handler(message))

    assert len(message.answers) == 1
    keyboard = message.answers[0][1]
    assert keyboard is not None
    project_callbacks = [
        row[0].callback_data
        for row in keyboard.inline_keyboard[:-1]
    ]
    assert len(project_callbacks) == 1
    assert project_callbacks[0].startswith("p:")
    with db_factory() as db:
        session = db.scalar(select(models.PurchaseSession))
        assert session is not None
        assert session.status == "awaiting_project"
        assert session.callback_token in keyboard.inline_keyboard[-1][0].callback_data


def test_unregistered_member_cannot_start_purchase(db_factory):
    add_project(db_factory, "Active project")
    message = FakeMessage(103)

    asyncio.run(handlers.new_purchase_handler(message))

    assert "not authorized" in message.answers[0][0]
    with db_factory() as db:
        assert db.scalar(select(models.PurchaseSession)) is None


def test_deactivated_member_cannot_start_or_proceed(db_factory):
    member_id = add_member(db_factory, 104, is_active=False)
    project_id = add_project(db_factory, "Active project")
    session_id, token = add_purchase_session(db_factory, member_id)

    message = FakeMessage(104)
    asyncio.run(handlers.new_purchase_handler(message))
    callback = FakeCallback(104, f"p:{token}:{project_id}")
    asyncio.run(handlers.project_selected_handler(callback))

    assert "not authorized" in message.answers[0][0]
    assert callback.answers == [("You are not authorized.", True)]
    assert get_purchase_session(db_factory, session_id).status == "awaiting_project"


def test_active_project_selection_updates_the_correct_session(
    db_factory,
    monkeypatch,
):
    member_id = add_member(db_factory, 105)
    project_id = add_project(db_factory, "Selected project")
    session_id, token = add_purchase_session(db_factory, member_id)
    edited = []

    async def edit_text(_message, text):
        edited.append(text)

    monkeypatch.setattr(Message, "edit_text", edit_text)
    callback = FakeCallback(
        105,
        f"p:{token}:{project_id}",
        message=make_telegram_message(),
    )

    asyncio.run(handlers.project_selected_handler(callback))

    updated = get_purchase_session(db_factory, session_id)
    assert updated.project_id == project_id
    assert updated.status == "awaiting_receipt"
    assert updated.updated_at > updated.created_at
    assert callback.answers == [("Project selected!", False)]
    assert edited and "Selected project" in edited[0]


def test_inactive_project_cannot_be_selected(db_factory):
    member_id = add_member(db_factory, 106)
    project_id = add_project(db_factory, "Inactive project", is_active=False)
    session_id, token = add_purchase_session(db_factory, member_id)
    callback = FakeCallback(106, f"p:{token}:{project_id}")

    asyncio.run(handlers.project_selected_handler(callback))

    assert callback.answers == [("This project is no longer available.", True)]
    unchanged = get_purchase_session(db_factory, session_id)
    assert unchanged.status == "awaiting_project"
    assert unchanged.project_id is None


def test_old_keyboard_cannot_modify_newer_session(db_factory):
    add_member(db_factory, 107)
    project_id = add_project(db_factory, "Active project")
    message = FakeMessage(107)
    asyncio.run(handlers.new_purchase_handler(message))
    asyncio.run(handlers.new_purchase_handler(message))

    old_token = message.answers[0][1].inline_keyboard[0][0].callback_data.split(":")[1]
    new_token = message.answers[1][1].inline_keyboard[0][0].callback_data.split(":")[1]
    with db_factory() as db:
        old_session = db.scalar(
            select(models.PurchaseSession).where(
                models.PurchaseSession.callback_token == old_token
            )
        )
        new_session = db.scalar(
            select(models.PurchaseSession).where(
                models.PurchaseSession.callback_token == new_token
            )
        )
        old_session_id = old_session.id
        new_session_id = new_session.id

    callback = FakeCallback(
        107,
        f"p:{old_token}:{project_id}",
    )
    asyncio.run(handlers.project_selected_handler(callback))

    assert callback.answers[0][1] is True
    assert get_purchase_session(db_factory, old_session_id).status == "cancelled"
    assert get_purchase_session(db_factory, new_session_id).status == "awaiting_project"


def test_concurrent_project_callbacks_only_advance_session_once(
    tmp_path,
    monkeypatch,
):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'concurrent.db'}",
        connect_args={"timeout": 5},
    )
    models.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(handlers, "SessionLocal", factory)
    with factory() as db:
        member = models.Member(full_name="Concurrent member", telegram_user_id=112)
        project = models.Project(name="Concurrent project")
        db.add_all([member, project])
        db.flush()
        purchase_session = models.PurchaseSession(member_id=member.id)
        db.add(purchase_session)
        db.commit()
        callback_data = f"p:{purchase_session.callback_token}:{project.id}"
        session_id = purchase_session.id

    def select_project(_):
        callback = FakeCallback(112, callback_data)
        asyncio.run(handlers.project_selected_handler(callback))
        return callback.answers[0]

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(select_project, range(2)))

        assert sorted(answer for answer, _ in results) == [
            "Project selected!",
            "This submission has expired. Start again.",
        ]
        with factory() as db:
            session = db.get(models.PurchaseSession, session_id)
            assert session.status == "awaiting_receipt"
    finally:
        engine.dispose()


def test_concurrent_purchase_starts_leave_only_one_active_session(
    tmp_path,
    monkeypatch,
):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'concurrent-start.db'}",
        connect_args={"timeout": 5},
    )
    models.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(handlers, "SessionLocal", factory)
    with factory() as db:
        db.add_all(
            [
                models.Member(full_name="Concurrent member", telegram_user_id=114),
                models.Project(name="Concurrent project"),
            ]
        )
        db.commit()

    def start_purchase(_):
        message = FakeMessage(114)
        asyncio.run(handlers.new_purchase_handler(message))
        return message.answers

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(start_purchase, range(2)))

        assert all(len(result) == 1 and result[0][1] is not None for result in results)
        with factory() as db:
            sessions = db.scalars(select(models.PurchaseSession)).all()
            assert len(sessions) == 2
            assert sum(
                session.status in {"awaiting_project", "awaiting_receipt"}
                for session in sessions
            ) == 1
            assert sum(session.status == "cancelled" for session in sessions) == 1
    finally:
        engine.dispose()


@pytest.mark.parametrize("status", ["awaiting_project", "awaiting_receipt"])
def test_cancellation_works_for_active_session_statuses(
    db_factory,
    status,
    monkeypatch,
):
    member_id = add_member(db_factory, 108)
    session_id, token = add_purchase_session(db_factory, member_id, status)
    edited = []

    async def edit_text(_message, text):
        edited.append(text)

    monkeypatch.setattr(Message, "edit_text", edit_text)
    callback = FakeCallback(
        108,
        f"c:{token}",
        message=make_telegram_message(),
    )

    asyncio.run(handlers.cancel_purchase_handler(callback))

    cancelled = get_purchase_session(db_factory, session_id)
    assert cancelled.status == "cancelled"
    assert cancelled.updated_at > cancelled.created_at
    assert callback.answers == [("Submission cancelled.", False)]
    assert edited and "cancelled" in edited[0].lower()


def test_deactivated_member_cannot_cancel_purchase(db_factory):
    member_id = add_member(db_factory, 113, is_active=False)
    session_id, token = add_purchase_session(db_factory, member_id)
    callback = FakeCallback(113, f"c:{token}")

    asyncio.run(handlers.cancel_purchase_handler(callback))

    assert callback.answers == [("You are not authorized.", True)]
    assert get_purchase_session(db_factory, session_id).status == "awaiting_project"


def test_member_cannot_select_or_cancel_another_members_session(db_factory):
    owner_id = add_member(db_factory, 109)
    other_member_id = add_member(db_factory, 110)
    project_id = add_project(db_factory, "Active project")
    session_id, token = add_purchase_session(db_factory, owner_id)

    selection = FakeCallback(110, f"p:{token}:{project_id}")
    cancellation = FakeCallback(110, f"c:{token}")
    asyncio.run(handlers.project_selected_handler(selection))
    asyncio.run(handlers.cancel_purchase_handler(cancellation))

    assert selection.answers[0][1] is True
    assert cancellation.answers[0][1] is True
    assert get_purchase_session(db_factory, session_id).status == "awaiting_project"
    assert other_member_id != owner_id


@pytest.mark.parametrize(
    ("handler", "data"),
    [
        (handlers.project_selected_handler, "p:short"),
        (handlers.project_selected_handler, "p:123456789012:"),
        (handlers.project_selected_handler, "project:legacy:format"),
        (handlers.cancel_purchase_handler, "c:invalid"),
        (handlers.cancel_purchase_handler, "c:123456789012:extra"),
    ],
)
def test_malformed_callback_data_is_acknowledged_safely(handler, data):
    callback = FakeCallback(111, data)

    asyncio.run(handler(callback))

    assert len(callback.answers) == 1
    assert callback.answers[0][1] is True

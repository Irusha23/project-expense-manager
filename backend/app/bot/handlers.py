from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

router = Router()


@router.message(CommandStart())
async def start_handler(message: Message):

    await message.answer(
        "Welcome to Project Expense Manager! 👋\n\n"
        "I'm your project expense assistant.\n\n"
        "You will be able to:\n"
        "• Submit purchase receipts\n"
        "• Select the relevant project\n"
        "• Track expense submissions\n\n"
        "Let's make project expense management easier!"
    )
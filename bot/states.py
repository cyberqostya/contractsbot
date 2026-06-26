from aiogram.fsm.state import State, StatesGroup


class BloggerFlow(StatesGroup):
    choosing_status = State()
    filling_requisites = State()
    confirming_requisites = State()
    waiting_signed_contract = State()
    waiting_invoice = State()
    waiting_payment = State()

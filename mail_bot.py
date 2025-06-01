import logging
import hashlib
import json
import os
import threading
import tkinter as tk
from tkinter import messagebox
from PIL import Image, ImageDraw
import pystray
import asyncio
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = "7610325862:AAHeDDr6w6is-vn1ycucCYM5ObExm3-TgrM"
MASTER_PASSWORD = "Wtt9631"
MASTER_PASSWORD_HASH = hashlib.sha256(MASTER_PASSWORD.encode()).hexdigest()

EMAILS_FILE = "emails.json"
USER_STATES_FILE = "user_states.json"
MAILLOG_FILE = "mailslog.json"

user_emails = {"next_id": 1, "entries": {}}
mail_log = {}
user_states = {}

ASK_EMAIL, ASK_PASSWORD, ASK_2FA, ASK_TAGS = range(4)
SELECT_EMAIL_TO_EDIT, SELECT_FIELD_TO_EDIT, GET_NEW_EMAIL_VALUE, GET_NEW_PASSWORD_VALUE, GET_NEW_2FA_VALUE, GET_NEW_TAGS_VALUE = range(4, 10)

telegram_application: Application = None
bot_thread: threading.Thread = None
tray_icon: pystray.Icon = None
root_window: tk.Tk = None

EMAILS_PER_PAGE = 5
ITEMS_PER_SELECTION_PAGE = 5
MAX_LINE_LENGTH = 40

def truncate_string(text: str, max_length: int) -> str:
    return (text[:max_length] + '...') if len(text) > max_length else text

def load_emails():
    global user_emails
    if os.path.exists(EMAILS_FILE):
        with open(EMAILS_FILE, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
                user_emails["next_id"] = data.get("next_id", 1)
                user_emails["entries"] = {int(k): v for k, v in data.get("entries", {}).items()}
            except (json.JSONDecodeError, ValueError):
                user_emails = {"next_id": 1, "entries": {}}
    else:
        user_emails = {"next_id": 1, "entries": {}}

def save_emails():
    with open(EMAILS_FILE, 'w', encoding='utf-8') as f:
        serializable_data = {
            "next_id": user_emails["next_id"],
            "entries": {str(k): v for k, v in user_emails["entries"].items()}
        }
        json.dump(serializable_data, f, indent=4, ensure_ascii=False)

def load_mail_log():
    global mail_log
    if os.path.exists(MAILLOG_FILE):
        with open(MAILLOG_FILE, 'r', encoding='utf-8') as f:
            try:
                mail_log = json.load(f)
                mail_log = {int(k) if k.isdigit() else k: v for k, v in mail_log.items()}
            except (json.JSONDecodeError, ValueError):
                mail_log = {}
    else:
        mail_log = {}

def save_mail_log():
    with open(MAILLOG_FILE, 'w', encoding='utf-8') as f:
        serializable_log = {str(k): v for k, v in mail_log.items()}
        json.dump(serializable_log, f, indent=4, ensure_ascii=False)

def log_email_activity(original_entry_id: int, action: str, current_data: dict, old_value=None, new_value=None):
    timestamp = datetime.now().isoformat()
    log_entry = {
        "timestamp": timestamp,
        "action": action,
        "current_data": current_data.copy()
    }
    if old_value is not None:
        log_entry["old_value"] = old_value
    if new_value is not None:
        log_entry["new_value"] = new_value

    if original_entry_id not in mail_log:
        mail_log[original_entry_id] = []
    mail_log[original_entry_id].append(log_entry)
    save_mail_log()

def load_user_authentication_states():
    global user_states
    if os.path.exists(USER_STATES_FILE):
        with open(USER_STATES_FILE, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
                user_states = {int(k): v for k, v in data.items()}
            except (json.JSONDecodeError, ValueError):
                user_states = {}
    else:
        user_states = {}

def save_user_authentication_states():
    serializable_states = {str(chat_id): state for chat_id, state in user_states.items() if state == "AUTHENTICATED"}
    with open(USER_STATES_FILE, 'w', encoding='utf-8') as f:
        json.dump(serializable_states, f, indent=4, ensure_ascii=False)

def get_user_state(chat_id: int) -> str | None:
    return user_states.get(chat_id)

def set_user_state(chat_id: int, state: str | None):
    if state is None and chat_id in user_states:
        del user_states[chat_id]
    elif state:
        user_states[chat_id] = state
    save_user_authentication_states()

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if get_user_state(chat_id) == "AUTHENTICATED":
        await update.message.reply_text("You are already authenticated. Use /mail to manage emails.")
    else:
        set_user_state(chat_id, "PENDING_PASSWORD")
        await update.message.reply_text("Welcome! Please enter your master password:")

async def mail_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    state = get_user_state(chat_id)

    if state == "AUTHENTICATED":
        context.user_data['current_mail_page'] = 0
        await send_mail_page(update, context, chat_id, 0)
    elif state == "PENDING_PASSWORD":
        await update.message.reply_text("Please enter your password first via /start.")
    else:
        await update.message.reply_text("Your email list is empty. Nothing to display.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    text = update.message.text
    state = get_user_state(chat_id)

    if state == "PENDING_PASSWORD":
        entered_password_hash = hashlib.sha256(text.encode()).hexdigest()
        if entered_password_hash == MASTER_PASSWORD_HASH:
            set_user_state(chat_id, "AUTHENTICATED")
        else:
            set_user_state(chat_id, "NOT_AUTHENTICATED")
        await update.message.reply_text("Password accepted. You are now authenticated. Use /mail to manage emails.")

async def send_mail_page(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, page: int):
    gmail_filter_active = context.user_data.get('gmail_filter_active', False)
    
    if gmail_filter_active:
        display_entries = [
            (entry_id, entry) for entry_id, entry in user_emails["entries"].items()
            if entry.get('email', '').lower().endswith('@gmail.com')
        ]
    else:
        display_entries = list(user_emails["entries"].items())

    total_emails = len(display_entries)
    total_pages = (total_emails + EMAILS_PER_PAGE - 1) // EMAILS_PER_PAGE if total_emails > 0 else 1
    page = max(0, min(page, total_pages - 1)) 

    start_index = page * EMAILS_PER_PAGE
    end_index = min(start_index + EMAILS_PER_PAGE, total_emails)

    if not display_entries:
        message_text = "No emails saved"
        if gmail_filter_active:
            message_text = "No Gmail emails found."
        else:
            message_text = "No emails saved."
    else:
        message_text = f"Your emails (Page {page + 1}/{total_pages})"
        if gmail_filter_active:
            message_text += " [Gmail Only]"
        message_text += ":\n\n"
        
        for i in range(start_index, end_index):
            entry_id, entry = display_entries[i]
            tags_info = f"Tags: {truncate_string(entry.get('tags', 'N/A'), MAX_LINE_LENGTH)}\n" if entry.get('tags') else ""
            message_text += (
                f"ID: {entry_id}\n"
                f"Email: {truncate_string(entry['email'], MAX_LINE_LENGTH)}\n"
                f"Password: {truncate_string(entry['password'], MAX_LINE_LENGTH)}\n"
                f"2FA Info: {truncate_string(entry.get('2fa', 'N/A'), MAX_LINE_LENGTH)}\n"
                f"{tags_info}\n"
            )

    keyboard = []
    if total_pages > 1:
        navigation_row = []
        if page > 0:
            navigation_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"mail_page_{page - 1}"))
        if page < total_pages - 1:
            navigation_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"mail_page_{page + 1}"))
        if navigation_row:
            keyboard.append(navigation_row)

    action_buttons = [InlineKeyboardButton("Add Email", callback_data="add_mail_start")]
    if user_emails["entries"]:
        action_buttons.append(InlineKeyboardButton("Delete Email", callback_data="delete_mail_start_0"))
        action_buttons.append(InlineKeyboardButton("Edit Data", callback_data="edit_mail_start_0"))
    keyboard.append(action_buttons)
    
    filter_button_text = "Filter: Gmail" if gmail_filter_active else "Filter: All"
    keyboard.append([InlineKeyboardButton(filter_button_text, callback_data="toggle_gmail_filter")])


    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        if update.callback_query:
            await update.callback_query.edit_message_text(message_text, reply_markup=reply_markup, parse_mode='HTML')
        else:
            await update.message.reply_text(message_text, reply_markup=reply_markup, parse_mode='HTML')
    except Exception as e:
        logger.error(f"Error sending mail page: {e}")
        error_message_target = update.callback_query.message if update.callback_query else update.message
        await error_message_target.reply_text("An error occurred displaying emails. Please try again or check logs.")

async def paginate_mail_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id

    if get_user_state(chat_id) != "AUTHENTICATED":
        await query.edit_message_text("Access denied. Please authenticate via /start.")
        return

    try:
        page = int(query.data.split('_')[-1])
        context.user_data['current_mail_page'] = page
        await send_mail_page(update, context, chat_id, page)
    except (ValueError, IndexError):
        await query.edit_message_text("Error: Invalid page navigation.")

async def toggle_gmail_filter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id

    if get_user_state(chat_id) != "AUTHENTICATED":
        await query.edit_message_text("Access denied. Please authenticate via /start.")
        return

    current_filter_state = context.user_data.get('gmail_filter_active', False)
    context.user_data['gmail_filter_active'] = not current_filter_state
    context.user_data['current_mail_page'] = 0

    await send_mail_page(update, context, chat_id, 0)


async def add_mail_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id

    if get_user_state(chat_id) != "AUTHENTICATED":
        await query.edit_message_text("Access denied.")
        return ConversationHandler.END

    keyboard = [[InlineKeyboardButton("Cancel", callback_data="mail_page_0")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Enter email address:", reply_markup=reply_markup)
    return ASK_EMAIL

async def ask_email_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['new_email_email'] = update.message.text
    await update.message.reply_text("Enter password for this email:")
    return ASK_PASSWORD

async def ask_password_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['new_email_password'] = update.message.text
    await update.message.reply_text("Enter 2FA information (or 'none' if not applicable):")
    return ASK_2FA

async def ask_2fa_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['new_email_2fa'] = update.message.text if update.message.text.lower() != 'none' else ""
    await update.message.reply_text("Enter Tags (e.g., 'work, personal' or 'none'):")
    return ASK_TAGS

async def ask_tags_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    tags_info = update.message.text if update.message.text.lower() != 'none' else ""

    entry_id = user_emails["next_id"]
    new_entry_data = {
        "email": context.user_data['new_email_email'],
        "password": context.user_data['new_email_password'],
        "2fa": context.user_data['new_email_2fa'],
        "tags": tags_info,
    }
    user_emails["entries"][entry_id] = new_entry_data
    user_emails["next_id"] += 1
    save_emails()
    log_email_activity(entry_id, "added", new_entry_data)

    await update.message.reply_text("Email successfully added!")
    for key in ['new_email_email', 'new_email_password', 'new_email_2fa']:
        if key in context.user_data:
            del context.user_data[key]
    
    current_page = context.user_data.get('current_mail_page', 0)
    await send_mail_page(update, context, chat_id, current_page)
    return ConversationHandler.END

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message_target = update.message if update.message else update.callback_query.message
    chat_id = message_target.chat_id
    
    text_to_send = "Operation cancelled."
    if update.callback_query:
        await update.callback_query.edit_message_text(text_to_send)
    else:
        await message_target.reply_text(text_to_send)

    for key in ['new_email_email', 'new_email_password', 'new_email_2fa',
                  'entry_id_to_edit', 'editing_field', 'current_edit_page',
                  'current_delete_page']:
            del context.user_data[key]

    current_page = context.user_data.get('current_mail_page', 0)

    class DummyUpdate:
        def __init__(self, msg):
            self.effective_chat = msg.chat
            self.message = msg 
            self.callback_query = None 

    await send_mail_page(DummyUpdate(message_target), context, chat_id, current_page)
    return ConversationHandler.END

async def delete_mail_start_paginated(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id

    if get_user_state(chat_id) != "AUTHENTICATED":
        await query.edit_message_text("Access denied.")
        return

    if not user_emails["entries"]:
        main_page_back = context.user_data.get('current_mail_page',0)
        await query.edit_message_text("No emails to delete.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to List", callback_data=f"mail_page_{main_page_back}")]]))
        return

    try:
        page = int(query.data.split('_')[-1])
        context.user_data['current_delete_page'] = page
    except (ValueError, IndexError):
        page = 0 
        context.user_data['current_delete_page'] = page

    await send_delete_selection_page(update, context, chat_id, page)

async def send_delete_selection_page(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, page: int):
    all_emails_for_selection = list(user_emails["entries"].items())
    total_emails = len(all_emails_for_selection)
    total_pages = (total_emails + ITEMS_PER_SELECTION_PAGE - 1) // ITEMS_PER_SELECTION_PAGE if total_emails > 0 else 1
    page = max(0, min(page, total_pages - 1))

    start_index = page * ITEMS_PER_SELECTION_PAGE
    end_index = min(start_index + ITEMS_PER_SELECTION_PAGE, total_emails)

    message_text = f"Select email to delete (Page {page + 1}/{total_pages}):"
    keyboard = []

    if not all_emails_for_selection:
        message_text = "No emails available for deletion."
    else:
        for i in range(start_index, end_index):
            entry_id, entry = all_emails_for_selection[i]
            keyboard.append([
                InlineKeyboardButton(f"{truncate_string(entry['email'], MAX_LINE_LENGTH - 10)} (ID: {entry_id})", callback_data=f"delete_confirm_{entry_id}")
            ])

    navigation_row = []
    if page > 0:
        navigation_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"delete_mail_start_{page - 1}"))
    if page < total_pages - 1:
        navigation_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"delete_mail_start_{page + 1}"))
    if navigation_row:
        keyboard.append(navigation_row)

    main_list_page = context.user_data.get('current_mail_page', 0)
    keyboard.append([InlineKeyboardButton("⬅️ Back to List", callback_data=f"mail_page_{main_list_page}")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    query = update.callback_query
    await query.edit_message_text(message_text, reply_markup=reply_markup)


async def delete_mail_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id

    if get_user_state(chat_id) != "AUTHENTICATED":
        await query.edit_message_text("Access denied.")
        return

    try:
        entry_id_to_delete = int(query.data.split("_")[-1])
    except (IndexError, ValueError):
        await query.edit_message_text("Error: Invalid ID for deletion.")
        current_page = context.user_data.get('current_mail_page', 0)
        await send_mail_page(update, context, chat_id, current_page)
        return

    if entry_id_to_delete in user_emails["entries"]:
        del user_emails["entries"][entry_id_to_delete]
        save_emails()
        await query.edit_message_text(f"Email with ID {entry_id_to_delete} deleted from active list.")
    else:
        await query.edit_message_text(f"Error: Email with ID {entry_id_to_delete} not found.")

    current_main_page = context.user_data.get('current_mail_page', 0)
    if 'current_delete_page' in context.user_data: del context.user_data['current_delete_page']
    await send_mail_page(update, context, chat_id, current_main_page)


async def edit_mail_start_paginated(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id

    if get_user_state(chat_id) != "AUTHENTICATED":
        await query.edit_message_text("Access denied.")
        return ConversationHandler.END

    if not user_emails["entries"]:
        main_page_back = context.user_data.get('current_mail_page',0)
        await query.edit_message_text("No emails to edit.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to List", callback_data=f"mail_page_{main_page_back}")]]))
        return ConversationHandler.END
    
    try:
        page = int(query.data.split('_')[-1])
        context.user_data['current_edit_page'] = page
    except (ValueError, IndexError):
        page = 0
        context.user_data['current_edit_page'] = page

    await send_edit_selection_page(update, context, chat_id, page)
    return SELECT_EMAIL_TO_EDIT

async def send_edit_selection_page(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, page: int):
    all_emails_for_selection = list(user_emails["entries"].items())
    total_emails = len(all_emails_for_selection)
    total_pages = (total_emails + ITEMS_PER_SELECTION_PAGE - 1) // ITEMS_PER_SELECTION_PAGE if total_emails > 0 else 1
    page = max(0, min(page, total_pages - 1))

    message_text = f"Select email to edit (Page {page + 1}/{total_pages}):"
    keyboard = []
    
    if not all_emails_for_selection:
        message_text = "No emails available for editing."
    else:
        for i in range(page * ITEMS_PER_SELECTION_PAGE, min((page + 1) * ITEMS_PER_SELECTION_PAGE, total_emails)):
            entry_id, entry = all_emails_for_selection[i]
            keyboard.append([
                InlineKeyboardButton(f"{truncate_string(entry['email'], MAX_LINE_LENGTH - 10)} (ID: {entry_id})", callback_data=f"edit_select_{entry_id}")
            ])
    
    navigation_row = []
    if page > 0:
        navigation_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"edit_mail_start_{page - 1}"))
    if page < total_pages - 1:
        navigation_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"edit_mail_start_{page + 1}"))
    if navigation_row:
        keyboard.append(navigation_row)
    
    main_list_page = context.user_data.get('current_mail_page', 0)
    keyboard.append([InlineKeyboardButton("⬅️ Back to List", callback_data=f"mail_page_{main_list_page}")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    query = update.callback_query
    await query.edit_message_text(message_text, reply_markup=reply_markup)

async def select_email_to_edit_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id

    if get_user_state(chat_id) != "AUTHENTICATED":
        await query.edit_message_text("Access denied.")
        return ConversationHandler.END

    try:
        entry_id_to_edit = int(query.data.split('_')[-1])
        if entry_id_to_edit not in user_emails["entries"]:
            raise ValueError("Entry ID not found")
        context.user_data['entry_id_to_edit'] = entry_id_to_edit
    except (ValueError, IndexError):
        await query.edit_message_text("Invalid selection. Please try again.")
        current_page = context.user_data.get('current_edit_page', 0)
        await send_edit_selection_page(update, context, chat_id, current_page)
        return SELECT_EMAIL_TO_EDIT

    entry_data = user_emails["entries"][entry_id_to_edit]
    message_text = f"Editing Email ID: {entry_id_to_edit}\n"
    message_text += f"Current Email: {entry_data['email']}\n"
    message_text += f"Current Pass: {truncate_string(entry_data['password'], 15)}\n"
    message_text += f"Current 2FA: {truncate_string(entry_data.get('2fa','N/A'), 15)}\n"
    message_text += f"Current Tags: {truncate_string(entry_data.get('tags','N/A'), 15)}\n\n"
    message_text += "What do you want to edit?"

    keyboard = [
        [InlineKeyboardButton("Email Address", callback_data=f"edit_field_email")],
        [InlineKeyboardButton("Password", callback_data=f"edit_field_password")],
        [InlineKeyboardButton("2FA Info", callback_data=f"edit_field_2fa")],
        [InlineKeyboardButton("Tags", callback_data=f"edit_field_tags")],
        [InlineKeyboardButton("⬅️ Back to Select Email", callback_data=f"edit_mail_start_{context.user_data.get('current_edit_page',0)}")],
        [InlineKeyboardButton("Cancel", callback_data="cancel_edit_op")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(message_text, reply_markup=reply_markup)
    return SELECT_FIELD_TO_EDIT

async def select_field_to_edit_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    field_to_edit = query.data.split('_')[-1]
    context.user_data['editing_field'] = field_to_edit
    
    prompt_text = ""
    next_state = -1

    if field_to_edit == "email":
        prompt_text = "Enter the new email address:"
        next_state = GET_NEW_EMAIL_VALUE
    elif field_to_edit == "password":
        prompt_text = "Enter the new password:"
        next_state = GET_NEW_PASSWORD_VALUE
    elif field_to_edit == "2fa":
        prompt_text = "Enter the new 2FA information (or 'none'):"
        next_state = GET_NEW_2FA_VALUE
    elif field_to_edit == "tags":
        prompt_text = "Enter the new tags (e.g., 'work, important' or 'none'):"
        next_state = GET_NEW_TAGS_VALUE
    else:
        await query.edit_message_text("Invalid field selection.")
        return SELECT_FIELD_TO_EDIT

    await query.edit_message_text(prompt_text)
    return next_state

async def get_new_value_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    new_value = update.message.text
    entry_id = context.user_data.get('entry_id_to_edit')
    editing_field = context.user_data.get('editing_field')

    if not entry_id or not editing_field:
        await update.message.reply_text("Error: Editing context lost. Please start over.")
        current_page = context.user_data.get('current_mail_page', 0)
        await send_mail_page(update, context, chat_id, current_page)
        return ConversationHandler.END

    original_entry = user_emails["entries"][entry_id]
    old_field_value = original_entry.get(editing_field, "") 

    if editing_field in ["2fa", "tags"] and new_value.lower() == 'none':
        new_value = ""

    user_emails["entries"][entry_id][editing_field] = new_value
    save_emails()
    
    log_email_activity(
        original_entry_id=entry_id,
        action=f"edited_{editing_field}",
        current_data=user_emails["entries"][entry_id],
        old_value=old_field_value,
        new_value=new_value
    )

    await update.message.reply_text(f"{editing_field.capitalize()} updated successfully for ID {entry_id}!")

    for key in ['entry_id_to_edit', 'editing_field', 'current_edit_page']:
        if key in context.user_data: del context.user_data[key]

    current_page = context.user_data.get('current_mail_page', 0)
    await send_mail_page(update, context, chat_id, current_page)
    return ConversationHandler.END

async def cancel_edit_op_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id

    await query.edit_message_text("Edit operation cancelled.")
    
    for key in ['entry_id_to_edit', 'editing_field', 'current_edit_page']:
        if key in context.user_data:
            del context.user_data[key]

    current_page = context.user_data.get('current_mail_page', 0)
    await send_mail_page(update, context, chat_id, current_page)
    return ConversationHandler.END

def run_bot():
    global telegram_application
    load_emails()
    load_user_authentication_states()
    load_mail_log()

    telegram_application = Application.builder().token(BOT_TOKEN).build()

    add_mail_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_mail_start_callback, pattern=r"^add_mail_start$")],
        states={
            ASK_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_email_received)],
            ASK_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_password_received)],
            ASK_2FA: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_2fa_received)],
            ASK_TAGS: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_tags_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
        allow_reentry=True
    )

    edit_mail_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(edit_mail_start_paginated, pattern=r"^edit_mail_start_\d+$")],
        states={
            SELECT_EMAIL_TO_EDIT: [
                CallbackQueryHandler(select_email_to_edit_handler, pattern=r"^edit_select_\d+$"),
                CallbackQueryHandler(edit_mail_start_paginated, pattern=r"^edit_mail_start_\d+$"), 
                CallbackQueryHandler(cancel_edit_op_callback, pattern="^cancel_edit_op$"),
                CallbackQueryHandler(paginate_mail_callback, pattern=r"^mail_page_\d+$")
            ],
            SELECT_FIELD_TO_EDIT: [
                CallbackQueryHandler(select_field_to_edit_handler, pattern=r"^edit_field_(email|password|2fa|tags)$"),
                CallbackQueryHandler(edit_mail_start_paginated, pattern=r"^edit_mail_start_\d+$"), 
                CallbackQueryHandler(cancel_edit_op_callback, pattern="^cancel_edit_op$")
            ],
            GET_NEW_EMAIL_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_new_value_handler)],
            GET_NEW_PASSWORD_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_new_value_handler)],
            GET_NEW_2FA_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_new_value_handler)],
            GET_NEW_TAGS_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_new_value_handler)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation), CallbackQueryHandler(cancel_edit_op_callback, pattern="^cancel_edit_op$")],
        allow_reentry=True
    )

    telegram_application.add_handler(CommandHandler("start", start_command))
    telegram_application.add_handler(CommandHandler("mail", mail_command))
    telegram_application.add_handler(CallbackQueryHandler(paginate_mail_callback, pattern=r"^mail_page_\d+$"))
    telegram_application.add_handler(CallbackQueryHandler(toggle_gmail_filter, pattern=r"^toggle_gmail_filter$"))

    telegram_application.add_handler(add_mail_conv_handler)
    telegram_application.add_handler(edit_mail_conv_handler)

    telegram_application.add_handler(CallbackQueryHandler(delete_mail_start_paginated, pattern=r"^delete_mail_start_\d+$"))
    telegram_application.add_handler(CallbackQueryHandler(delete_mail_confirm, pattern=r"^delete_confirm_\d+$"))

    telegram_application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        logger.info("Bot polling started.")
        telegram_application.run_polling(drop_pending_updates=True)
    except Exception as e:
        logger.error(f"Critical error in run_polling: {e}", exc_info=True)
    finally:
        logger.info("Bot polling stopped or encountered an error.")
        if telegram_application and telegram_application.running:
             asyncio.run(telegram_application.stop())


def start_bot_action():
    global bot_thread, root_window, status_label
    if bot_thread and bot_thread.is_alive():
        messagebox.showinfo("Telegram Bot", "Bot is already running!")
        return

    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    if root_window and status_label: status_label.config(text="Bot Status: Running")
    messagebox.showinfo("Telegram Bot", "Bot started successfully!")

def stop_bot_action():
    global telegram_application, bot_thread, root_window, status_label
    logger.info("Stop bot action initiated.")
    if telegram_application :
        if telegram_application.running:
            logger.info("Application is running, attempting to stop.")
            async def _stop_application():
                await telegram_application.stop()
                await telegram_application.shutdown()

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            
            try:
                loop.run_until_complete(_stop_application())
            except Exception as e:
                logger.error(f"Exception during bot stop: {e}")

        else:
            logger.info("Application was initialized but not running.")
        telegram_application = None
    else:
        logger.info("Telegram application not initialized.")


    if bot_thread and bot_thread.is_alive():
        logger.info("Joining bot thread...")
        bot_thread.join(timeout=10)
        if bot_thread.is_alive():
            logger.warning("Bot thread did not terminate in time.")
    bot_thread = None

    if root_window and status_label: status_label.config(text="Bot Status: Stopped")
    messagebox.showinfo("Telegram Bot", "Bot has been stopped (or was not running).")
    logger.info("Stop bot action completed.")


def hide_window_to_tray():
    if root_window: root_window.withdraw()
    if tray_icon:
        tray_icon.visible = True

def show_window_from_tray(icon, item):
    if icon: icon.visible = False
    if root_window: root_window.deiconify()

def quit_application(icon, item):
    if icon: icon.stop()
    stop_bot_action() 
    if root_window: root_window.quit()


def create_tray_icon():
    global tray_icon
    try:
        image = Image.new('RGB', (64, 64), color = '#40E0D0')
    except Exception as e:
        logger.error(f"Could not create tray icon image: {e}")
        image = None 

    menu = (pystray.MenuItem('Show', show_window_from_tray, default=True),
            pystray.MenuItem('Quit', quit_application))

    tray_icon = pystray.Icon("TelegramBot", image, "Telegram Bot Control", menu)
    tray_icon.run_detached()


def setup_gui():
    global root_window, status_label
    root_window = tk.Tk()
    root_window.title("Telegram Bot Control")
    root_window.geometry("350x250")

    status_label = tk.Label(root_window, text="Bot Status: Stopped", font=("Arial", 12))
    status_label.pack(pady=20)

    start_button = tk.Button(root_window, text="Start Bot", command=start_bot_action, width=15, height=2)
    start_button.pack(pady=5)

    stop_button = tk.Button(root_window, text="Stop Bot", command=stop_bot_action, width=15, height=2)
    stop_button.pack(pady=5)

    hide_button = tk.Button(root_window, text="Hide to Tray", command=hide_window_to_tray, width=15, height=2)
    hide_button.pack(pady=10)

    root_window.protocol("WM_DELETE_WINDOW", hide_window_to_tray)

    create_tray_icon()
    root_window.mainloop()


if __name__ == "__main__":
    setup_gui()

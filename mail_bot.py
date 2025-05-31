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

BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"
MASTER_PASSWORD = "YOUR_MASTER_PASSWORD_HERE"
MASTER_PASSWORD_HASH = hashlib.sha256(MASTER_PASSWORD.encode()).hexdigest()

EMAILS_FILE = "emails.json"
USER_STATES_FILE = "user_states.json"

user_emails = {"next_id": 1, "entries": {}}

user_states = {}

ASK_EMAIL, ASK_PASSWORD, ASK_2FA, ASK_TAGS = range(4)

telegram_application: Application = None
bot_thread: threading.Thread = None
tray_icon: pystray.Icon = None
root_window: tk.Tk = None

EMAILS_PER_PAGE = 5
MAX_LINE_LENGTH = 40

def load_emails():
    global user_emails
    if os.path.exists(EMAILS_FILE):
        with open(EMAILS_FILE, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
                user_emails["next_id"] = data.get("next_id", 1)
                user_emails["entries"] = {int(k): v for k, v in data.get("entries", {}).items()}
            except json.JSONDecodeError as e:
                user_emails = {"next_id": 1, "entries": {}}
            except ValueError as e:
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

def load_user_authentication_states():
    global user_states
    if os.path.exists(USER_STATES_FILE):
        with open(USER_STATES_FILE, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
                user_states = {int(k): v for k, v in data.items()}
            except json.JSONDecodeError as e:
                user_states = {}
            except ValueError as e:
                user_states = {}
    else:
        user_states = {}

def save_user_authentication_states():
    serializable_states = {str(chat_id): state for chat_id, state in user_states.items() if state == "AUTHENTICATED"}
    with open(USER_STATES_FILE, 'w', encoding='utf-8') as f:
        json.dump(serializable_states, f, indent=4, ensure_ascii=False)

def get_user_state(chat_id: int) -> str | None:
    return user_states.get(chat_id)

def set_user_state(chat_id: int, state: str):
    user_states[chat_id] = state
    if state == "AUTHENTICATED":
        save_user_authentication_states()
    else:
        if chat_id in user_states and user_states.get(chat_id) == "AUTHENTICATED":
            save_user_authentication_states()

def truncate_string(text: str, max_length: int) -> str:
    return (text[:max_length] + '...') if len(text) > max_length else text

async def send_mail_page(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int, page: int):
    all_emails = list(user_emails["entries"].items())
    total_emails = len(all_emails)
    total_pages = (total_emails + EMAILS_PER_PAGE - 1) // EMAILS_PER_PAGE

    start_index = page * EMAILS_PER_PAGE
    end_index = min(start_index + EMAILS_PER_PAGE, total_emails)

    if not all_emails:
        message_text = "No emails saved."
    else:
        message_text = f"Your emails (Page {page + 1}/{total_pages}):\n\n"
        for i in range(start_index, end_index):
            entry_id, entry = all_emails[i]
            tags_info = f"Tags: {truncate_string(entry.get('tags', 'N/A'), MAX_LINE_LENGTH)}\n" if 'tags' in entry else ""
            message_text += (
                f"ID: {entry_id}\n"
                f"Email: {truncate_string(entry['email'], MAX_LINE_LENGTH)}\n"
                f"Password: {truncate_string(entry['password'], MAX_LINE_LENGTH)}\n"
                f"2FA Info: {truncate_string(entry['2fa'], MAX_LINE_LENGTH)}\n"
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

    keyboard.append([InlineKeyboardButton("Add Email", callback_data="add_mail_start")])
    if all_emails:
        keyboard.append([InlineKeyboardButton("Delete Email", callback_data="delete_mail_start")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.edit_message_text(message_text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(message_text, reply_markup=reply_markup)

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
        await update.message.reply_text("Access denied. Please enter your password first.")
    elif state == "PSEUDO_AUTHENTICATED":
        await update.message.reply_text("No emails saved.")
    else:
        await update.message.reply_text("Access denied. Please authenticate first via /start.")

async def paginate_mail_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    
    if get_user_state(chat_id) != "AUTHENTICATED":
        await query.edit_message_text("Access denied.")
        return

    try:
        page = int(query.data.split('_')[-1])
        context.user_data['current_mail_page'] = page
        await send_mail_page(update, context, chat_id, page)
    except (ValueError, IndexError):
        await query.edit_message_text("Error: Invalid page navigation.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    text = update.message.text
    state = get_user_state(chat_id)

    if state == "PENDING_PASSWORD":
        entered_password_hash = hashlib.sha256(text.encode()).hexdigest()
        
        await update.message.reply_text("Password accepted.") 

        if entered_password_hash == MASTER_PASSWORD_HASH:
            set_user_state(chat_id, "AUTHENTICATED")
        else:
            set_user_state(chat_id, "PSEUDO_AUTHENTICATED")

async def add_mail_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id

    if get_user_state(chat_id) != "AUTHENTICATED":
        await query.edit_message_text("Access denied.")
        return ConversationHandler.END

    await query.edit_message_text("Enter email address:")
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
    context.user_data['new_email_2fa'] = update.message.text
    await update.message.reply_text("Enter Tags (e.g., 'work, personal, projectX' or 'none'):")
    return ASK_TAGS

async def ask_tags_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    tags_info = update.message.text if update.message.text.lower() != 'none' else ""

    entry_id = user_emails["next_id"]
    user_emails["entries"][entry_id] = {
        "email": context.user_data['new_email_email'],
        "password": context.user_data['new_email_password'],
        "2fa": context.user_data['new_email_2fa'],
        "tags": tags_info,
    }
    user_emails["next_id"] += 1
    save_emails()

    await update.message.reply_text("Email successfully added!")
    del context.user_data['new_email_email']
    del context.user_data['new_email_password']
    del context.user_data['new_email_2fa']
    return ConversationHandler.END

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Operation cancelled.")
    if 'new_email_email' in context.user_data: del context.user_data['new_email_email']
    if 'new_email_password' in context.user_data: del context.user_data['new_email_password']
    if 'new_email_2fa' in context.user_data: del context.user_data['new_email_2fa']
    return ConversationHandler.END

async def delete_mail_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id

    if get_user_state(chat_id) != "AUTHENTICATED":
        await query.edit_message_text("Access denied.")
        return

    if not user_emails["entries"]:
        await query.edit_message_text("No emails to delete.")
        return

    keyboard = []
    for entry_id, entry in user_emails["entries"].items():
        keyboard.append([
            InlineKeyboardButton(f"Delete: {truncate_string(entry['email'], MAX_LINE_LENGTH)} (ID: {entry_id})", callback_data=f"delete_confirm_{entry_id}")
        ])
    
    if not keyboard:
        await query.edit_message_text("No emails to delete.")
        return

    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Select email to delete:", reply_markup=reply_markup)

async def delete_mail_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id

    if get_user_state(chat_id) != "AUTHENTICATED":
        await query.edit_message_text("Access denied.")
        return

    try:
        entry_id_to_delete = int(query.data.split("_")[2])
    except (IndexError, ValueError):
        await query.edit_message_text("Error: Invalid ID for deletion.")
        return

    if entry_id_to_delete in user_emails["entries"]:
        del user_emails["entries"][entry_id_to_delete]
        save_emails()
        await query.edit_message_text(f"Email with ID {entry_id_to_delete} deleted.")
    else:
        await query.edit_message_text(f"Error: Email with ID {entry_id_to_delete} not found.")

def run_bot():
    global telegram_application
    load_emails()
    load_user_authentication_states()

    telegram_application = Application.builder().token(BOT_TOKEN).build()

    add_mail_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_mail_start, pattern=r"^add_mail_start$")],
        states={
            ASK_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_email_received)],
            ASK_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_password_received)],
            ASK_2FA: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_2fa_received)],
            ASK_TAGS: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_tags_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
        allow_reentry=True
    )

    telegram_application.add_handler(CommandHandler("start", start_command))
    telegram_application.add_handler(CommandHandler("mail", mail_command))
    telegram_application.add_handler(CallbackQueryHandler(paginate_mail_callback, pattern=r"^mail_page_\d+$"))
    telegram_application.add_handler(add_mail_conv_handler)
    telegram_application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    telegram_application.add_handler(CallbackQueryHandler(delete_mail_start, pattern=r"^delete_mail_start$"))
    telegram_application.add_handler(CallbackQueryHandler(delete_mail_confirm, pattern=r"^delete_confirm_\d+$"))
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    telegram_application.run_polling(drop_pending_updates=True)

def start_bot_action():
    global bot_thread
    if bot_thread and bot_thread.is_alive():
        messagebox.showinfo("Telegram Bot", "Bot is already running!")
        return

    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    status_label.config(text="Bot Status: Running")
    messagebox.showinfo("Telegram Bot", "Bot started successfully!")

def stop_bot_action():
    global telegram_application, bot_thread
    if telegram_application and telegram_application.running:
        telegram_application.stop()
        bot_thread.join(timeout=5)
        if bot_thread.is_alive():
            pass 
        status_label.config(text="Bot Status: Stopped")
        messagebox.showinfo("Telegram Bot", "Bot stopped.")
    else:
        messagebox.showinfo("Telegram Bot", "Bot is not running.")

def hide_window_to_tray():
    root_window.withdraw()
    if tray_icon:
        tray_icon.visible = True

def show_window_from_tray(icon, item):
    icon.visible = False
    root_window.deiconify()

def quit_application(icon, item):
    icon.stop()
    stop_bot_action()
    root_window.quit()

def create_tray_icon():
    global tray_icon
    image = Image.new('RGB', (64, 64), color = 'gray')
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 64, 64), fill='gray')

    menu = (pystray.MenuItem('Show', show_window_from_tray),
            pystray.MenuItem('Quit', quit_application))

    tray_icon = pystray.Icon("Telegram Bot", image, "Telegram Bot", menu)
    tray_icon.run_detached()

def setup_gui():
    global root_window, status_label
    root_window = tk.Tk()
    root_window.title("Telegram Bot Control")
    root_window.geometry("300x200")

    status_label = tk.Label(root_window, text="Bot Status: Stopped", font=("Arial", 12))
    status_label.pack(pady=20)

    start_button = tk.Button(root_window, text="Start Bot", command=start_bot_action)
    start_button.pack(pady=5)

    stop_button = tk.Button(root_window, text="Stop Bot", command=stop_bot_action)
    stop_button.pack(pady=5)

    hide_button = tk.Button(root_window, text="Hide to Tray", command=hide_window_to_tray)
    hide_button.pack(pady=5)

    root_window.protocol("WM_DELETE_WINDOW", hide_window_to_tray) 

    create_tray_icon()
    root_window.mainloop()

if __name__ == "__main__":
    setup_gui()

# Telegram Email Vault Bot

A simple Telegram bot designed to securely store and manage email credentials (email address, password, 2FA info, and tags) with a shared master password for access. This bot features a desktop GUI for control and supports pagination for displaying multiple entries.

## Features

* **Master Password Protection**: All data is protected by a single, shared master password. Only users who know this password can access the stored credentials.
* **Centralized Storage**: All authenticated users share a common database of email entries.
* **Simple GUI Control**: A basic desktop graphical user interface (GUI) for starting and stopping the bot, and hiding it to the system tray.
* **Email Management**:
    * **Add Email**: Store new email credentials.
    * **Delete Email**: Remove existing email entries by ID.
    * **View Emails**: Display stored emails with pagination to handle large lists efficiently.
* **Truncated Display**: Long email, password, 2FA, and tag entries are truncated for better readability in Telegram messages.
* **Persistent Data**: Email entries and authenticated user states are saved to `emails.json` and `user_states.json` files, respectively, ensuring data persistence across bot restarts.
* **Tray Icon**: The bot can run in the background with a system tray icon for easy access.

## How to Set Up and Run

### Prerequisites

* Python 3.x
* `python-telegram-bot` library (version 20.x recommended)
* `Pillow` library (for GUI tray icon)
* `pystray` library (for system tray icon)

You can install the necessary libraries using pip:

```bash
pip install python-telegram-bot==20.x Pillow pystray

# Email Fetcher

A Python script that automatically fetches job-related emails from Gmail and creates draft response emails.

## Overview

This project consists of two main scripts:
- **`fetcher.py`**: Fetches emails matching job-related keywords from your Gmail account
- **`sender.py`**: Creates draft emails to respond to job opportunities

## Setup

### 1. Google Cloud Console Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select an existing one
3. Enable the **Gmail API** for your project
4. Create OAuth 2.0 credentials:
   - Go to **APIs & Services** → **Credentials**
   - Click **Create Credentials** → **OAuth client ID**
   - Choose **Desktop app** as the application type
   - Download the credentials file and save it as `credentials.json` in the project root

### 2. Add Test Users

**Important**: Add all email accounts you'll use (both fetching and sending accounts) as **Test Users** in the OAuth consent screen:
- Go to **APIs & Services** → **OAuth consent screen**
- Under **Test users**, click **Add Users**
- Add your email addresses
- Without this step, you'll get a "Permission denied" error during authentication

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configuration

1. Copy `config.py` and update the following:
   - `SENDER_EMAIL`: Your sending email address
   - `ATTACHMENT_FILE`: Path to your resume (optional)
   - `DRAFT_BODY`: Your email template (HTML format supported)

### 5. Run

```bash
# Fetch emails
python fetcher.py

# Create draft emails
python sender.py
```

## Features

- Filters emails by job-related keywords
- Extracts company and position information
- Creates draft emails with attachments
- Supports HTML email formatting with clickable links

## Security

- Token files and credentials are excluded from version control (see `.gitignore`)
- Never commit `credentials.json` or files in the `tokens/` folder

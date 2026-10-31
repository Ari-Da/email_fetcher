import json
import os

SETTINGS_FILE = "settings.json"
REQUIRED_KEYS = [
    'features', 'recruiter_accounts', 'application_accounts', 'destination_email',
    'draft_subject', 'draft_body_file', 'attachment_file',
]
ACCOUNT_LISTS = ['recruiter_accounts', 'application_accounts']


# Loads settings.json, and stops with a clear message if it isn't usable.
def load_settings(path=SETTINGS_FILE):
    if not os.path.exists(path):
        raise SystemExit(f"{path} not found. Copy settings.example.json to {path} and fill it in.")
    with open(path) as settings_file:
        settings = json.load(settings_file)

    for key in REQUIRED_KEYS:
        if key not in settings:
            raise SystemExit(f"{path} is missing '{key}'. See settings.example.json.")
    for key in ACCOUNT_LISTS:
        accounts = settings[key]
        if not isinstance(accounts, list) or not all(isinstance(a, str) and '@' in a for a in accounts):
            raise SystemExit(f"'{key}' in {path} must be a list of email addresses.")
    return settings

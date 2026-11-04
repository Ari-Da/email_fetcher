import datetime
import hashlib
import json
import os

import gmail_client

# Import configuration
from config import DAYS_BACK, RECRUITER_OVERLAP_DAYS, RECRUITER_STATE_FILE


# Where each account got to, and who already has a draft. Addresses are saved
# as hashes, so the file can match a recruiter without listing them.
def load_state(path=RECRUITER_STATE_FILE):
    if not os.path.exists(path):
        return {'scannedThrough': {}, 'contacted': []}
    with open(path) as state_file:
        return json.load(state_file)


# Written to a temp file first, so a crash can't leave a half-saved state.
def save_state(state, path=RECRUITER_STATE_FILE):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_path = f"{path}.tmp"
    gmail_client.save_private(temp_path, json.dumps(state))
    os.replace(temp_path, path)


def address_hash(address):
    return hashlib.sha256(address.strip().lower().encode()).hexdigest()


# Picks the search start: the full window for a first run, a retry or a chosen
# number of days, otherwise the last finished run minus a small overlap.
def scan_start(state, account, today, retry=False, days=None):
    window_start = today - datetime.timedelta(days=days or DAYS_BACK)
    last_scan = state['scannedThrough'].get(account)
    if retry or days or not last_scan:
        return window_start.strftime('%Y/%m/%d')
    start = datetime.date.fromisoformat(last_scan) - datetime.timedelta(days=RECRUITER_OVERLAP_DAYS)
    return start.strftime('%Y/%m/%d')

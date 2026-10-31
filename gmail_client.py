import base64
import os
import re
import sys
import time

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Import configuration
from config import GMAIL_READONLY_SCOPES, CREDENTIALS_FILE

PAGE_SIZE = 100

# Under this length a text/plain part is usually a "view in browser" stub
MIN_PLAIN_BODY = 200

# Gmail limits requests per minute. Pacing avoids most refusals, and retries
# wait out the rest with growing delays that outlast the one-minute window.
SECONDS_BETWEEN_READS = 0.1
GMAIL_RETRIES = 8

# A login blocked by an organization never reports back, so don't wait forever
LOGIN_TIMEOUT_SECONDS = 180


# Raised when an account needs a browser login but nobody is there to do it.
class LoginNeeded(Exception):
    pass


# Each address has one read-only token, shared by the tracker and the recruiter
# finder. The draft token is kept apart in TOKEN_SENDER_FILE.
def token_path(account):
    slug = re.sub(r'[^a-z0-9]+', '_', account.lower()).strip('_')
    return f"tokens/token_gmail_{slug}.json"


# True only when the token can do nothing beyond reading mail.
def is_read_only(creds):
    return set(creds.scopes or []) == set(GMAIL_READONLY_SCOPES)


# A scheduled run has no terminal, so it can't wait on a browser login.
def can_open_browser():
    return sys.stdin.isatty()


# Saves a token that only this macOS user can read.
def save_private(path, content):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as private_file:
        private_file.write(content)
    os.chmod(path, 0o600)


# Loads a saved token, refreshing it or logging in again when needed.
def get_credentials(account):
    path = token_path(account)
    creds = None
    if os.path.exists(path) and os.path.getsize(path) > 0:
        try:
            # No scopes passed here, so we check what the token really holds
            creds = Credentials.from_authorized_user_file(path)
        except ValueError:
            creds = None

    # A token with more access than reading is never used
    if creds and not is_read_only(creds):
        creds = None

    if creds and not creds.valid:
        try:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                creds = None
        except RefreshError:
            # Tokens for an OAuth app in Testing mode expire after 7 days
            creds = None

    if not creds:
        if not can_open_browser():
            raise LoginNeeded(f"{account} needs a browser login. Run this once from a terminal.")
        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, GMAIL_READONLY_SCOPES)
        try:
            creds = flow.run_local_server(port=0, login_hint=account, timeout_seconds=LOGIN_TIMEOUT_SECONDS)
        except Exception as error:
            raise LoginNeeded(f"{account} login did not finish. It may be blocked or was cancelled.") from error
        if not is_read_only(creds):
            raise SystemExit(f"Login for {account} did not return a read-only token. Nothing was read or saved.")
    return creds


# Returns a read-only Gmail service, and refuses to continue if the login
# landed on a different mailbox than the one asked for.
def connect(account):
    creds = get_credentials(account)
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    actual = service.users().getProfile(userId="me").execute()["emailAddress"]
    if actual.lower() != account.lower():
        raise SystemExit(f"Logged in as {actual}, expected {account}. Nothing was read or saved.")

    save_private(token_path(account), creds.to_json())
    return service


# Yields every message ID for a query, following pagination to the end.
def search_message_ids(service, query):
    page_token = None
    while True:
        results = service.users().messages().list(
            userId="me", q=query, maxResults=PAGE_SIZE, pageToken=page_token
        ).execute(num_retries=GMAIL_RETRIES)
        for message in results.get("messages", []):
            yield message["id"]
        page_token = results.get("nextPageToken")
        if not page_token:
            return


def get_message(service, message_id):
    time.sleep(SECONDS_BETWEEN_READS)
    return service.users().messages().get(
        userId="me", id=message_id, format="full"
    ).execute(num_retries=GMAIL_RETRIES)


def get_header(message, name):
    headers = message.get("payload", {}).get("headers", [])
    return next((h["value"] for h in headers if h["name"].lower() == name.lower()), "")


# Walks every MIME part, however deeply it is nested.
def walk_parts(payload):
    yield payload
    for part in payload.get("parts", []):
        yield from walk_parts(part)


# Fetches the best text body available. Unlike fetcher.get_email_body, this
# searches all nested parts and survives emails that are not valid UTF-8.
def get_email_body(payload):
    parts = [
        part for part in walk_parts(payload)
        if part.get("body", {}).get("data") and not part.get("filename")
    ]

    def first_of(mime_type):
        for part in parts:
            if part.get("mimeType") == mime_type:
                return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
        return ""

    plain = first_of("text/plain")
    html = first_of("text/html")
    if plain and (len(plain) >= MIN_PLAIN_BODY or not html):
        return plain
    return html or plain

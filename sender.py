import collections
import datetime
import os.path
import base64
import re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Import from fetcher
from fetcher import fetch_emails
import gmail_client
import recruiter_classifier
import recruiter_state
from settings import load_settings
from track_applications import registered_label

# Import configuration
from config import (
    GMAIL_COMPOSE_SCOPES,
    CREDENTIALS_FILE,
    TOKEN_SENDER_FILE,
    EMAIL_KEYWORDS,
    JOB_KEYWORDS,
    LINKEDIN_DOMAINS,
    TRACKER_ATS_DOMAINS,
    TRACKER_JOB_BOARD_DOMAINS,
    TRACKER_PERSONAL_DOMAINS
)

AUTOMATED_MAILBOX = re.compile(r"^(?:no-?reply|do-?not-?reply|notifications?|mailer-daemon|postmaster|alerts?|updates?)\b", re.I)
# Messages about a process already under way, which outreach would interrupt
ACTIVE_PROCESS_SUBJECT = re.compile(
    r"\b(?:interview\w*|recruiter screen|phone screen|confirmation|confirmed|scheduled?|reschedul\w+|assessment|your application)\b", re.I)

# Extract email address from sender string like 'Name <email@domain.com>' or 'email@domain.com'
def extract_email_address(sender_string):
    # Try to find email in angle brackets first
    match = re.search(r'<([^>]+)>', sender_string)
    if match:
        return match.group(1)
    
    # If no angle brackets, try to find email pattern
    match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', sender_string)
    if match:
        return match.group(0)
    
    return None

# Returns why an email isn't real outreach to answer, or None if it is.
def filter_reason(to_email, subject, bulk):
    mailbox, _, domain = to_email.lower().partition('@')
    if AUTOMATED_MAILBOX.match(mailbox):
        return 'automated address'
    third_party = TRACKER_ATS_DOMAINS | TRACKER_JOB_BOARD_DOMAINS
    if any(domain == known or domain.endswith('.' + known) for known in third_party):
        return 'hiring system'
    if domain.endswith('.edu') or '.edu.' in domain:
        return 'university'
    if ACTIVE_PROCESS_SUBJECT.search(subject):
        return 'active process'
    if bulk:
        return 'mass email'
    return None

def email_domain(address):
    return address.lower().partition('@')[2]

# Companies with an interview under way. Everyone there is off limits, not just
# the person who sent the scheduling email. Gmail and similar services are not
# companies, so they never block anyone.
def active_process_companies(email_results):
    companies = set()
    for email_result in email_results:
        address = extract_email_address(email_result['sender'])
        if not address or not ACTIVE_PROCESS_SUBJECT.search(email_result['subject']):
            continue
        domain = email_domain(address)
        if not any(domain == known or domain.endswith('.' + known) for known in TRACKER_PERSONAL_DOMAINS):
            companies.add(registered_label(domain))
    return companies

# True if she already has a draft or sent email to this address. A retry
# ignores the saved list, so a deleted draft can be made again.
def already_contacted(read_service, to_email, state, retry=False):
    if not retry and recruiter_state.address_hash(to_email) in state['contacted']:
        return True
    query = f'to:"{to_email}" (in:sent OR in:drafts)'
    return next(gmail_client.search_message_ids(read_service, query), None) is not None

# Check if sender is from LinkedIn
def is_linkedin_sender(sender_string):
    sender_lower = sender_string.lower()
    return any(domain in sender_lower for domain in LINKEDIN_DOMAINS)

# Check if subject contains any of the email_keywords or job_keywords
def subject_contains_keywords(subject):
    subject_lower = subject.lower()
    # Check for email keywords
    has_email_keyword = any(keyword.lower() in subject_lower for keyword in EMAIL_KEYWORDS)
    # Check for job keywords
    has_job_keyword = any(keyword.lower() in subject_lower for keyword in JOB_KEYWORDS)
    # Subject must contain at least one keyword from either list
    return has_email_keyword or has_job_keyword

# Create a draft email in Gmail
def create_draft_message(service, to_email, subject, body, from_email=None, attachment_path=None):
    # Create multipart message to support attachments
    message = MIMEMultipart()
    message['to'] = to_email
    message['subject'] = subject
    
    # Set the From address if provided
    if from_email:
        message['from'] = from_email
    
    # Add body as HTML (DRAFT_BODY should contain HTML)
    message.attach(MIMEText(body, 'html'))
    
    # Add attachment if provided
    if attachment_path and attachment_path.strip() and os.path.exists(attachment_path):
        with open(attachment_path, "rb") as attachment:
            # Create MIMEBase object
            part = MIMEBase('application', 'octet-stream')
            part.set_payload(attachment.read())
        
        # Encode file in ASCII characters to send by email
        encoders.encode_base64(part)
        
        # Add header as key/value pair to attachment part
        filename = os.path.basename(attachment_path)
        part.add_header(
            'Content-Disposition',
            f'attachment; filename= {filename}',
        )
        
        # Attach the part to message
        message.attach(part)
    
    # Encode the message
    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')
    
    # Create the draft
    draft = service.users().drafts().create(
        userId='me',
        body={
            'message': {
                'raw': raw_message
            }
        }
    ).execute()
    
    return draft

def main(retry=False, days=None):
    settings = load_settings()
    destination_email = settings['destination_email']
    with open(settings['draft_body_file']) as body_file:
        draft_body = body_file.read()

    # No draft is made without the model's check, so stop early if it's missing
    recruiter_classifier.require_model()

    # Get email results from fetcher
    email_results, finished_accounts = fetch_emails(retry, days)
    state = recruiter_state.load_state()
    
    # Authenticate with compose scope for creating drafts
    creds = None
    
    # Check if token file exists and is not empty
    if os.path.exists(TOKEN_SENDER_FILE) and os.path.getsize(TOKEN_SENDER_FILE) > 0:
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_SENDER_FILE, GMAIL_COMPOSE_SCOPES)
        except (ValueError, Exception):
            # If token file is invalid, treat as if it doesn't exist
            creds = None
    
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        try:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                creds = None
        except RefreshError:
            creds = None
        if not creds:
            if not gmail_client.can_open_browser():
                raise gmail_client.LoginNeeded(f"{destination_email} needs a browser login. Run this once from a terminal.")
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_FILE, GMAIL_COMPOSE_SCOPES
            )
            creds = flow.run_local_server(port=0, login_hint=destination_email)
        # Save the credentials for the next run
        gmail_client.save_private(TOKEN_SENDER_FILE, creds.to_json())

    # Build the Gmail service
    service = build("gmail", "v1", credentials=creds)
    
    # Get the authenticated user's email (this is the sending account)
    profile = service.users().getProfile(userId="me").execute()
    authenticated_email = profile.get("emailAddress", "Unknown")
    if authenticated_email.lower() != destination_email.lower():
        os.remove(TOKEN_SENDER_FILE)
        print(f"ERROR: logged in as {authenticated_email}, expected {destination_email}. No drafts created.")
        return
    print(f"Drafts will be created in: {authenticated_email}\n")
    
    # Draft access can't search mail, so the check for past contact reads separately
    read_service = gmail_client.connect(destination_email)
    
    # Process each email result
    drafts_created = 0
    skipped = 0
    drafted_for = set()
    skip_reasons = collections.Counter()
    # Replies sent from her own accounts or the searched ones are never recruiters
    own_addresses = {a.lower() for a in settings['recruiter_accounts'] + [destination_email]}
    active_companies = active_process_companies(email_results)
    
    print("\n\nProcessing results for draft creation:\n")
    print("-" * 40)
    
    for email_result in email_results:
        subject = email_result['subject']
        sender = email_result['sender']
        
        # Filter: Skip LinkedIn senders
        if is_linkedin_sender(sender):
            skip_reasons['linkedin'] += 1
            skipped += 1
            continue
        
        # Filter: Check if subject contains keywords
        if not subject_contains_keywords(subject):
            skip_reasons['no job words in subject'] += 1
            skipped += 1
            continue
        
        # Extract email address from sender
        to_email = extract_email_address(sender)
        if not to_email:
            skip_reasons['no sender address'] += 1
            skipped += 1
            continue
        if to_email.lower() in own_addresses:
            skip_reasons['own email'] += 1
            skipped += 1
            continue
        
        # Filter: Skip automated, university, in-progress and mass emails
        reason = filter_reason(to_email, subject, email_result['bulk'])
        if not reason and registered_label(email_domain(to_email)) in active_companies:
            reason = 'active process'
        if reason:
            skip_reasons[reason] += 1
            skipped += 1
            continue
        
        # One draft per recruiter, even if they emailed several accounts
        if to_email.lower() in drafted_for:
            skip_reasons['same recruiter again'] += 1
            skipped += 1
            continue
        drafted_for.add(to_email.lower())
        
        # Skip anyone she already contacted
        if not re.fullmatch(r'[\w.+-]+@[\w.-]+', to_email):
            skip_reasons['invalid address'] += 1
            skipped += 1
            continue
        if already_contacted(read_service, to_email, state, retry):
            skip_reasons['already contacted'] += 1
            skipped += 1
            continue
        
        # The rules are a first pass. The model reads the email for context
        if not recruiter_classifier.is_cold_outreach(sender, subject, email_result['text']):
            skip_reasons['not cold outreach'] += 1
            skipped += 1
            continue
        
        # Create draft
        try:
            draft = create_draft_message(service, to_email, settings['draft_subject'], draft_body, destination_email, settings['attachment_file'])
            print("-" * 40)
            print(f"Draft created for: {to_email}")
            print(f"  From: {destination_email}")
            print(f"  Original subject: {subject}")
            drafts_created += 1
            # Saved per draft, so a crash can't lead to a duplicate
            state['contacted'].append(recruiter_state.address_hash(to_email))
            recruiter_state.save_state(state)
        except Exception as e:
            print(f"Error creating draft for {to_email}: {str(e)}")
            skipped += 1
        
        print("-" * 40)
    
    # Progress only moves forward once every result has been handled
    for account in finished_accounts:
        state['scannedThrough'][account] = datetime.date.today().isoformat()
    recruiter_state.save_state(state)
    print(f"\nSummary: {drafts_created} drafts created, {skipped} skipped")
    for reason, count in skip_reasons.most_common():
        print(f"  {reason}: {count}")

if __name__ == "__main__":
    main()

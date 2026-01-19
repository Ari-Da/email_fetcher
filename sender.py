import os.path
import base64
import re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Import from fetcher
from fetcher import fetch_emails

# Import configuration
from config import (
    GMAIL_COMPOSE_SCOPES,
    CREDENTIALS_FILE,
    TOKEN_SENDER_FILE,
    EMAIL_KEYWORDS,
    JOB_KEYWORDS,
    LINKEDIN_DOMAINS,
    DRAFT_SUBJECT,
    DRAFT_BODY,
    SENDER_EMAIL,
    ATTACHMENT_FILE
)

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

def main():
    # Get email results from fetcher
    email_results = fetch_emails()
    
    if not email_results:
        print("No email results to process.")
        return
    
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
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_FILE, GMAIL_COMPOSE_SCOPES
            )
            creds = flow.run_local_server(port=0)
        # Save the credentials for the next run
        with open(TOKEN_SENDER_FILE, "w") as token:
            token.write(creds.to_json())

    # Build the Gmail service
    service = build("gmail", "v1", credentials=creds)
    
    # Verify sender email is configured
    if not SENDER_EMAIL:
        print("ERROR: SENDER_EMAIL is not configured in config.py")
        print("Please set SENDER_EMAIL to the email address you want to send from.")
        return
    
    # Get the authenticated user's email (this is the sending account)
    profile = service.users().getProfile(userId="me").execute()
    authenticated_email = profile.get("emailAddress", "Unknown")
    print(f"Authenticated sending account: {authenticated_email}")
    print(f"Drafts will be created in: {authenticated_email}")
    print(f"From address will be set to: {SENDER_EMAIL}\n")
    
    # Process each email result
    drafts_created = 0
    skipped = 0
    
    print("\nProcessing results for draft creation:\n")
    
    for email_result in email_results:
        subject = email_result['subject']
        sender = email_result['sender']
        
        # Filter: Skip LinkedIn senders
        if is_linkedin_sender(sender):
            print(f"Skipping LinkedIn sender: {sender}")
            skipped += 1
            continue
        
        # Filter: Check if subject contains keywords
        if not subject_contains_keywords(subject):
            print(f"Skipping - subject doesn't contain keywords: {subject}")
            skipped += 1
            continue
        
        # Extract email address from sender
        to_email = extract_email_address(sender)
        if not to_email:
            print(f"Skipping - could not extract email from: {sender}")
            skipped += 1
            continue
        
        # Create draft
        try:
            draft = create_draft_message(service, to_email, DRAFT_SUBJECT, DRAFT_BODY, SENDER_EMAIL, ATTACHMENT_FILE)
            print("-" * 40)
            print(f"Draft created for: {to_email}")
            print(f"  From: {SENDER_EMAIL}")
            print(f"  Original subject: {subject}")
            drafts_created += 1
        except Exception as e:
            print(f"Error creating draft for {to_email}: {str(e)}")
            skipped += 1
        
        print("-" * 40)
    
    print(f"\nSummary: {drafts_created} drafts created, {skipped} skipped")

if __name__ == "__main__":
    main()

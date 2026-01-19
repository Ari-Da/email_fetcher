# Gmail API Scopes
# If modifying these scopes, delete the token files
GMAIL_READONLY_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
GMAIL_COMPOSE_SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]

# File paths
CREDENTIALS_FILE = "credentials.json"
TOKEN_FETCHER_FILE = "tokens/token_fetcher.json"
TOKEN_SENDER_FILE = "tokens/token_sender.json"

# Email fetching configuration
MAX_RESULTS = 20
DAYS_BACK = 120

# Email search keywords
EMAIL_KEYWORDS = [
    "recruit", "recruiter", "recruiting", "recruitment",
    "job", "jobs",
    "hiring", "hire",
    "career", "careers",
    "opportunity", "opportunities",
    "founder", "founders"
]

# Job title keywords
JOB_KEYWORDS = ['engineer', 'manager', 'developer', 'consultant', 'analyst', 'designer', 'director']

# Generic email domains to filter out
GENERIC_DOMAINS = {
    'gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com', 'aol.com', 'icloud.com',
    'linkedin.com', 'e.linkedin.com', 'linkedin.messages.com',  # LinkedIn specific wrappers
    'indeed.com', 'glassdoor.com', 'monster.com',
    'recruiting.com', 'smartrecruiters.com', 'jobvite.com', 'lever.co', 'greenhouse.io',  # ATS systems often mask real company
    'no-reply', 'noreply', 'notifications', 'email'
}

# LinkedIn domains for filtering
LINKEDIN_DOMAINS = ['linkedin.com', 'e.linkedin.com', 'linkedin.messages.com']

# Sender email address (the account that will send the drafts)
# This should be a DIFFERENT account from the one used for fetching emails
# When running sender.py, authenticate with this account (Account B)
SENDER_EMAIL = ""  # Set your sending email address here

# Draft email configurations
DRAFT_SUBJECT = "" # Set your draft subject here

# Path to attachment file, or no attachment if empty string ""
ATTACHMENT_FILE = ""  # Set path to your resume/attachment file here

# Draft email body (HTML format supported)
DRAFT_BODY = ""  # Set your email body template here
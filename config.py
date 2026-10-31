# Gmail API Scopes
# If modifying these scopes, delete the token files
GMAIL_READONLY_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
GMAIL_COMPOSE_SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]

# File paths
CREDENTIALS_FILE = "credentials.json"
TOKEN_SENDER_FILE = "tokens/token_sender.json"

# Email fetching configuration
MAX_RESULTS = 40
DAYS_BACK = 120

# Email search keywords
EMAIL_KEYWORDS = [
    "recruit", "recruiter", "recruiting", "recruitment",
    "job", "jobs",
    "hiring", "hire",
    "career", "careers",
    "opportunity", "opportunities",
    "founder", "founders",
    # "investor", "investors",
    # "partner", "partners",
    # "advisor", "advisors",
    # "consultant", "consultants"
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

# Personal values (accounts, draft text) live in settings.json, which git
# ignores. Never add them here: this file is public.

# Application tracker (track_applications.py)
TRACKER_START_DATE = "2025/09/01"
TRACKER_NO_RESPONSE_DAYS = 30
TRACKER_OUTPUT_FILE = "data/applications.json"
TRACKER_TEST_OUTPUT_FILE = "data/applications_test.json"
TRACKER_STATE_FILE = "data/tracker_state.json"
# Each run looks back this far before the last scan, to catch late emails
TRACKER_OVERLAP_DAYS = 2

# Gmail search phrases for the tracker. Kept broad on purpose: the classifier
# decides what counts, so a missed phrase here loses an application for good.
TRACKER_SEARCH_PHRASES = [
    "thank you for applying", "thanks for applying",
    "thank you for your application", "thank you for your interest",
    "your application", "application received", "application submitted",
    "application was sent",
    "interview", "phone screen", "assessment", "coding challenge",
    "other candidates", "move forward", "moving forward", "regret to inform",
    "offer letter",
]

# Senders that act for many employers, so the domain never names the company
TRACKER_ATS_DOMAINS = {
    'greenhouse.io', 'greenhouse-mail.io', 'lever.co', 'ashbyhq.com',
    'myworkday.com', 'myworkdayjobs.com', 'workday.com',
    'smartrecruiters.com', 'icims.com', 'jobvite.com', 'taleo.net',
    'successfactors.com', 'successfactors.eu', 'eightfold.ai',
    'workable.com', 'workablemail.com', 'breezy.hr', 'bamboohr.com',
    'rippling.com', 'dover.com', 'teamtailor.com', 'recruitee.com',
    'paylocity.com', 'ultipro.com', 'ukg.com', 'adp.com', 'gem.com',
    'avature.net', 'oraclecloud.com', 'jazzhr.com', 'applytojob.com',
    'phenompeople.com', 'tal.net', 'njoyn.com', 'hackerrankforwork.com',
    'hackerrank.com', 'panpowered.com', 'codesignal.com', 'codility.com',
}
TRACKER_JOB_BOARD_DOMAINS = {
    'linkedin.com', 'indeed.com', 'indeedemail.com', 'glassdoor.com',
    'ziprecruiter.com', 'joinhandshake.com', 'wellfound.com', 'simplify.jobs',
    'dice.com', 'monster.com', 'ripplematch.com', 'talent.com',
}
TRACKER_PERSONAL_DOMAINS = {
    'gmail.com', 'googlemail.com', 'yahoo.com', 'hotmail.com', 'outlook.com',
    'aol.com', 'icloud.com', 'proton.me', 'protonmail.com',
}

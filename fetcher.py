import datetime
import re

from bs4 import BeautifulSoup

import gmail_client
import recruiter_state
from settings import load_settings

# Import configuration
from config import (
    EMAIL_KEYWORDS,
    JOB_KEYWORDS,
    GENERIC_DOMAINS
)

#

# Helper to pull e.g. 'databricks' from 'name@databricks.com'
def extract_domain_from_email(email_string):
    try:
        # regex to grab just the email address if it's inside < >
        match = re.search(r'[\w\.-]+@([\w\.-]+)', email_string)
        if match:
            full_domain = match.group(1).lower()
            
            # Check if it is a generic domain
            if full_domain in GENERIC_DOMAINS:
                return None
            
            # Remove .com, .org, etc to get the clean name
            company_name = full_domain.rsplit('.', 1)[0]
            return company_name.title()
    except:
        pass
    return None

# Scans the body text for ANY email address that isn't generic.
# Useful for LinkedIn messages where the real sender is in the footer.
def find_company_email_in_body(text):
    # Find all email-looking strings
    email_matches = re.findall(r'[\w\.-]+@[\w\.-]+\.\w+', text)
    
    for email in email_matches:
        domain_company = extract_domain_from_email(email)
        if domain_company:
            return domain_company # Return the first valid non-generic company found
            
    return None

# Finds job title using Regex (Primary) and Subject (Fallback)
def extract_position_regex(text, subject):
    pattern = r'\b(Senior|Junior|Lead|Staff|Principal|Head of)?\s*[\w\s\-]{0,25}\s(Engineer|Developer|Manager|Director|Analyst|Designer|Scientist|Consultant)\b'
    matches = [m.group(0) for m in re.finditer(pattern, text, re.IGNORECASE)]
    
    if matches:
        return max(matches, key=len)
    
    # Fallback: Check Subject line
    fallback_keywords = JOB_KEYWORDS + ['role', 'opportunity']
    if any(k in subject.lower() for k in fallback_keywords):
        return subject.strip()

    return "Not Found"


# Look for patterns like 'at Company' or 'Company Recruiting'
def extract_company_from_subject(subject):
    subject = subject.strip()
    
    # Pattern 1: "Role at Company" or "Role @ Company"
    # We look for " at " followed by words that start with a capital letter
    # e.g. "Engineer at Databricks"
    at_match = re.search(r'\s(?:at|@)\s+([A-Z][\w\s&]+)', subject)
    if at_match:
        company = at_match.group(1).strip()
        # Clean up if it grabbed too much (e.g. "Databricks in NYC")
        return company.split(' in ')[0].strip()

    # Pattern 2: "Company Recruiting" or "Company Hiring"
    recruiting_match = re.search(r'^([A-Z][\w\s]+?)\s+(?:Recruiting|Hiring|Careers|Talent)', subject)
    if recruiting_match:
        return recruiting_match.group(1).strip()

    # Pattern 3: Separators " | " or " - " 
    # This is riskier, usually the company is at the END or START.
    # e.g. "Senior Engineer - Amazon"
    if ' - ' in subject:
        parts = subject.split(' - ')
        # Assume the shorter part is the company if it looks like a name
        possible_company = parts[-1].strip()
        if len(possible_company) < 20 and "Job" not in possible_company:
            return possible_company
            
    return None

# Mass emails carry list headers or an unsubscribe link. A person writing to
# one recipient has neither.
def is_bulk_email(headers, text):
    for header in headers:
        name = header['name'].lower()
        if name in ('list-unsubscribe', 'list-id'):
            return True
        if name == 'precedence' and header['value'].strip().lower() in ('bulk', 'list'):
            return True
    return 'unsubscribe' in text.lower()

# Fetch email results, plus the accounts that finished. Reading saves no
# progress: sender.py does that once the drafts exist.
def fetch_emails(retry=False, days=None):
    state = recruiter_state.load_state()
    today = datetime.date.today()
    email_results = []
    finished_accounts = []
    for account in load_settings()['recruiter_accounts']:
        print(f"\nAccount: {account}")
        try:
            start_date = recruiter_state.scan_start(state, account, today, retry, days)
            email_results.extend(fetch_account_emails(gmail_client.connect(account), start_date))
            finished_accounts.append(account)
        except (Exception, SystemExit) as error:
            # One account failing never stops the others
            print(f"{account}: skipped. {error}")
    return email_results, finished_accounts

# Runs the recruiter search on one connected account
def fetch_account_emails(service, date_query):
    # 1. Build Query String (uses standard Gmail search syntax)
    # Example result: 'after:2023/10/05 ("job" OR "recruiting")'
    keyword_query = " OR ".join([f'"{k}"' for k in EMAIL_KEYWORDS])
    query_string = (
        f'after:{date_query} '
        f'subject:({keyword_query}) '
        f'-category:promotions -category:social '
    )

    print(f"Searching for emails matching: {query_string}")

    # 2. Fetch every Message ID in the window
    messages = list(gmail_client.search_message_ids(service, query_string))

    if not messages:
        print("No messages found.")
        return []

    print(f"Found {len(messages)} messages:\n")

    # 3. Fetch Details for each ID and build result list
    email_results = []
    for message_id in messages:
        msg_detail = gmail_client.get_message(service, message_id)
        headers = msg_detail['payload']['headers']
        
        # Extract Metadata
        subject = next((h['value'] for h in headers if h['name'] == 'Subject'), '(No Subject)')
        sender = next((h['value'] for h in headers if h['name'] == 'From'), '(Unknown)')
        
        # Extract Body & Clean it (remove HTML tags)
        raw_body = gmail_client.get_email_body(msg_detail['payload'])
        if not raw_body: continue
        clean_text = BeautifulSoup(raw_body, "html.parser").get_text()
        
        # Logic Start
        
        # 1. Try to get Company from Header (Sender)
        company = extract_domain_from_email(sender)
        
        # 2. If Header was generic (e.g. LinkedIn), search Body for a real email
        if not company:
            company = find_company_email_in_body(clean_text)

        # Step 3: Check Subject (Pattern Match)
        if not company:
            company = extract_company_from_subject(subject)

        # 4. Final Fallback
        if not company:
            company = "Not Found"

        # 4. Get Position
        position = extract_position_regex(clean_text, subject)

        # Add to results list
        email_results.append({
            'subject': subject,
            'sender': sender,
            'company': company,
            'position': position,
            'bulk': is_bulk_email(headers, clean_text),
            'text': clean_text
        })

        # Output
        print(f"Subject: {subject}")
        print(f"Sender: {sender}")
        print(f"Company: {company}")
        print(f"Position: {position}")
        print("-" * 40)
    
    return email_results

def main():
    fetch_emails()

if __name__ == "__main__":
    main()
import datetime
import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from email.utils import parseaddr
from typing import Optional

from bs4 import BeautifulSoup

import gmail_client

# Import configuration
from config import (
    TRACKER_ATS_DOMAINS,
    TRACKER_JOB_BOARD_DOMAINS,
    TRACKER_NO_RESPONSE_DAYS,
    TRACKER_OUTPUT_FILE,
    TRACKER_OVERLAP_DAYS,
    TRACKER_PERSONAL_DOMAINS,
    TRACKER_REVIEW_MESSAGES_FILE,
    TRACKER_SEARCH_PHRASES,
    TRACKER_START_DATE,
    TRACKER_STATE_FILE,
    TRACKER_TEST_OUTPUT_FILE,
)

# A confirmation and a duplicate from a job board arrive within this many days
SAME_APPLICATION_DAYS = 1
NEW_APPLICATION_REASONS = {'no_application_found', 'role_not_matched'}

# Checked in this order. Rejections often open with "Thank you for applying",
# so 'applied' has to come last.
STATUS_PATTERNS = [
    ('rejected', re.compile(
        r"regret to inform"
        r"|\b(?:not|won'?t|unable to|cannot|can'?t)\b[^.!?]{0,40}\b(?:mov\w+|proceed\w*|go\w*)\s+forward"
        r"|\bnot be (?:moving|proceeding|progressing)\b"
        r"|\b(?:mov\w+|proceed\w*|pursu\w+|go\w*)\b[^.!?]{0,40}\bother (?:candidates|applicants)\b"
        r"|\bnot (?:been )?selected\b"
        r"|\bposition has been filled\b"
        r"|\bno longer (?:considering|under consideration)\b"
        r"|\bdecided not to\b"
        r"|\bnot (?:the right|a) (?:fit|match)\b", re.I)),
    ('offer', re.compile(
        r"\bpleased to (?:offer|extend)\b"
        r"|\bextend(?:ing)? (?:you )?an offer\b"
        r"|\boffer (?:letter|of employment)\b", re.I)),
    ('assessment', re.compile(
        r"\b(?:online|technical|coding|skills?) (?:assessment|challenge|test)\b"
        r"|\bcomplete (?:the|an|your|this) (?:\w+ )?assessment\b"
        r"|\btake[- ]home\b"
        r"|\b(?:hackerrank|codesignal|codility)\b", re.I)),
    ('interview', re.compile(
        r"\binvit\w+ (?:you )?to (?:\w+ ){0,3}interview\b"
        r"|\b(?:schedul\w+|book\w*|availability for) (?:a |an |your |the )?(?:\w+ ){0,3}(?:interview|phone screen)\b"
        r"|\bnext (?:round|stage)\b"
        r"|\bmove forward with your (?:application|candidacy)\b", re.I)),
    ('applied', re.compile(
        r"\bthanks?(?: you)? for (?:applying|your application|submitting)\b"
        r"|\bthanks?(?: you)? for your interest\b"
        r"|\breceived your application\b"
        r"|\bapplication (?:received|submitted)\b"
        r"|\bapplication\b[^.!?]{0,100}\b(?:has been|was|is)\s+(?:successfully\s+)?(?:received|submitted|sent)\b"
        r"|\bindeed application\b", re.I)),
]

# Sentences like "if selected, we'll schedule an interview" promise nothing.
HYPOTHETICAL = re.compile(
    r"\b(?:if|should|once|may|might|whether|when|as soon as|in the event|we will|we'll|will be)\b", re.I)
# "We will not be moving forward" is a real rejection, so this list is shorter.
HYPOTHETICAL_REJECTION = re.compile(r"\b(?:if|should|in the event|whether)\b", re.I)

JOB_CONTEXT = re.compile(
    r"\b(?:position|role|job|careers?|candidates?|recruit\w*|hiring|internship|intern|employment|resume|talent)\b", re.I)
NOT_A_JOB = re.compile(
    r"\b(?:scholarship|fellowship|admissions?|tuition|credit card|loan|mortgage|apartment|lease|rental"
    r"|visa application|insurance|grant)\b", re.I)

INTERNSHIP = re.compile(r"\b(?:internships?|interns?|co-?op)\b", re.I)
FULL_TIME = re.compile(r"\b(?:full[- ]time|new grad(?:uate)?|permanent)\b", re.I)

# Word lengths are capped because tracking URLs are huge unbroken strings, and
# an uncapped pattern takes minutes to give up on one.
COMPANY_NAME = r"\b[A-Z0-9][\w&'.\-]{0,30}(?:[ ](?:[A-Z&][\w&'.\-]{0,30}|of|and)){0,4}"
COMPANY_PATTERNS = [
    re.compile(rf"(?i:appl(?:ying|ied|ication|y)|role|position|opportunity|careers?|team)\b[^.!?]{{0,80}}?\b(?i:at)\s+({COMPANY_NAME})"),
    re.compile(rf"(?i:appl(?:ying|ied|ication|y))\b[^.!?]{{0,60}}?\b(?i:to|with)\s+(?:the\s+)?({COMPANY_NAME})"),
    re.compile(rf"(?i:interest in)\s+(?:joining\s+|working (?:at|with)\s+)?({COMPANY_NAME})"),
    re.compile(rf"^({COMPANY_NAME})\s+(?:Recruiting|Recruitment|Careers|Talent|Hiring)\b"),
]
COMPANY_SUFFIX = re.compile(r"\s+(?:Recruiting|Recruitment|Careers?|Talent(?: Acquisition)?|Hiring(?: Team)?|Team|Jobs)$")
NOT_COMPANIES = {
    'a', 'an', 'our', 'this', 'that', 'the', 'your', 'we', 'you', 'us', 'it', 'hiring', 'team', 'company',
    'hr', 'human resources', 'talent acquisition', 'recruiting', 'careers', 'jobs',
    'greenhouse', 'lever', 'workday', 'ashby', 'indeed', 'linkedin',
}
# Dropped only when comparing names, so "Acme" and "Acme Global" are one company
NAME_VARIANT = re.compile(r"\s+(?:ai|global|technologies|technology|group|holdings|labs)$", re.I)
LEGAL_SUFFIX = re.compile(r"[,\s]+(?:inc|incorporated|llc|ltd|limited|corp|corporation|co|plc|gmbh)\.?$", re.I)
ATS_LABEL_DISPLAY = re.compile(r"^(.+?)\s+(?:Recruiting|Recruitment|Careers|Talent(?: Acquisition)?|Hiring(?: Team)?|Jobs|HR)\b")
GENERIC_SUBDOMAINS = {'www', 'mail', 'email', 'e', 'em', 't', 'us', 'eu', 'hire', 'jobs', 'careers', 'notifications', 'no-reply', 'noreply'}
GENERIC_MAILBOXES = {'no-reply', 'noreply', 'do-not-reply', 'donotreply', 'notifications', 'notification', 'jobs', 'careers', 'recruiting', 'talent', 'hr', 'apply'}

ROLE_NOUN = (r"(?:Engineer|Developer|Scientist|Analyst|Researcher|Designer|Manager|Consultant|Architect"
             r"|Intern|Internship|Co-?op|Associate|Specialist|Programmer)")
ROLE_NAME = rf"\b(?:[A-Z][\w+#/&.\-]{{0,30}}[ ,]+){{0,6}}?{ROLE_NOUN}(?:[ ,\-]+(?:(?:Summer|Fall|Winter|Spring)[ ]+)?(?:\d{{4}}[ ]+)?(?:Internship|Intern|Co-?op|I{{1,3}}\b))?(?:\s*\([^)]{{1,30}}\))?"
ROLE_CONTEXT = [
    re.compile(rf"(?i:for|to|as)\s+(?:(?i:the|our|a|an)\s+)?(?:(?i:position|role)\s+(?i:of)\s+)?({ROLE_NAME})"),
    re.compile(rf"({ROLE_NAME})\s+(?i:position|role|opening|opportunity|at)\b"),
]
BARE_ROLE = re.compile(rf"\b({ROLE_NAME})\b")
# Titles of the people writing the email, not jobs she applied to
NOT_ROLES = re.compile(r"^(?:Hiring|Talent|Recruiting|Recruitment|People|Human|HR)\b", re.I)


# One email's worth of evidence. Holds no email text, sender names or subjects.
@dataclass
class Signal:
    account: str
    message_id: str
    date: str
    status: Optional[str]
    company: Optional[str]
    role: Optional[str]
    job_type: str
    reason: Optional[str] = None


def build_query(start_date=TRACKER_START_DATE):
    phrases = " OR ".join(f'"{phrase}"' for phrase in TRACKER_SEARCH_PHRASES)
    return f'after:{start_date} -from:me ({phrases})'


# Strips HTML, keeping block elements as sentence breaks.
def clean_body(raw_body):
    soup = BeautifulSoup(raw_body, "html.parser")
    for tag in soup(["style", "script", "head"]):
        tag.decompose()
    for tag in soup(["br", "p", "div", "tr", "li", "h1", "h2", "h3", "table"]):
        tag.insert_after("\n\n")
    return soup.get_text()


# Hard-wrapped plain text breaks lines mid-sentence, so only a blank line or
# end punctuation counts as a boundary.
def split_sentences(text):
    text = re.sub(r"[ \t\r\f\v\xa0]+", " ", text)
    sentences = []
    for block in re.split(r"\n\s*\n", text):
        block = re.sub(r"\s*\n\s*", " ", block).strip()
        sentences.extend(s.strip() for s in re.split(r"(?<=[.!?])\s+", block) if s.strip())
    return sentences


# Returns (status, unclear). unclear means a status phrase was seen but only in
# a hypothetical sentence and nothing firmer was found.
def classify(subject, sentences):
    lines = [subject] + sentences
    unclear = False
    for status, pattern in STATUS_PATTERNS:
        for line in lines:
            if not pattern.search(line):
                continue
            guard = HYPOTHETICAL_REJECTION if status == 'rejected' else HYPOTHETICAL
            if status != 'applied' and guard.search(line):
                unclear = True
                continue
            return status, False
    return None, unclear


def clean_company(name):
    name = LEGAL_SUFFIX.sub("", name.strip().rstrip(".,!:;-'"))
    name = COMPANY_SUFFIX.sub("", name).strip()
    if not name or name.lower() in NOT_COMPANIES or re.search(rf"\b{ROLE_NOUN}\b", name):
        return None
    return name


def registered_label(domain):
    labels = domain.split('.')
    if len(labels) >= 3 and labels[-2] in ('co', 'com', 'ac') and len(labels[-1]) == 2:
        return labels[-3]
    return labels[-2] if len(labels) >= 2 else labels[0]


def matches_domain(domain, known_domains):
    return any(domain == known or domain.endswith('.' + known) for known in known_domains)


# ATS addresses sometimes carry the employer, e.g. acme@myworkday.com or
# noreply@acme.icims.com.
def company_from_ats_address(address, domain):
    local = address.split('@')[0].lower()
    if domain in ('myworkday.com', 'myworkdayjobs.com') and local not in GENERIC_MAILBOXES:
        return local.title()
    for known in TRACKER_ATS_DOMAINS:
        if domain.endswith('.' + known):
            subdomain = domain[:-len(known) - 1].split('.')[-1]
            if subdomain not in GENERIC_SUBDOMAINS:
                return subdomain.title()
    return None


# The display name is only used when it reads like a department. A person's
# name must never end up in the tracker.
def find_company(sender, subject, sentences):
    display, address = parseaddr(sender)
    domain = address.split('@')[-1].lower() if '@' in address else ''

    third_party = (matches_domain(domain, TRACKER_ATS_DOMAINS)
                   or matches_domain(domain, TRACKER_JOB_BOARD_DOMAINS)
                   or matches_domain(domain, TRACKER_PERSONAL_DOMAINS))
    if domain and not third_party:
        return registered_label(domain).title()

    for pattern in COMPANY_PATTERNS:
        for line in [subject] + sentences:
            for match in pattern.finditer(line):
                company = clean_company(match.group(1))
                if company:
                    return company

    if matches_domain(domain, TRACKER_ATS_DOMAINS):
        company = company_from_ats_address(address, domain)
        if company:
            return company
        match = ATS_LABEL_DISPLAY.match(display.strip())
        if match:
            return clean_company(match.group(1))
    return None


def clean_role(role):
    role = re.sub(r"\s+", " ", role).strip(" ,-")
    # Requisition numbers: "REQ123456 Data Engineer", "Analyst (Job number: 2000)"
    role = re.sub(r"^[A-Z]*-?\d{4,}\s+", "", role)
    role = re.sub(r"\s*\((?:Job number:\s*)?\d+\)", "", role)
    if NOT_ROLES.match(role):
        return None
    return role


# Bare titles are trusted in a subject only. In a body they may just be the
# signature, so a body match needs surrounding words like "for the ... role".
def find_role(subject, sentences):
    for line in [subject] + sentences:
        for pattern in ROLE_CONTEXT:
            match = pattern.search(line)
            if match and clean_role(match.group(1)):
                return clean_role(match.group(1))
    match = BARE_ROLE.search(subject)
    return clean_role(match.group(1)) if match else None


def find_job_type(role, sentences):
    if role and INTERNSHIP.search(role):
        return 'internship'
    text = " ".join(sentences)
    intern, full = bool(INTERNSHIP.search(text)), bool(FULL_TIME.search(text))
    if intern == full:
        return 'unknown'
    return 'internship' if intern else 'full-time'


# Turns one Gmail message into a Signal, or None when it isn't about a job.
def parse_message(message, account):
    subject = gmail_client.get_header(message, 'Subject')
    sender = gmail_client.get_header(message, 'From')
    sentences = split_sentences(clean_body(gmail_client.get_email_body(message['payload'])))
    text = " ".join([subject] + sentences)

    if not JOB_CONTEXT.search(text):
        return None
    status, unclear = classify(subject, sentences)
    if not status and not unclear:
        return None

    role = find_role(subject, sentences)
    if NOT_A_JOB.search(text) and not role:
        return None

    date = datetime.datetime.fromtimestamp(int(message['internalDate']) / 1000).date().isoformat()
    return Signal(
        account=account,
        message_id=message['id'],
        date=date,
        status=status,
        company=find_company(sender, subject, sentences),
        role=role,
        job_type=find_job_type(role, sentences),
        reason='unclear_status' if unclear else None,
    )


def company_key(name):
    name = re.sub(r"\s*\(Tier \d+\)\s*$", "", name or "", flags=re.I)
    name = NAME_VARIANT.sub("", LEGAL_SUFFIX.sub("", name.strip()))
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def role_key(role):
    if not role:
        return ""
    role = re.sub(r"\([^)]*\)", " ", role.lower())
    role = re.sub(r"\binternship\b", "intern", role)
    return " ".join(word for word in re.findall(r"[a-z0-9+#]+", role) if not re.search(r"\d{4}", word))


def days_between(first, second):
    return abs((datetime.date.fromisoformat(first) - datetime.date.fromisoformat(second)).days)


def new_record(signal):
    return {
        'company': signal.company,
        'role': signal.role,
        'appliedAt': signal.date,
        'types': {signal.job_type},
        'latestStatus': 'applied',
        'firstResponseAt': None,
        'lastUpdateAt': signal.date,
        'accounts': {signal.account},
    }


def merge_into(record, signal):
    record['appliedAt'] = min(record['appliedAt'], signal.date)
    record['role'] = record['role'] or signal.role
    record['types'].add(signal.job_type)
    record['accounts'].add(signal.account)


# Finds the record a confirmation belongs to. Same company and role is the same
# application, which is what collapses a job seen in both accounts.
def find_applied_record(records, signal):
    same_company = [r for r in records if company_key(r['company']) == company_key(signal.company)]
    for record in same_company:
        if record['role'] and signal.role and role_key(record['role']) == role_key(signal.role):
            return record
    nearby = [
        r for r in same_company
        if (not r['role'] or not signal.role)
        and days_between(r['appliedAt'], signal.date) <= SAME_APPLICATION_DAYS
    ]
    return nearby[0] if len(nearby) == 1 else None


# Finds the record a response belongs to, or says why it can't. Never guesses:
# a response to the wrong job is worse than a line in the review list.
def find_response_record(records, signal):
    candidates = [
        r for r in records
        if company_key(r['company']) == company_key(signal.company)
        and (r['appliedAt'] is None or r['appliedAt'] <= signal.date)
    ]
    if not candidates:
        return None, 'no_application_found'
    if signal.role:
        for record in candidates:
            if record['role'] and role_key(record['role']) == role_key(signal.role):
                return record, None
        unnamed = [r for r in candidates if not r['role']]
        if len(unnamed) == 1:
            return unnamed[0], None
        return None, 'role_not_matched'
    if len(candidates) == 1:
        return candidates[0], None
    return None, 'several_applications_at_company'


def review_entry(signal, reason):
    return {
        'date': signal.date,
        'account': signal.account,
        'messageId': signal.message_id,
        'statusGuess': signal.status,
        'companyGuess': signal.company,
        'roleGuess': signal.role,
        'reason': reason,
    }


def final_status(record, today):
    if record['firstResponseAt']:
        return record['latestStatus']
    if days_between(record['appliedAt'], today) >= TRACKER_NO_RESPONSE_DAYS:
        return 'no response'
    return 'applied'


# Stable across runs, so a re-sync updates a row instead of adding one.
def record_id(*parts):
    return 'app-' + hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]


# "Account 1" style labels, so the page never holds an email address.
def account_labels(accounts):
    return {account: f"Account {number}" for number, account in enumerate(accounts, start=1)}


def source_label(accounts, labels):
    return ", ".join(sorted(labels[account] for account in accounts))


# The job title decides: a named role without "intern" in it counts as
# full-time. With no role, what the emails say is used instead.
def final_type(record):
    if record['role']:
        return 'internship' if INTERNSHIP.search(record['role']) else 'full-time'
    known = record['types'] - {'unknown'}
    return known.pop() if len(known) == 1 else 'unknown'


def finish_record(record, today, labels):
    return {
        'id': record_id(company_key(record['company']), role_key(record['role']),
                        record['appliedAt'] or record['firstResponseAt']),
        'company': record['company'],
        'role': record['role'],
        'appliedDate': record['appliedAt'],
        'type': final_type(record),
        'status': final_status(record, today),
        'lastUpdate': record['lastUpdateAt'],
        'sourceAccount': source_label(record['accounts'], labels),
        'needsReview': False,
    }


# A review row uses the same fields as an application, so the page can list it.
# reviewReason is a fixed category, never text from the email.
def finish_review(entry, labels):
    return {
        'id': record_id(entry['account'], entry['messageId']),
        'company': entry['companyGuess'],
        'role': entry['roleGuess'],
        'appliedDate': entry['date'],
        'type': 'unknown',
        'status': entry['statusGuess'] or 'applied',
        'lastUpdate': entry['date'],
        'sourceAccount': labels[entry['account']],
        'needsReview': True,
        'reviewReason': entry['reason'],
    }


# Groups signals into one record per application plus a needs-review list.
def group_signals(signals, today, labels):
    records, review = [], []
    ordered = sorted(signals, key=lambda s: s.date)

    # Confirmations first, so a response can find its application
    for signal in ordered:
        if signal.reason:
            review.append(review_entry(signal, signal.reason))
        elif signal.status == 'applied':
            if not signal.company:
                review.append(review_entry(signal, 'company_not_found'))
                continue
            record = find_applied_record(records, signal)
            if record:
                merge_into(record, signal)
            else:
                records.append(new_record(signal))

    for signal in ordered:
        if signal.reason or signal.status == 'applied':
            continue
        if not signal.company:
            review.append(review_entry(signal, 'company_not_found'))
            continue
        record, reason = find_response_record(records, signal)
        # A rejection proves she applied, even with no confirmation email. The
        # application date stays unknown rather than borrowing the rejection's.
        if signal.status == 'rejected' and reason in NEW_APPLICATION_REASONS:
            record, reason = new_record(signal), None
            record['appliedAt'] = None
            records.append(record)
        if reason:
            review.append(review_entry(signal, reason))
            continue
        record['latestStatus'] = signal.status
        record['role'] = record['role'] or signal.role
        record['firstResponseAt'] = record['firstResponseAt'] or signal.date
        record['lastUpdateAt'] = signal.date
        record['types'].add(signal.job_type)
        record['accounts'].add(signal.account)

    finished = [finish_record(record, today, labels) for record in records]
    finished.sort(key=lambda r: (r['appliedDate'] or r['lastUpdate'], r['company']), reverse=True)
    review.sort(key=lambda r: r['date'], reverse=True)
    return finished, review


def count_by(applications, field):
    counts = {}
    for application in applications:
        counts[application[field]] = counts.get(application[field], 0) + 1
    return dict(sorted(counts.items()))


def build_stats(applications, review):
    return {
        'applications': len(applications),
        'companies': len({company_key(a['company']) for a in applications}),
        'byType': count_by(applications, 'type'),
        'byStatus': count_by(applications, 'status'),
        'needsReview': len(review),
    }


# Where each account got to, plus the basic results of every email kept so far.
# Old results stay because today's rejection may belong to last year's application.
def load_state():
    if not os.path.exists(TRACKER_STATE_FILE):
        return {'scannedThrough': {}, 'signals': []}
    with open(TRACKER_STATE_FILE) as state_file:
        return json.load(state_file)


# Written to a temp file first, so a crash can't leave a half-saved state.
def save_private_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_path = f"{path}.tmp"
    gmail_client.save_private(temp_path, json.dumps(payload, indent=2))
    os.replace(temp_path, path)


# Picks the search start: everything for a new account or a full rescan,
# otherwise the last scan minus a small overlap.
def scan_start(state, account, full):
    last_scan = state['scannedThrough'].get(account)
    if full or not last_scan:
        return TRACKER_START_DATE
    start = datetime.date.fromisoformat(last_scan) - datetime.timedelta(days=TRACKER_OVERLAP_DAYS)
    return max(start.strftime('%Y/%m/%d'), TRACKER_START_DATE)


# Returns the Signals for emails not seen before. Fetching stops at limit.
def scan_account(service, account, start_date, known_ids, limit=None):
    signals = []
    scanned = 0
    for message_id in gmail_client.search_message_ids(service, build_query(start_date)):
        if message_id in known_ids:
            continue
        if limit and scanned >= limit:
            break
        scanned += 1
        signal = parse_message(gmail_client.get_message(service, message_id), account)
        if signal:
            signals.append(signal)
    print(f"{account}: searched from {start_date}, read {scanned} new emails, kept {len(signals)}")
    return signals


# Applied after loading, so changing the list needs no rescan.
def without_excluded(signals, excluded_companies):
    excluded = {company_key(company) for company in excluded_companies}
    return [s for s in signals if company_key(s.company) not in excluded]


# A --limit test keeps its results out of the state, so a later real run still
# covers everything. Other runs save after each account, and only once that
# account has finished, so a failed run is simply retried.
def update_state(state, account, today, new_signals, full, limit):
    if limit:
        return
    if full:
        state['signals'] = [s for s in state['signals'] if s['account'] != account]
    state['signals'].extend(asdict(signal) for signal in new_signals)
    state['scannedThrough'][account] = today
    save_private_json(TRACKER_STATE_FILE, state)


def run(settings, full=False, limit=None):
    accounts = settings['application_accounts']
    today = datetime.date.today().isoformat()
    state = load_state()

    # One account failing never stops the others. It keeps its saved results and
    # its old position, so the next run picks it up where it left off.
    test_signals = []
    failed = []
    for account in accounts:
        try:
            service = gmail_client.connect(account)
            known_ids = set() if full else {s['message_id'] for s in state['signals'] if s['account'] == account}
            signals = scan_account(service, account, scan_start(state, account, full), known_ids, limit)
        except (Exception, SystemExit) as error:
            print(f"{account}: skipped. {error}")
            failed.append(account)
            continue
        update_state(state, account, today, signals, full, limit)
        if limit:
            test_signals.extend(signals)

    # With nothing new at all, the last good results are left in place
    if len(failed) == len(accounts):
        print("No account could be read, so nothing was saved.")
        return failed

    # Accounts removed from settings drop out of the results
    saved = [Signal(**s) for s in state['signals'] if s['account'] in accounts]
    labels = account_labels(accounts)
    kept = without_excluded(saved + test_signals, settings.get('excluded_companies', []))
    applications, review = group_signals(kept, today, labels)
    review_rows = [finish_review(entry, labels) for entry in review]

    stats = build_stats(applications, review)
    output_file = TRACKER_TEST_OUTPUT_FILE if limit else TRACKER_OUTPUT_FILE
    save_private_json(output_file, {
        'generatedAt': today,
        'startDate': TRACKER_START_DATE,
        'noResponseDays': TRACKER_NO_RESPONSE_DAYS,
        'failedAccounts': [labels[account] for account in failed],
        'stats': stats,
        'applications': applications + review_rows,
    })
    # Gmail message IDs let her open a review item. This file is never synced.
    save_private_json(TRACKER_REVIEW_MESSAGES_FILE, {
        row['id']: {'account': entry['account'], 'messageId': entry['messageId']}
        for row, entry in zip(review_rows, review)
    })
    print(json.dumps(stats, indent=2))
    print(f"Saved to {output_file}")
    return failed

import datetime
import json
import re
from email.utils import getaddresses, parseaddr

import gmail_client
from track_applications import (
    email_link,
    matches_domain,
    record_id,
    registered_label,
    save_private_json,
)

# Import configuration
from config import RECRUITER_OUTPUT_FILE, TRACKER_PERSONAL_DOMAINS

BOUNCE_SENDER = re.compile(r"^(?:mailer-daemon|postmaster)@", re.I)
AUTOMATED_SENDER = re.compile(r"^(?:no-?reply|do-?not-?reply|notifications?)@", re.I)
AUTOMATED_SUBJECT = re.compile(
    r"automatic reply|auto-?reply|out of (?:the )?office|undeliverable|delivery status|mail delivery", re.I)


def build_query(subjects):
    wanted = " OR ".join(f'subject:"{subject}"' for subject in subjects)
    return f'in:sent ({wanted})'


def message_date(message):
    return datetime.datetime.fromtimestamp(int(message['internalDate']) / 1000).date().isoformat()


def sender_address(message):
    return parseaddr(gmail_client.get_header(message, 'From'))[1].lower()


def is_outreach(message, account, subjects):
    subject = gmail_client.get_header(message, 'Subject').strip().lower()
    return sender_address(message) == account.lower() and subject in {s.lower() for s in subjects}


# Replies that no person wrote, such as out-of-office messages, don't count.
def is_automated(message):
    return (AUTOMATED_SENDER.match(sender_address(message))
            or AUTOMATED_SUBJECT.search(gmail_client.get_header(message, 'Subject'))
            or gmail_client.get_header(message, 'Auto-Submitted').lower().startswith('auto-'))


# The company comes from the recipient's domain. Addresses themselves are never kept.
def company_from(message):
    addresses = getaddresses([gmail_client.get_header(message, 'To')])
    domain = addresses[0][1].split('@')[-1].lower() if addresses and '@' in addresses[0][1] else ''
    if not domain or matches_domain(domain, TRACKER_PERSONAL_DOMAINS):
        return None
    return registered_label(domain).title()


# Turns one Gmail thread into a row, or None if it isn't outreach. The link
# follows the same rule as applications: the email behind the current status.
def summarize_thread(thread, account, subjects):
    messages = sorted(thread.get('messages', []), key=lambda m: int(m['internalDate']))
    sent = next((m for m in messages if is_outreach(m, account, subjects)), None)
    if not sent:
        return None

    status, latest, reply_date = 'no reply', sent, None
    for message in messages:
        if int(message['internalDate']) <= int(sent['internalDate']) or sender_address(message) == account.lower():
            continue
        if BOUNCE_SENDER.match(sender_address(message)):
            if status == 'no reply':
                status, latest = 'bounced', message
        elif not is_automated(message):
            status, latest, reply_date = 'replied', message, message_date(message)
            break

    return {
        'id': record_id(account, thread['id']),
        'company': company_from(sent),
        'sentDate': message_date(sent),
        'status': status,
        'replyDate': reply_date,
        'emailUrl': email_link(account, latest['id']),
    }


def build_stats(rows):
    replied = sum(r['status'] == 'replied' for r in rows)
    bounced = sum(r['status'] == 'bounced' for r in rows)
    return {'sent': len(rows), 'replied': replied, 'bounced': bounced, 'noReply': len(rows) - replied - bounced}


def run(settings):
    account = settings['destination_email']
    subjects = [settings['draft_subject']] + settings.get('recruiter_subject_history', [])
    service = gmail_client.connect(account)

    rows = []
    for thread_id in gmail_client.search_thread_ids(service, build_query(subjects)):
        row = summarize_thread(gmail_client.get_thread(service, thread_id), account, subjects)
        if row:
            rows.append(row)
    rows.sort(key=lambda r: r['sentDate'], reverse=True)

    stats = build_stats(rows)
    save_private_json(RECRUITER_OUTPUT_FILE, {
        'generatedAt': datetime.date.today().isoformat(),
        'stats': stats,
        'outreach': rows,
    })
    print(json.dumps(stats, indent=2))
    print(f"Saved to {RECRUITER_OUTPUT_FILE}")

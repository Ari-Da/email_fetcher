import datetime
import json
import os
import shutil
import sys

import gmail_client
from config import RECRUITER_OUTPUT_FILE, TRACKER_NO_RESPONSE_DAYS, TRACKER_OUTPUT_FILE
from track_applications import company_key, days_between, role_key

SYNC_FOLDER = "data/sync"
# Saved rows hold no version, so the expected versions are tracked here. If one
# is wrong, the database refuses that write and nothing changes.
VERSIONS_FILE = f"{SYNC_FOLDER}/versions.json"
PLAN_FILE = f"{SYNC_FOLDER}/plan.json"
BATCH_SIZE = 50

# The only fields that may reach the page. Anything else in the file stays local.
PAGE_FIELDS = [
    'company', 'role', 'appliedDate', 'type', 'status', 'lastUpdate',
    'sourceAccount', 'emailUrl', 'needsReview', 'reviewReason', 'origin', 'jobsId',
]

# A skill row and a confirmation email are the same application within this window.
MATCH_DAYS = 5
ROLE_OVERLAP_NEEDED = 0.5
# Email only moves a status forward. Rejected and offer both end the process.
STATUS_RANK = {'applied': 0, 'no response': 1, 'assessment': 2, 'interview': 3, 'offer': 4, 'rejected': 4}


OUTREACH_FIELDS = ['company', 'sentDate', 'status', 'replyDate', 'emailUrl']

# Each target keeps its own files in its own folder.
TARGETS = {
    'applications': {'source': TRACKER_OUTPUT_FILE, 'rows': 'applications', 'collection': 'email_applications',
                     'fields': PAGE_FIELDS, 'folder': SYNC_FOLDER},
    'outreach': {'source': RECRUITER_OUTPUT_FILE, 'rows': 'outreach', 'collection': 'recruiter_outreach',
                 'fields': OUTREACH_FIELDS, 'folder': f"{SYNC_FOLDER}/outreach"},
}


def page_document(row, fields=PAGE_FIELDS):
    return {field: row.get(field) for field in fields}


def load_versions(path=VERSIONS_FILE):
    if not os.path.exists(path):
        return {}
    with open(path) as versions_file:
        return json.load(versions_file)


# Reads documents saved by ArtifactData's out_dir, as {id: (fields, version)}.
def load_existing(folder, versions=None):
    versions = load_versions() if versions is None else versions
    existing = {}
    if not os.path.isdir(folder):
        return existing
    for name in os.listdir(folder):
        doc_id = name[:-len('.json')]
        with open(os.path.join(folder, name)) as document:
            existing[doc_id] = (json.load(document), versions.get(doc_id, 1))
    return existing


# Records what each row's version became once a plan has been applied.
def versions_after(plan, versions):
    updated = dict(versions)
    for entry in (write for batch in plan for write in batch):
        if entry['op'] == 'delete':
            updated.pop(entry['doc_id'], None)
        else:
            updated[entry['doc_id']] = entry.get('if_version', 0) + 1
    return updated


# The page's rows can't be downloaded to files, so a local copy of them is kept
# instead, one file per row. It only changes through the writes made by these
# tools, and the apply skill adds its rows to it too.
def mirror_folder(target):
    return f"{target['folder']}/mirror/{target['collection']}"


def save_mirror_row(folder, doc_id, document):
    os.makedirs(folder, exist_ok=True)
    gmail_client.save_private(os.path.join(folder, f"{doc_id}.json"), json.dumps(document))


# Brings the local copy in line with a plan that was applied to the page.
def update_mirror(plan, folder):
    for entry in (write for batch in plan for write in batch):
        path = os.path.join(folder, f"{entry['doc_id']}.json")
        if entry['op'] == 'delete':
            if os.path.exists(path):
                os.remove(path)
        else:
            with open(entry['file_path']) as document:
                save_mirror_row(folder, entry['doc_id'], json.load(document))


def mark_applied(plan_path=PLAN_FILE, versions_path=VERSIONS_FILE, mirror=None):
    with open(plan_path) as plan_file:
        plan = json.load(plan_file)
    updated = versions_after(plan, load_versions(versions_path))
    with open(versions_path, 'w') as versions_file:
        json.dump(updated, versions_file)
    if mirror:
        update_mirror(plan, mirror)
    print("Recorded the applied plan.")


# One-time start for the local copy, from results already synced to the page.
def seed_mirror(target):
    folder = mirror_folder(target)
    if os.path.isdir(folder) and os.listdir(folder):
        raise SystemExit(f"{folder} already has rows. Remove it first to seed again.")
    with open(target['source']) as output:
        rows = json.load(output)[target['rows']]
    for row in rows:
        save_mirror_row(folder, row['id'], page_document(row, target['fields']))
    print(f"Seeded {len(rows)} rows into {folder}")


# Full replace, used for recruiter outreach: the page mirrors the results, and
# rows no longer in them are deleted.
def build_writes(rows, existing, fields=PAGE_FIELDS):
    writes = []
    current = set()
    for row in rows:
        current.add(row['id'])
        document = page_document(row, fields)
        saved, version = existing.get(row['id'], (None, None))
        if saved is None:
            writes.append({'op': 'set', 'doc_id': row['id'], 'data': document})
        elif {field: saved.get(field) for field in fields} != document:
            writes.append({'op': 'set', 'doc_id': row['id'], 'data': document, 'if_version': version})
    for doc_id, (_, version) in existing.items():
        if doc_id not in current:
            writes.append({'op': 'delete', 'doc_id': doc_id, 'if_version': version})
    return writes


# Same company, allowing for subsidiaries: "Acme Cloud Services" is Acme.
def same_company(first, second):
    a, b = company_key(first), company_key(second)
    if not a or not b:
        return False
    shorter, longer = sorted((a, b), key=len)
    return a == b or (len(shorter) >= 4 and longer.startswith(shorter))


def similar_role(first, second):
    a, b = set(role_key(first).split()), set(role_key(second).split())
    if not a or not b:
        return False
    return a <= b or b <= a or len(a & b) / len(a | b) >= ROLE_OVERLAP_NEEDED


def within_window(email_row, page_row):
    if not page_row.get('appliedDate'):
        return False
    if email_row.get('appliedDate'):
        return days_between(email_row['appliedDate'], page_row['appliedDate']) <= MATCH_DAYS
    # A rejection with no confirmation has no applied date, only its own date
    return page_row['appliedDate'] <= (email_row.get('lastUpdate') or '')


# Splits the skill rows that could be this email's application into strong
# matches (role agrees, or the email names no role) and weak ones (role differs).
def match_candidates(email_row, skill_rows):
    strong, weak = [], []
    for doc_id, saved in skill_rows:
        if not same_company(email_row.get('company'), saved.get('company')) or not within_window(email_row, saved):
            continue
        if not email_row.get('role') or similar_role(email_row['role'], saved.get('role')):
            strong.append(doc_id)
        else:
            weak.append(doc_id)
    return strong, weak


def later_status(saved_status, email_status):
    if STATUS_RANK.get(email_status, 0) > STATUS_RANK.get(saved_status, 0):
        return email_status
    return saved_status or email_status


# The skill's company, role, date and type are exact; email adds what it knows.
def fill_from_email(saved, email_row):
    merged = dict(saved)
    merged['emailUrl'] = email_row.get('emailUrl') or saved.get('emailUrl')
    merged['sourceAccount'] = email_row.get('sourceAccount') or saved.get('sourceAccount')
    merged['status'] = later_status(saved.get('status'), email_row.get('status'))
    merged['lastUpdate'] = max(saved.get('lastUpdate') or '', email_row.get('lastUpdate') or '') or None
    return merged


def email_document(row):
    document = page_document(row)
    document['origin'] = 'email'
    document['jobsId'] = None
    return document


def set_if_changed(writes, doc_id, document, saved, version):
    if saved is None:
        writes.append({'op': 'set', 'doc_id': doc_id, 'data': document})
    elif {field: saved.get(field) for field in PAGE_FIELDS} != {field: document.get(field) for field in PAGE_FIELDS}:
        writes.append({'op': 'set', 'doc_id': doc_id, 'data': document, 'if_version': version})


# Merges tracker results into the history page. Nothing is ever deleted: rows
# are only removed by hand. Rows the apply skill wrote are kept and filled in
# when their confirmation email arrives.
def merge_writes(rows, existing, today):
    writes = []
    used = set()
    skill_rows = [(doc_id, saved) for doc_id, (saved, _) in existing.items() if saved.get('origin') == 'skill']

    for row in rows:
        document = email_document(row)
        if row['id'] in existing:
            saved, version = existing[row['id']]
            used.add(row['id'])
            merged = dict(document, status=later_status(saved.get('status'), document['status']))
            set_if_changed(writes, row['id'], merged, saved, version)
            continue

        if not row.get('needsReview'):
            open_rows = [(doc_id, saved) for doc_id, saved in skill_rows if doc_id not in used]
            strong, weak = match_candidates(row, open_rows)
            if len(strong) == 1:
                saved, version = existing[strong[0]]
                used.add(strong[0])
                set_if_changed(writes, strong[0], fill_from_email(saved, row), saved, version)
                continue
            if strong or weak:
                document['needsReview'] = True
                document['reviewReason'] = 'possible_duplicate'
        set_if_changed(writes, row['id'], document, None, None)

    current = {row['id'] for row in rows}
    for doc_id, (saved, version) in existing.items():
        if doc_id in used or doc_id in current:
            continue
        if saved.get('origin') == 'skill':
            # Same 30-day rule as email rows
            waiting = saved.get('status') == 'applied' and saved.get('appliedDate')
            if waiting and days_between(saved['appliedDate'], today) >= TRACKER_NO_RESPONSE_DAYS:
                set_if_changed(writes, doc_id, dict(saved, status='no response'), saved, version)
        elif not saved.get('needsReview'):
            # An email row the latest scan no longer has. Flagged rather than
            # deleted, so it leaves the totals but stays visible.
            set_if_changed(writes, doc_id, dict(saved, origin='email', needsReview=True, reviewReason='not_in_latest_scan'),
                           saved, version)
    return writes


def batches(writes, size=BATCH_SIZE):
    return [writes[start:start + size] for start in range(0, len(writes), size)]


# Writes one file per document and a plan listing the ArtifactData batches.
def main(target_name):
    target = TARGETS[target_name]
    folder = target['folder']
    with open(target['source']) as output:
        rows = json.load(output)[target['rows']]
    existing = load_existing(mirror_folder(target), load_versions(f"{folder}/versions.json"))
    if target_name == 'applications':
        writes = merge_writes(rows, existing, datetime.date.today().isoformat())
    else:
        writes = build_writes(rows, existing, target['fields'])

    docs_folder = f"{folder}/docs"
    shutil.rmtree(docs_folder, ignore_errors=True)
    os.makedirs(docs_folder)
    plan = []
    for batch in batches(writes):
        entries = []
        for write in batch:
            entry = {'op': write['op'], 'collection': target['collection'], 'doc_id': write['doc_id']}
            if 'if_version' in write:
                entry['if_version'] = write['if_version']
            if 'data' in write:
                path = f"{docs_folder}/{write['doc_id']}.json"
                with open(path, 'w') as document:
                    json.dump(write['data'], document)
                entry['file_path'] = os.path.abspath(path)
            entries.append(entry)
        plan.append(entries)
    with open(f"{folder}/plan.json", 'w') as plan_file:
        json.dump(plan, plan_file)
    print(f"{len(rows)} rows, {len(writes)} writes in {len(plan)} batches. Saved to {folder}/plan.json")


if __name__ == "__main__":
    name = 'outreach' if 'outreach' in sys.argv else 'applications'
    target = TARGETS[name]
    if '--applied' in sys.argv:
        mark_applied(f"{target['folder']}/plan.json", f"{target['folder']}/versions.json", mirror_folder(target))
    elif '--seed' in sys.argv:
        seed_mirror(target)
    else:
        main(name)

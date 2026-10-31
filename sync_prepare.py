import json
import os
import shutil
import sys

from config import TRACKER_OUTPUT_FILE

SYNC_FOLDER = "data/sync"
EXISTING_FOLDER = f"{SYNC_FOLDER}/existing/email_applications"
PLAN_FILE = f"{SYNC_FOLDER}/plan.json"
# Saved rows hold no version, so the expected versions are tracked here. If one
# is wrong, the database refuses that write and nothing changes.
VERSIONS_FILE = f"{SYNC_FOLDER}/versions.json"
BATCH_SIZE = 50

# The only fields that may reach the page. Anything else in the file stays local.
PAGE_FIELDS = [
    'company', 'role', 'appliedDate', 'type', 'status', 'lastUpdate',
    'sourceAccount', 'emailUrl', 'needsReview', 'reviewReason',
]


def page_document(row):
    return {field: row.get(field) for field in PAGE_FIELDS}


def load_versions(path=VERSIONS_FILE):
    if not os.path.exists(path):
        return {}
    with open(path) as versions_file:
        return json.load(versions_file)


# Reads documents saved by ArtifactData's out_dir, as {id: (fields, version)}.
def load_existing(folder=EXISTING_FOLDER, versions=None):
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


def mark_applied(plan_path=PLAN_FILE, versions_path=VERSIONS_FILE):
    with open(plan_path) as plan_file:
        plan = json.load(plan_file)
    updated = versions_after(plan, load_versions(versions_path))
    with open(versions_path, 'w') as versions_file:
        json.dump(updated, versions_file)
    print("Recorded the applied plan.")


# Compares the results with the page. Rows no longer in the results are deleted,
# because a changed ID would otherwise leave a duplicate behind.
def build_writes(rows, existing):
    writes = []
    current = set()
    for row in rows:
        current.add(row['id'])
        document = page_document(row)
        saved, version = existing.get(row['id'], (None, None))
        if saved is None:
            writes.append({'op': 'set', 'doc_id': row['id'], 'data': document})
        elif {field: saved.get(field) for field in PAGE_FIELDS} != document:
            writes.append({'op': 'set', 'doc_id': row['id'], 'data': document, 'if_version': version})
    for doc_id, (_, version) in existing.items():
        if doc_id not in current:
            writes.append({'op': 'delete', 'doc_id': doc_id, 'if_version': version})
    return writes


def batches(writes, size=BATCH_SIZE):
    return [writes[start:start + size] for start in range(0, len(writes), size)]


# Writes one file per document and a plan listing the ArtifactData batches.
def main():
    with open(TRACKER_OUTPUT_FILE) as output:
        rows = json.load(output)['applications']
    writes = build_writes(rows, load_existing())

    docs_folder = f"{SYNC_FOLDER}/docs"
    shutil.rmtree(docs_folder, ignore_errors=True)
    os.makedirs(docs_folder)
    plan = []
    for batch in batches(writes):
        entries = []
        for write in batch:
            entry = {'op': write['op'], 'collection': 'email_applications', 'doc_id': write['doc_id']}
            if 'if_version' in write:
                entry['if_version'] = write['if_version']
            if 'data' in write:
                path = f"{docs_folder}/{write['doc_id']}.json"
                with open(path, 'w') as document:
                    json.dump(write['data'], document)
                entry['file_path'] = os.path.abspath(path)
            entries.append(entry)
        plan.append(entries)
    with open(PLAN_FILE, 'w') as plan_file:
        json.dump(plan, plan_file)
    print(f"{len(rows)} rows, {len(writes)} writes in {len(plan)} batches. Saved to {PLAN_FILE}")


if __name__ == "__main__":
    if '--applied' in sys.argv:
        mark_applied()
    else:
        main()

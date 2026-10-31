# Email Fetcher

A Python tool that uses the Gmail API to track job applications and their responses, and to find recruiter emails and draft replies to them.

## Overview

The project has two features, which run separately and can each use several Gmail accounts:

- **Application tracker**: finds jobs applied to and the responses to them, such as rejections, interviews and offers. It is read-only and is meant to run daily on a schedule.
- **Recruiter finder**: finds recruiter emails and creates draft replies. It is meant to run on command.

| File | Purpose |
|---|---|
| `run.py` | Starts either feature or both |
| `sync_prepare.py` | Works out what changed since the last sync to the page |
| `track_applications.py` | Application tracker |
| `fetcher.py`, `sender.py` | Recruiter finder: search, then drafts |
| `gmail_client.py` | Shared read-only Gmail login, search and email reading |
| `settings.py`, `settings.json` | Loads your private settings |
| `config.py` | Public, non-personal defaults |

## Setup

### 1. Google Cloud Console Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select an existing one
3. Enable the **Gmail API** for your project
4. Create OAuth 2.0 credentials:
   - Go to **APIs & Services** → **Credentials**
   - Click **Create Credentials** → **OAuth client ID**
   - Choose **Desktop app** as the application type
   - Download the credentials file and save it as `credentials.json` in the project root

### 2. Add Test Users

**Important**: Add every email account you'll use (tracker, recruiter and draft accounts) as **Test Users** in the OAuth consent screen:
- Go to **APIs & Services** → **OAuth consent screen**
- Under **Test users**, click **Add Users**
- Add your email addresses
- Without this step, you'll get a "Permission denied" error during authentication

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configuration

See [Settings](#settings) below.

### 5. Run

See [Running](#running). Each account opens a browser login the first time.

## Settings

Copy `settings.example.json` to `settings.json` and fill it in. Put the draft text in `draft_body.html`. Git ignores both, so addresses stay off GitHub. Never put personal values in `config.py`, which is public.

- `features`: what `python run.py` runs, and so what a schedule runs
- `recruiter_accounts`: inboxes the recruiter finder searches
- `application_accounts`: inboxes the application tracker searches
- `destination_email`: the one account that holds the drafts
- `excluded_companies`: employers the tracker leaves out, with no rescan needed

Add every address as a **Test User** (see step 2 above).

## Running

```bash
python run.py              # whatever is switched on in settings.json
python run.py tracker      # application tracker only
python run.py recruiter    # recruiter finder and drafts only
python run.py all          # both
python fetcher.py          # recruiter results only, no drafts
```

Tracker options: `--limit 50` tests on a few emails without saving progress, and `--full` rescans from the start date.

The tracker finds every application in the listed accounts since the start date. It does not depend on any job list, so a job counts whether or not it was ever saved anywhere else.

| File in `data/` | Holds | Goes to the page? |
|---|---|---|
| `applications.json` | One row per application, plus flagged needs-review rows | Yes, after review |
| `tracker_state.json` | How far each account got, and the basic results of each email kept | No |
| `applications_test.json` | Results of `--limit` test runs | No |
| `sync/` | Working files for a sync: the page's rows and the planned changes | Only the listed fields |

All of these are ignored by git. None holds email text, subjects or sender names. `applications.json` names the account each application came from and links to one Gmail message, so keep any page it syncs to private. A link only opens for someone signed in to that mailbox.

Each row has `id`, `company`, `role`, `appliedDate`, `type` (`full-time`, `internship` or `unknown`), `status` (`applied`, `rejected`, `assessment`, `interview`, `offer` or `no response`), `lastUpdate`, `sourceAccount`, `emailUrl` and `needsReview`. `emailUrl` opens the email behind the current status: the rejection or assessment if there is one, otherwise the confirmation. Needs-review rows also have `reviewReason`. A row becomes `no response` after 30 days with no reply.

How the tracker decides:

- **Connecting emails**: a confirmation and a later response for the same company and role form one application. With repeat applications, a response goes to the most recent one.
- **Counting applications**: confirmations with different dates are always separate applications, even for the same company and role. On the same date, the same company and role count once, including across accounts.
- **Type**: the job title decides. A title mentioning intern is `internship`, and any other title is `full-time`. With no role, the type comes from what the emails say, otherwise it stays `unknown`.
- **Rejections with no confirmation**: they count as applications, because she can't be rejected without applying. Their `appliedDate` is empty, meaning unknown, so they don't appear in date-based stats. Other responses with no application, such as interview invitations, go to needs review.
- **Rejections with no role at a company with several applications**: each closes the oldest unanswered application there, and any extra rejections count as new applications with an unknown date. The role may be wrong in these cases, but the rejection totals are right.

If an account fails, for example because its login is blocked, it is skipped and the other accounts still run and save. The run lists skipped accounts and ends with exit code 2, and a skipped account resumes from where it left off next time. To stop using an account, remove it from `settings.json`.

Gmail limits requests per minute, so the tracker reads at a steady pace and waits and retries when Gmail asks it to slow down. A large mailbox can take several minutes on the first run, and progress is printed as it goes.

A run with no terminal can't open a browser, so that account is skipped instead of hanging. A login in a terminal gives up after 3 minutes.

To run the tests: `python -m unittest discover -s tests`

## Syncing to a page

Python cannot write to the page directly. `sync_prepare.py` compares `data/applications.json` with a saved copy of the page's rows and plans the changes, and Claude applies them with the ArtifactData tool. Only the fields in `PAGE_FIELDS` are sent. A sync updates changed rows, adds new ones, deletes rows no longer in the results, and never touches any other collection.

## Features

Application tracker:

- Records company, role, date, full-time or internship, and latest status for each application
- Counts a job seen in several accounts once, keeping the earliest date
- Sends unclear cases to a needs-review list instead of guessing
- Only reads new email on each run

Recruiter finder:

- Filters emails by job-related keywords
- Extracts company and position information
- Creates one draft per recruiter in a single destination account, with attachments
- Supports HTML email formatting with clickable links

## Security

- Tokens, credentials, settings, draft text and results are excluded from version control (see `.gitignore`)
- Never commit `credentials.json`, `settings.json`, `draft_body.html`, or anything in `tokens/` or `data/`
- Logging in happens in Google's own browser page. The scripts never see a password
- `gmail_client.py` refuses any token that can do more than read mail. Only `sender.py` has draft access, and it only creates drafts. Google has no drafts-only permission, so that token could technically send mail
- Remove access at any time at https://myaccount.google.com/permissions

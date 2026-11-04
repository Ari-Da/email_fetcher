import json
import urllib.error
import urllib.request

# Import configuration
from config import OLLAMA_URL, RECRUITER_MODEL

MAX_BODY_CHARACTERS = 2000

# Picking a category works far better than yes or no: with a plain yes-or-no
# question, this small model answered no to every email.
PROMPT = """Sort this email into exactly one category.

recruiter_outreach: a recruiter, hiring manager or founder writes to the reader about a job opening or asks if they are interested.
interview_process: scheduling, confirming or preparing for an interview the reader already has.
application_update: an update about a job the reader applied to, including rejections.
marketing: newsletters, job alerts, tips, product or resume-site emails.
other: anything else, including university, alumni and volunteering emails.

Examples:
"I'm a recruiter at Acme and your profile stood out. Open to a chat about our engineer role?" -> recruiter_outreach
"Please pick a time for your recruiter screen." -> interview_process
"We have decided to move forward with other candidates." -> application_update
"Here are this week's top jobs for you." -> marketing
"Alumni, help us recruit future students." -> other

From: {sender}
Subject: {subject}
Body:
{body}

Reply with JSON only, like {{"category": "other"}}."""


class ModelUnavailable(Exception):
    pass


def request_json(path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(f"{OLLAMA_URL}{path}", data=data, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.load(response)
    except (urllib.error.URLError, TimeoutError) as error:
        raise ModelUnavailable(f"Ollama is not answering at {OLLAMA_URL}. Open the Ollama app and try again.") from error


# Stops a run before any drafting if the check can't happen.
def require_model():
    models = [model['name'] for model in request_json('/api/tags').get('models', [])]
    if RECRUITER_MODEL not in models:
        raise ModelUnavailable(f"Model {RECRUITER_MODEL} is missing. Run: ollama pull {RECRUITER_MODEL}")


# True only for a clear yes. The email text goes to Ollama on this Mac only.
def is_cold_outreach(sender, subject, body):
    reply = request_json('/api/generate', {
        'model': RECRUITER_MODEL,
        'prompt': PROMPT.format(sender=sender, subject=subject, body=body[:MAX_BODY_CHARACTERS]),
        'stream': False,
        'format': 'json',
        'options': {'temperature': 0},
    })
    try:
        return json.loads(reply.get('response', '')).get('category') == 'recruiter_outreach'
    except (ValueError, AttributeError):
        return False

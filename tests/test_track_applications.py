import base64
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gmail_client
import run as run_module
import fetcher
import recruiter_classifier
import recruiter_state
import recruiter_stats
import sender
import settings as settings_module
import sync_prepare
import track_applications as tracker
from config import GMAIL_READONLY_SCOPES, GMAIL_COMPOSE_SCOPES
from track_applications import Signal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACCOUNT_A = "a@example.com"
ACCOUNT_B = "b@example.com"
TODAY = "2026-10-30"


def encode(text):
    return base64.urlsafe_b64encode(text.encode()).decode()


def message(sender, subject, body, mime="text/plain", date_ms=1760000000000, message_id="m1"):
    return {
        'id': message_id,
        'internalDate': str(date_ms),
        'payload': {
            'mimeType': mime,
            'headers': [{'name': 'From', 'value': sender}, {'name': 'Subject', 'value': subject}],
            'body': {'data': encode(body)},
        },
    }


def signal(status, date, company="Acme", role=None, job_type="unknown", account=ACCOUNT_A, reason=None):
    return Signal(account, f"id-{date}-{status}", date, status, company, role, job_type, reason)


def parse(sender, subject, body, **kwargs):
    return tracker.parse_message(message(sender, subject, body, **kwargs), ACCOUNT_A)


class ClassifyTests(unittest.TestCase):
    def status(self, subject, body):
        return tracker.classify(subject, tracker.split_sentences(body))[0]

    def test_confirmation(self):
        self.assertEqual(self.status("Thank you for applying", "We will review your resume."), 'applied')

    def test_rejection_opening_with_thanks_is_rejected(self):
        body = "Thank you for applying. We have decided to move forward with other candidates."
        self.assertEqual(self.status("Your application", body), 'rejected')

    def test_polite_rejection_wording(self):
        for body in ["Thank you for your interest. We think you are not the strongest fit here, and this job isn\u2019t a good match.",
                     "Thanks for applying. We won\u2019t be moving forward with your application.",
                     "Thank you for your interest. You are not quite the right fit for this role.",
                     "Thanks for applying. We have decided to go in a different direction.",
                     "Thanks for applying. We chose people who more closely match the position.",
                     "Thanks for applying. You are no longer being considered.",
                     "Thanks for applying. This position has been closed.",
                     "After review, we have decided to progress with other candidates for this role.",
                     "We have selected other applicants for this role."]:
            self.assertEqual(self.status("Your application", body), 'rejected', body)

    def test_ordinary_confirmation_is_not_a_rejection(self):
        for body in ["Thanks for applying. We will see if your experience is a fit for the role.",
                     "Thank you for your interest. We look for the best match for each role.",
                     "Thanks for applying. If this role is not a match, we will keep your resume."]:
            self.assertEqual(self.status("Application received", body), 'applied', body)

    def test_will_not_move_forward_is_rejected(self):
        self.assertEqual(self.status("Update", "We will not be moving forward with your application."), 'rejected')

    def test_confirmation_promising_interview_stays_applied(self):
        body = "Thank you for applying. If selected, we will schedule an interview."
        self.assertEqual(self.status("Application received", body), 'applied')

    def test_future_assessment_boilerplate_stays_applied(self):
        body = "Thanks for applying. You may be asked to complete an online assessment."
        self.assertEqual(self.status("Application received", body), 'applied')

    def test_real_interview(self):
        self.assertEqual(self.status("Next steps", "We would like to invite you to an interview."), 'interview')

    def test_interview_process_boilerplate_is_not_an_interview(self):
        body = "Thanks for applying. If selected, your interview will be with our team."
        self.assertEqual(self.status("Application received", body), 'applied')

    def test_assessment(self):
        self.assertEqual(self.status("Next steps", "Please complete the online assessment on HackerRank."), 'assessment')

    def test_offer(self):
        self.assertEqual(self.status("Great news", "We are pleased to offer you the position."), 'offer')

    def test_equal_opportunity_boilerplate_is_not_an_offer(self):
        self.assertIsNone(self.status("News", "We offer competitive benefits and equal opportunity."))

    def test_hypothetical_only_is_unclear(self):
        status, unclear = tracker.classify("Hello", ["Let me know if you can schedule an interview."])
        self.assertIsNone(status)
        self.assertTrue(unclear)


class ParseMessageTests(unittest.TestCase):
    def test_company_comes_from_own_domain(self):
        result = parse("Careers <careers@acme.com>", "Thank you for applying", "We got your resume for the role.")
        self.assertEqual(result.company, "Acme")

    def test_greenhouse_is_not_the_company(self):
        result = parse("Globex <no-reply@us.greenhouse-mail.io>",
                       "Thank you for applying to Globex",
                       "Thank you for applying to Globex. We review every resume.")
        self.assertEqual(result.company, "Globex")

    def test_intern_after_season_stays_in_the_role(self):
        result = parse("careers@acme.com", "Thanks for applying", "Thanks for applying for the Software Engineer - Summer 2026 Intern role.")
        self.assertIn("Intern", result.role)
        self.assertEqual(result.job_type, "internship")

    def test_company_name_clean_up(self):
        self.assertEqual(tracker.clean_company("Acme and"), "Acme")
        self.assertEqual(tracker.clean_company("Acme We"), "Acme")
        self.assertEqual(tracker.clean_company("123 Acme Systems"), "Acme Systems")
        self.assertIsNone(tracker.clean_company("REQ123456 Summer"))
        self.assertIsNone(tracker.clean_company("123"))
        self.assertIsNone(tracker.clean_company("Talent"))

    def test_join_and_hq_domains(self):
        self.assertEqual(tracker.registered_label("joinacme.com"), "acme")
        self.assertEqual(tracker.registered_label("acmehq.com"), "acme")
        self.assertEqual(tracker.company_key("Acme Health"), tracker.company_key("acmehealth"))

    def test_assessment_and_matching_platforms_are_not_employers(self):
        for address in ["no-reply@hackerrankforwork.com", "no-reply@ripplematch.com", "no-reply@njoyn.com"]:
            result = parse(address, "Thanks for applying", "Thanks for applying to Globex. We review every role.")
            self.assertEqual(result.company, "Globex")

    def test_hr_is_not_a_company(self):
        result = parse("no-reply@ashbyhq.com", "Thanks for applying", "Your application was sent to HR. We will review the role.")
        self.assertIsNone(result.company)

    def test_tal_is_a_hiring_system(self):
        result = parse("no-reply@tal.net", "Thanks for applying", "Thanks for applying to Globex. We review every role.")
        self.assertEqual(result.company, "Globex")

    def test_role_at_company_through_ats(self):
        result = parse("no-reply@ashbyhq.com", "Thanks for applying",
                       "Thanks for applying for the Software Engineer Intern role at Initech.")
        self.assertEqual(result.company, "Initech")
        self.assertEqual(result.role, "Software Engineer Intern")
        self.assertEqual(result.job_type, "internship")

    def test_linkedin_confirmation(self):
        result = parse("LinkedIn <jobs-noreply@linkedin.com>", "Alex, your application was sent to Hooli",
                       "Your application was sent to Hooli. Machine Learning Engineer position.")
        self.assertEqual(result.status, "applied")
        self.assertEqual(result.company, "Hooli")

    def test_workday_tenant_from_address(self):
        result = parse("Workday <umbrella@myworkday.com>", "Application received",
                       "We received your application for this job.")
        self.assertEqual(result.company, "Umbrella")

    def test_person_display_name_is_never_the_company(self):
        result = parse("Jane Doe <no-reply@lever.co>", "Thank you for applying", "We reviewed your resume for the role.")
        self.assertIsNone(result.company)

    def test_hiring_manager_is_not_a_role(self):
        result = parse("careers@acme.com", "Application received",
                       "Thanks for applying to this job.\n\nKind regards,\n\nHiring Manager")
        self.assertIsNone(result.role)

    def test_type_unknown_when_not_stated(self):
        self.assertEqual(parse("careers@acme.com", "Application received", "Thanks for this role.").job_type, "unknown")

    def test_full_time_stated(self):
        result = parse("careers@acme.com", "Application received", "Thanks for this full-time role.")
        self.assertEqual(result.job_type, "full-time")

    def test_credit_card_application_is_dropped(self):
        self.assertIsNone(parse("cards@bank.com", "Thank you for applying", "Your credit card application. Career tips inside."))

    def test_non_job_email_is_dropped(self):
        self.assertIsNone(parse("x@shop.com", "Thank you for your application", "Your store account is ready."))

    def test_cold_recruiter_pitch_is_dropped(self):
        self.assertIsNone(parse("Sam <sam@staffing.com>", "Exciting role", "I have a great job for you. Are you free?"))

    def test_html_body(self):
        html = "<html><body><p>Thanks for applying to the <b>Data Scientist</b> role.</p></body></html>"
        self.assertEqual(parse("careers@acme.com", "Hello", html, mime="text/html").role, "Data Scientist")

    def test_hard_wrapped_plain_text(self):
        result = parse("careers@acme.com", "Update", "We have decided to move forward\nwith other candidates for this role.")
        self.assertEqual(result.status, "rejected")

    def test_signal_holds_no_email_text(self):
        result = parse("Jane Doe <jane@acme.com>", "SECRET SUBJECT", "Thanks for applying to this role. SECRET BODY")
        self.assertNotIn("SECRET", json.dumps(result.__dict__))
        self.assertNotIn("Jane", json.dumps(result.__dict__))


class SlowInputTests(unittest.TestCase):
    def test_huge_unbroken_string_finishes_quickly(self):
        start = time.time()
        sentences = tracker.split_sentences("A" * 200000)
        tracker.find_role("Subject", sentences)
        tracker.find_company("x@ashbyhq.com", "Subject", sentences)
        self.assertLess(time.time() - start, 2)


class GroupingTests(unittest.TestCase):
    def group(self, signals):
        return tracker.group_signals(signals, TODAY)

    def test_same_job_on_one_date_in_both_accounts_counts_once(self):
        records, _ = self.group([
            signal('applied', '2026-02-01', role='Data Analyst', account=ACCOUNT_B),
            signal('applied', '2026-02-01', role='Data Analyst', account=ACCOUNT_A),
        ])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['appliedDate'], '2026-02-01')
        self.assertEqual(records[0]['sourceAccount'], f'{ACCOUNT_A}, {ACCOUNT_B}')

    def test_two_roles_at_one_company_stay_separate(self):
        records, _ = self.group([
            signal('applied', '2026-02-01', role='Data Analyst'),
            signal('applied', '2026-02-01', role='Data Engineer'),
        ])
        self.assertEqual(len(records), 2)

    def test_latest_status_follows_date(self):
        records, _ = self.group([
            signal('rejected', '2026-03-10'),
            signal('interview', '2026-03-01'),
            signal('applied', '2026-02-01'),
        ])
        self.assertEqual(records[0]['status'], 'rejected')
        self.assertEqual(records[0]['lastUpdate'], '2026-03-10')

    def test_rejection_without_role_closes_oldest_unanswered_application(self):
        records, review = self.group([
            signal('applied', '2026-02-05', role='Data Engineer'),
            signal('applied', '2026-02-01', role='Data Analyst'),
            signal('rejected', '2026-03-01'),
        ])
        statuses = {r['role']: r['status'] for r in records}
        self.assertEqual(statuses, {'Data Analyst': 'rejected', 'Data Engineer': 'no response'})
        self.assertEqual(review, [])

    def test_extra_rejections_become_undated_applications(self):
        records, review = self.group([
            signal('applied', '2026-02-01', role='Data Analyst'),
            signal('applied', '2026-02-01', role='Data Engineer'),
            signal('rejected', '2026-03-01'),
            signal('rejected', '2026-03-02'),
            signal('rejected', '2026-03-03'),
        ])
        self.assertEqual(len(records), 3)
        self.assertEqual(sum(r['status'] == 'rejected' for r in records), 3)
        self.assertEqual(sum(r['appliedDate'] is None for r in records), 1)
        self.assertEqual(review, [])

    def test_same_day_undated_rejections_keep_separate_ids(self):
        records, _ = self.group([
            signal('applied', '2026-02-01', role='Data Analyst'),
            signal('applied', '2026-02-01', role='Data Engineer'),
            signal('rejected', '2026-03-01'), signal('rejected', '2026-03-01'),
            signal('rejected', '2026-03-05'), signal('rejected', '2026-03-05'),
        ])
        self.assertEqual(len({r['id'] for r in records}), len(records))

    def test_rejection_without_role_attaches_to_only_application(self):
        records, review = self.group([signal('applied', '2026-02-01', role='Data Analyst'), signal('rejected', '2026-03-01')])
        self.assertEqual(records[0]['status'], 'rejected')
        self.assertEqual(review, [])

    def test_interview_for_job_never_applied_to_stays_out_of_stats(self):
        records, review = self.group([signal('interview', '2026-03-01', company='Cold Co')])
        self.assertEqual(records, [])
        self.assertEqual(review[0]['reason'], 'no_application_found')

    def test_response_about_another_role_is_not_counted(self):
        records, review = self.group([
            signal('applied', '2026-02-01', role='Data Analyst'),
            signal('interview', '2026-03-01', role='Sales Manager'),
        ])
        self.assertEqual(records[0]['status'], 'no response')
        self.assertEqual(review[0]['reason'], 'role_not_matched')

    def test_response_before_application_is_not_counted(self):
        records, review = self.group([signal('applied', '2026-03-01'), signal('interview', '2026-02-01')])
        self.assertEqual(records[0]['status'], 'no response')
        self.assertEqual(review[0]['reason'], 'no_application_found')

    def test_same_role_on_different_dates_are_two_applications(self):
        for later in ['2026-02-02', '2026-09-01']:
            records, _ = self.group([
                signal('applied', '2026-02-01', role='Data Analyst'),
                signal('applied', later, role='Data Analyst'),
            ])
            self.assertEqual(len(records), 2)

    def test_response_goes_to_the_most_recent_repeat_application(self):
        records, _ = self.group([
            signal('applied', '2026-02-01', role='Data Analyst'),
            signal('applied', '2026-05-01', role='Data Analyst'),
            signal('rejected', '2026-06-01', role='Data Analyst'),
        ])
        statuses = {r['appliedDate']: r['status'] for r in records}
        self.assertEqual(statuses, {'2026-02-01': 'no response', '2026-05-01': 'rejected'})

    def test_link_opens_the_email_behind_the_status(self):
        applied = signal('applied', '2026-09-01')
        rejected = signal('rejected', '2026-09-20', account=ACCOUNT_B)
        records, _ = self.group([applied])
        self.assertEqual(records[0]['emailUrl'], tracker.email_link(ACCOUNT_A, applied.message_id))
        self.assertEqual(records[0]['status'], 'no response')
        records, _ = self.group([applied, rejected])
        self.assertEqual(records[0]['emailUrl'], tracker.email_link(ACCOUNT_B, rejected.message_id))

    def test_email_link_names_the_account_and_message(self):
        self.assertEqual(tracker.email_link(ACCOUNT_A, 'abc'), 'https://mail.google.com/mail/?authuser=a%40example.com#all/abc')

    def test_same_day_duplicate_without_role_merges(self):
        records, _ = self.group([
            signal('applied', '2026-02-01', role='Data Analyst'),
            signal('applied', '2026-02-01'),
        ])
        self.assertEqual(len(records), 1)

    def test_no_response_after_thirty_days(self):
        records, _ = self.group([signal('applied', '2026-09-30')])
        self.assertEqual(records[0]['status'], 'no response')

    def test_waiting_inside_thirty_days(self):
        records, _ = self.group([signal('applied', '2026-10-01')])
        self.assertEqual(records[0]['status'], 'applied')

    def test_title_beats_what_the_email_says(self):
        records, _ = self.group([signal('applied', '2026-02-01', role='Data Analyst', job_type='internship')])
        self.assertEqual(records[0]['type'], 'full-time')

    def test_email_decides_type_when_there_is_no_role(self):
        records, _ = self.group([
            signal('applied', '2026-02-01', job_type='internship'),
            signal('rejected', '2026-02-05'),
        ])
        self.assertEqual(records[0]['type'], 'internship')

    def test_conflicting_emails_with_no_role_stay_unknown(self):
        records, _ = self.group([
            signal('applied', '2026-02-01', job_type='internship'),
            signal('applied', '2026-02-01', job_type='full-time'),
        ])
        self.assertEqual(records[0]['type'], 'unknown')

    def test_intern_in_title_is_internship(self):
        records, _ = self.group([signal('applied', '2026-02-01', role='Data Analyst Intern')])
        self.assertEqual(records[0]['type'], 'internship')

    def test_named_role_without_intern_is_full_time(self):
        records, _ = self.group([signal('applied', '2026-02-01', role='Data Analyst')])
        self.assertEqual(records[0]['type'], 'full-time')

    def test_later_email_supplies_the_role_for_type(self):
        records, _ = self.group([signal('applied', '2026-02-01'), signal('rejected', '2026-03-01', role='Data Analyst')])
        self.assertEqual(records[0]['type'], 'full-time')

    def test_no_role_and_nothing_stated_stays_unknown(self):
        records, _ = self.group([signal('applied', '2026-02-01')])
        self.assertEqual(records[0]['type'], 'unknown')

    def test_rejection_without_confirmation_counts_once(self):
        records, review = self.group([signal('rejected', '2026-03-01'), signal('rejected', '2026-03-05')])
        self.assertEqual(len(records), 1)
        self.assertIsNone(records[0]['appliedDate'])
        self.assertEqual(records[0]['lastUpdate'], '2026-03-05')
        self.assertEqual(records[0]['status'], 'rejected')
        self.assertEqual(review, [])

    def test_unknown_date_rows_sort_and_keep_separate_ids(self):
        records, _ = self.group([
            signal('rejected', '2026-03-01', company='Acme'),
            signal('rejected', '2026-03-01', company='Globex'),
            signal('applied', '2026-02-01', company='Initech'),
        ])
        self.assertEqual(len({r['id'] for r in records}), 3)

    def test_rejection_for_another_role_is_its_own_application(self):
        records, review = self.group([
            signal('applied', '2026-02-01', role='Data Analyst'),
            signal('rejected', '2026-03-01', role='Sales Manager'),
        ])
        self.assertEqual(len(records), 2)
        self.assertEqual(review, [])

    def test_excluded_company_is_dropped(self):
        signals = [signal('applied', '2026-02-01', company='Campus'), signal('applied', '2026-02-01', company='Acme')]
        self.assertEqual([s.company for s in tracker.without_excluded(signals, ['CAMPUS'])], ['Acme'])

    def test_missing_company_goes_to_review(self):
        _, review = self.group([signal('applied', '2026-02-01', company=None)])
        self.assertEqual(review[0]['reason'], 'company_not_found')

    def test_unclear_goes_to_review(self):
        records, review = self.group([signal(None, '2026-02-01', reason='unclear_status')])
        self.assertEqual(records, [])
        self.assertEqual(review[0]['reason'], 'unclear_status')

    def test_company_name_variants_are_one_company(self):
        records, review = self.group([
            signal('applied', '2026-02-01', company='Acme', role='Data Analyst'),
            signal('rejected', '2026-03-01', company='Acme Global'),
        ])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['status'], 'rejected')
        self.assertEqual(review, [])

    def test_roleless_duplicate_merges_with_other_jobs_at_company(self):
        records, _ = self.group([
            signal('applied', '2026-02-01', role='Data Analyst'),
            signal('applied', '2026-03-01', role='Data Engineer'),
            signal('applied', '2026-03-01'),
        ])
        self.assertEqual(len(records), 2)

    def test_requisition_number_does_not_split_a_role(self):
        records, _ = self.group([
            signal('applied', '2026-02-01', role=tracker.clean_role('REQ123456 Data Engineer')),
            signal('applied', '2026-02-01', role=tracker.clean_role('Data Engineer (Job number: 987654321)')),
        ])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['role'], 'Data Engineer')

    def test_id_includes_applied_date(self):
        first, _ = self.group([signal('applied', '2026-02-01', role='Data Analyst')])
        second, _ = self.group([signal('applied', '2026-02-09', role='Data Analyst')])
        self.assertNotEqual(first[0]['id'], second[0]['id'])

    def test_record_has_only_the_agreed_fields(self):
        records, _ = self.group([signal('applied', '2026-02-01', role='Data Analyst')])
        self.assertEqual(set(records[0]), {'id', 'company', 'role', 'appliedDate', 'type', 'status',
                                           'lastUpdate', 'sourceAccount', 'emailUrl', 'needsReview'})

    def test_roleless_applications_get_separate_ids(self):
        records, _ = self.group([signal('applied', '2026-02-01'), signal('applied', '2026-05-01')])
        self.assertEqual(len({r['id'] for r in records}), 2)

    def test_record_id_is_stable_across_runs(self):
        first, _ = self.group([signal('applied', '2026-02-01', role='Data Analyst')])
        second, _ = self.group([signal('applied', '2026-02-01', role='Data Analyst (Summer)')])
        self.assertEqual(first[0]['id'], second[0]['id'])


class LoginTests(unittest.TestCase):
    def test_no_terminal_means_no_browser_login(self):
        with mock.patch.object(gmail_client, 'can_open_browser', return_value=False), \
                mock.patch.object(gmail_client, 'token_path', return_value='/nonexistent/token.json'):
            with self.assertRaises(gmail_client.LoginNeeded):
                gmail_client.get_credentials(ACCOUNT_A)


    def test_blocked_login_gives_up_instead_of_hanging(self):
        flow = mock.Mock()
        flow.run_local_server.side_effect = AttributeError("no redirect came back")
        with mock.patch.object(gmail_client, 'can_open_browser', return_value=True), \
                mock.patch.object(gmail_client, 'token_path', return_value='/nonexistent/token.json'), \
                mock.patch.object(gmail_client.InstalledAppFlow, 'from_client_secrets_file', return_value=flow):
            with self.assertRaises(gmail_client.LoginNeeded):
                gmail_client.get_credentials(ACCOUNT_A)
        self.assertEqual(flow.run_local_server.call_args.kwargs['timeout_seconds'], gmail_client.LOGIN_TIMEOUT_SECONDS)


class SettingsTests(unittest.TestCase):
    def write(self, settings):
        handle = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False)
        json.dump(settings, handle)
        handle.close()
        self.addCleanup(os.remove, handle.name)
        return handle.name

    def example(self):
        with open(os.path.join(ROOT, 'settings.example.json')) as example:
            return json.load(example)

    def test_example_is_valid(self):
        loaded = settings_module.load_settings(self.write(self.example()))
        self.assertTrue(loaded['features']['application_tracker'])

    def test_missing_file_explains_itself(self):
        with self.assertRaises(SystemExit):
            settings_module.load_settings('/nonexistent/settings.json')

    def test_missing_key_is_rejected(self):
        settings = self.example()
        del settings['destination_email']
        with self.assertRaises(SystemExit):
            settings_module.load_settings(self.write(settings))

    def test_bad_address_is_rejected(self):
        settings = self.example()
        settings['application_accounts'] = ['not-an-address']
        with self.assertRaises(SystemExit):
            settings_module.load_settings(self.write(settings))

    def test_account_list_must_be_a_list(self):
        settings = self.example()
        settings['recruiter_accounts'] = ACCOUNT_A
        with self.assertRaises(SystemExit):
            settings_module.load_settings(self.write(settings))


class FeatureSwitchTests(unittest.TestCase):
    def chosen(self, command, tracker_on=True, recruiter_on=False):
        return run_module.choose_features(command, {'application_tracker': tracker_on, 'recruiter': recruiter_on})

    def test_schedule_follows_settings(self):
        self.assertEqual(self.chosen(None), ['tracker'])
        self.assertEqual(self.chosen(None, tracker_on=False, recruiter_on=True), ['recruiter'])
        self.assertEqual(self.chosen(None, tracker_on=True, recruiter_on=True), ['tracker', 'recruiter'])
        self.assertEqual(self.chosen(None, tracker_on=False), [])

    def test_command_overrides_settings(self):
        self.assertEqual(self.chosen('recruiter'), ['recruiter'])
        self.assertEqual(self.chosen('tracker', tracker_on=False), ['tracker'])
        self.assertEqual(self.chosen('all'), ['tracker', 'recruiter'])


class FakeGmail:
    def __init__(self, ids):
        self.ids, self.read, self.queries = ids, [], []

    def search(self, service, query):
        self.queries.append(query)
        return iter(self.ids)

    def get(self, service, message_id):
        self.read.append(message_id)
        if message_id == 'broken':
            raise RuntimeError("Gmail failed")
        return message("careers@acme.com", "Thank you for applying", "Thanks for applying to this role.", message_id=message_id)


class ResumeTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.state_file = os.path.join(folder.name, 'state.json')
        self.output_file = os.path.join(folder.name, 'applications.json')
        self.test_file = os.path.join(folder.name, 'test.json')
        for name, value in [('TRACKER_STATE_FILE', self.state_file), ('TRACKER_OUTPUT_FILE', self.output_file),
                            ('TRACKER_TEST_OUTPUT_FILE', self.test_file)]:
            patcher = mock.patch.object(tracker, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(gmail_client, 'connect', return_value=object())
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_tracker(self, ids, accounts=(ACCOUNT_A,), **options):
        gmail = FakeGmail(ids)
        with mock.patch.object(gmail_client, 'search_message_ids', gmail.search), \
                mock.patch.object(gmail_client, 'get_message', gmail.get):
            tracker.run({'application_accounts': list(accounts)}, **options)
        return gmail

    def test_first_run_starts_from_start_date(self):
        gmail = self.run_tracker(['1'])
        self.assertIn("after:2025/09/01", gmail.queries[0])

    def test_second_run_resumes_with_overlap_and_skips_seen_emails(self):
        self.run_tracker(['1'])
        gmail = self.run_tracker(['1', '2'])
        last_scan = datetime.date.today() - datetime.timedelta(days=2)
        self.assertIn(f"after:{last_scan.strftime('%Y/%m/%d')}", gmail.queries[0])
        self.assertEqual(gmail.read, ['2'])

    def test_old_application_still_gets_a_new_rejection(self):
        tracker.save_private_json(self.state_file, {'scannedThrough': {ACCOUNT_A: '2026-01-01'}, 'signals': [
            {'account': ACCOUNT_A, 'message_id': 'old', 'date': '2025-10-01', 'status': 'applied',
             'company': 'Acme', 'role': None, 'job_type': 'unknown', 'reason': None}]})
        gmail = FakeGmail([])
        rejection = message("careers@acme.com", "Update", "We decided to move forward with other candidates for this role.",
                            date_ms=1767225600000, message_id='new')
        with mock.patch.object(gmail_client, 'search_message_ids', lambda s, q: iter(['new'])), \
                mock.patch.object(gmail_client, 'get_message', lambda s, i: rejection):
            tracker.run({'application_accounts': [ACCOUNT_A]})
        with open(self.output_file) as output:
            self.assertEqual(json.load(output)['applications'][0]['status'], 'rejected')

    def test_failed_account_keeps_its_old_position(self):
        self.run_tracker(['1'], accounts=[ACCOUNT_A])
        with open(self.state_file) as state_file:
            before = json.load(state_file)
        self.run_tracker(['broken'], accounts=[ACCOUNT_A])
        with open(self.state_file) as state_file:
            self.assertEqual(json.load(state_file), before)

    def connect_failing_for(self, bad_account, error):
        def connect(account):
            if account == bad_account:
                raise error
            return object()
        return mock.patch.object(gmail_client, 'connect', connect)

    def test_blocked_account_does_not_stop_the_other(self):
        for error in [gmail_client.LoginNeeded("blocked"), SystemExit("wrong mailbox"), RuntimeError("network")]:
            for bad, good in [(ACCOUNT_A, ACCOUNT_B), (ACCOUNT_B, ACCOUNT_A)]:
                if os.path.exists(self.state_file):
                    os.remove(self.state_file)
                gmail = FakeGmail(['1'])
                with self.connect_failing_for(bad, error), \
                        mock.patch.object(gmail_client, 'search_message_ids', gmail.search), \
                        mock.patch.object(gmail_client, 'get_message', gmail.get):
                    failed = tracker.run({'application_accounts': [ACCOUNT_A, ACCOUNT_B]})
                self.assertEqual(failed, [bad])
                with open(self.output_file) as output:
                    saved = json.load(output)
                self.assertEqual(saved['failedAccounts'], [bad])
                self.assertEqual(saved['applications'][0]['sourceAccount'], good)

    def test_skipped_account_keeps_earlier_results_in_the_output(self):
        self.run_tracker(['1'], accounts=[ACCOUNT_A])
        with self.connect_failing_for(ACCOUNT_A, gmail_client.LoginNeeded("expired")):
            self.run_tracker([], accounts=[ACCOUNT_A, ACCOUNT_B])
        with open(self.output_file) as output:
            self.assertEqual(json.load(output)['applications'][0]['sourceAccount'], ACCOUNT_A)

    def test_all_accounts_failing_leaves_last_results_alone(self):
        self.run_tracker(['1'])
        with open(self.output_file) as output:
            before = output.read()
        with self.connect_failing_for(ACCOUNT_A, gmail_client.LoginNeeded("expired")):
            self.run_tracker([])
        with open(self.output_file) as output:
            self.assertEqual(output.read(), before)

    def test_new_account_starts_from_start_date(self):
        self.run_tracker(['1'], accounts=[ACCOUNT_A])
        gmail = self.run_tracker([], accounts=[ACCOUNT_A, ACCOUNT_B])
        self.assertIn("after:2025/09/01", gmail.queries[1])

    def test_limit_saves_no_progress(self):
        self.run_tracker(['1', '2', '3'], limit=1)
        self.assertFalse(os.path.exists(self.state_file))
        self.assertFalse(os.path.exists(self.output_file))
        self.assertTrue(os.path.exists(self.test_file))

    def test_full_rescan_rereads_everything(self):
        self.run_tracker(['1'])
        gmail = self.run_tracker(['1'], full=True)
        self.assertIn("after:2025/09/01", gmail.queries[0])
        self.assertEqual(gmail.read, ['1'])
        with open(self.state_file) as state_file:
            self.assertEqual(len(json.load(state_file)['signals']), 1)

    def test_removed_account_drops_out_of_results(self):
        self.run_tracker(['1'], accounts=[ACCOUNT_A])
        self.run_tracker([], accounts=[ACCOUNT_B])
        with open(self.output_file) as output:
            self.assertEqual(json.load(output)['applications'], [])

    def test_output_names_the_real_account_but_no_sender(self):
        self.run_tracker(['1'])
        with open(self.output_file) as output:
            text = output.read()
        self.assertIn(ACCOUNT_A, text)
        self.assertNotIn("careers@", text)

    def test_review_items_sit_in_the_same_list_flagged(self):
        interview = message("careers@acme.com", "Update", "We would like to invite you to an interview for this role.", message_id='cold')
        with mock.patch.object(gmail_client, 'search_message_ids', lambda s, q: iter(['cold'])), \
                mock.patch.object(gmail_client, 'get_message', lambda s, i: interview):
            tracker.run({'application_accounts': [ACCOUNT_A]})
        with open(self.output_file) as output:
            saved = json.load(output)
        self.assertEqual(saved['stats']['applications'], 0)
        self.assertTrue(saved['applications'][0]['needsReview'])
        self.assertEqual(saved['applications'][0]['reviewReason'], 'no_application_found')
        self.assertNotIn("careers@", json.dumps(saved))
        self.assertTrue(saved['applications'][0]['emailUrl'].endswith('#all/cold'))

    def test_state_file_is_private_and_holds_no_email_text(self):
        self.run_tracker(['1'])
        self.assertEqual(os.stat(self.state_file).st_mode & 0o777, 0o600)
        with open(self.state_file) as state_file:
            self.assertNotIn("Thank", state_file.read())


def thread_message(message_id, sender, when, subject="Hello there", to="sam@acme.com", auto=None):
    headers = [{'name': 'From', 'value': sender}, {'name': 'To', 'value': to}, {'name': 'Subject', 'value': subject}]
    if auto:
        headers.append({'name': 'Auto-Submitted', 'value': auto})
    return {'id': message_id, 'internalDate': str(when), 'payload': {'headers': headers}}


class OutreachTests(unittest.TestCase):
    SUBJECTS = ["Hello there"]

    def summary(self, *messages):
        return recruiter_stats.summarize_thread({'id': 't1', 'messages': list(messages)}, ACCOUNT_A, self.SUBJECTS)

    def sent(self, **options):
        return thread_message('sent', f"Me <{ACCOUNT_A}>", 1760000000000, **options)

    def test_sent_with_no_reply(self):
        row = self.summary(self.sent())
        self.assertEqual((row['status'], row['replyDate'], row['company']), ('no reply', None, 'Acme'))

    def test_recruiter_reply_counts_and_link_opens_it(self):
        row = self.summary(self.sent(), thread_message('reply', "Sam <sam@acme.com>", 1760100000000, "Re: Hello there"))
        self.assertEqual(row['status'], 'replied')
        self.assertTrue(row['replyDate'])
        self.assertTrue(row['emailUrl'].endswith('#all/reply'))

    def test_out_of_office_is_not_a_reply(self):
        for reply in [thread_message('r', "Sam <sam@acme.com>", 1760100000000, "Automatic reply: Hello there"),
                      thread_message('r', "Sam <sam@acme.com>", 1760100000000, "Re: Hello there", auto="auto-replied"),
                      thread_message('r', "no-reply@acme.com", 1760100000000, "Re: Hello there")]:
            self.assertEqual(self.summary(self.sent(), reply)['status'], 'no reply')

    def test_bounce_is_separate_from_a_reply(self):
        row = self.summary(self.sent(), thread_message('b', "mailer-daemon@googlemail.com", 1760100000000, "Delivery Status Notification"))
        self.assertEqual(row['status'], 'bounced')

    def test_real_reply_beats_an_earlier_auto_reply(self):
        row = self.summary(self.sent(),
                           thread_message('a', "Sam <sam@acme.com>", 1760100000000, "Automatic reply: Hello there"),
                           thread_message('r', "Sam <sam@acme.com>", 1760200000000, "Re: Hello there"))
        self.assertEqual(row['status'], 'replied')

    def test_her_own_follow_up_is_not_a_reply(self):
        row = self.summary(self.sent(), thread_message('f', ACCOUNT_A, 1760100000000, "Re: Hello there"))
        self.assertEqual(row['status'], 'no reply')

    def test_other_subjects_are_ignored(self):
        self.assertIsNone(self.summary(thread_message('x', ACCOUNT_A, 1760000000000, "Something else")))

    def test_row_holds_no_recruiter_details(self):
        row = self.summary(self.sent(), thread_message('r', "Sam Lee <sam@acme.com>", 1760100000000, "Re: Hello there"))
        self.assertNotIn("Sam", json.dumps(row))
        self.assertNotIn("sam@", json.dumps(row))
        self.assertEqual(set(row), {'id', 'company', 'sentDate', 'status', 'replyDate', 'emailUrl'})

    def test_personal_recipient_has_no_company(self):
        self.assertIsNone(self.summary(self.sent(to="sam@gmail.com"))['company'])

    def test_stats(self):
        rows = [{'status': 'replied'}, {'status': 'no reply'}, {'status': 'no reply'}, {'status': 'bounced'}]
        self.assertEqual(recruiter_stats.build_stats(rows), {'sent': 4, 'replied': 1, 'bounced': 1, 'noReply': 2})

    def test_query_only_searches_sent_mail(self):
        self.assertEqual(recruiter_stats.build_query(["A", "B"]), 'in:sent (subject:"A" OR subject:"B")')

    def test_outreach_sync_keeps_to_its_own_fields(self):
        row = {'id': 'x', 'company': 'Acme', 'sentDate': '2026-01-01', 'status': 'replied', 'replyDate': None, 'emailUrl': 'u', 'extra': 'no'}
        fields = sync_prepare.TARGETS['outreach']['fields']
        self.assertNotIn('extra', sync_prepare.build_writes([row], {}, fields)[0]['data'])


class RecruiterMemoryTests(unittest.TestCase):
    TODAY = datetime.date(2026, 10, 30)

    def test_first_run_goes_back_thirty_days(self):
        state = {'scannedThrough': {}, 'contacted': []}
        self.assertEqual(recruiter_state.scan_start(state, ACCOUNT_A, self.TODAY), '2026/09/30')

    def test_later_run_continues_with_overlap(self):
        state = {'scannedThrough': {ACCOUNT_A: '2026-10-20'}, 'contacted': []}
        self.assertEqual(recruiter_state.scan_start(state, ACCOUNT_A, self.TODAY), '2026/10/18')

    def test_retry_uses_the_full_window(self):
        state = {'scannedThrough': {ACCOUNT_A: '2026-10-20'}, 'contacted': []}
        self.assertEqual(recruiter_state.scan_start(state, ACCOUNT_A, self.TODAY, retry=True), '2026/09/30')

    def test_days_option_searches_further_back(self):
        state = {'scannedThrough': {ACCOUNT_A: '2026-10-20'}, 'contacted': []}
        self.assertEqual(recruiter_state.scan_start(state, ACCOUNT_A, self.TODAY, days=90), '2026/08/01')

    def test_new_account_starts_with_the_window(self):
        state = {'scannedThrough': {ACCOUNT_A: '2026-10-20'}, 'contacted': []}
        self.assertEqual(recruiter_state.scan_start(state, ACCOUNT_B, self.TODAY), '2026/09/30')

    def test_hash_ignores_case_and_hides_the_address(self):
        self.assertEqual(recruiter_state.address_hash("Sam@Acme.com "), recruiter_state.address_hash("sam@acme.com"))
        self.assertNotIn("acme", recruiter_state.address_hash("sam@acme.com"))

    def test_state_file_is_private(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'state.json')
            recruiter_state.save_state({'scannedThrough': {}, 'contacted': ['x']}, path)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(recruiter_state.load_state(path)['contacted'], ['x'])

    def contacted(self, found, state=None, retry=False):
        state = state or {'contacted': []}
        with mock.patch.object(gmail_client, 'search_message_ids', lambda service, query: iter(found)):
            return sender.already_contacted(object(), "sam@acme.com", state, retry)

    def test_saved_recruiter_is_skipped_without_searching(self):
        state = {'contacted': [recruiter_state.address_hash("sam@acme.com")]}
        with mock.patch.object(gmail_client, 'search_message_ids', side_effect=AssertionError):
            self.assertTrue(sender.already_contacted(object(), "sam@acme.com", state))

    def test_gmail_history_is_skipped(self):
        self.assertTrue(self.contacted(['1']))

    def test_new_recruiter_is_kept(self):
        self.assertFalse(self.contacted([]))

    def test_retry_redoes_a_deleted_draft(self):
        state = {'contacted': [recruiter_state.address_hash("sam@acme.com")]}
        self.assertFalse(self.contacted([], state, retry=True))

    def test_retry_still_respects_sent_mail(self):
        state = {'contacted': [recruiter_state.address_hash("sam@acme.com")]}
        self.assertTrue(self.contacted(['1'], state, retry=True))

    def test_search_checks_sent_and_drafts(self):
        queries = []
        with mock.patch.object(gmail_client, 'search_message_ids', lambda s, q: queries.append(q) or iter([])):
            sender.already_contacted(object(), "sam@acme.com", {'contacted': []})
        self.assertEqual(queries, ['to:"sam@acme.com" (in:sent OR in:drafts)'])


class RecruiterFilterTests(unittest.TestCase):
    def reason(self, to_email="sam@acme.com", subject="Opportunity at Acme", bulk=False):
        return sender.filter_reason(to_email, subject, bulk)

    def test_person_reaching_out_passes(self):
        self.assertIsNone(self.reason())
        self.assertIsNone(self.reason(subject="Re: Opportunity with Acme"))

    def test_automated_addresses_are_skipped(self):
        for address in ["no-reply@acme.com", "noreply@acme.com", "do-not-reply@acme.com", "notifications@acme.com"]:
            self.assertEqual(self.reason(address), 'automated address', address)

    def test_hiring_systems_and_boards_are_skipped(self):
        for address in ["team@us.greenhouse-jobs.com", "sam@hire.lever.co", "sam@ashbyhq.com", "sam@ripplematch.com"]:
            self.assertEqual(self.reason(address), 'hiring system', address)

    def test_universities_are_skipped(self):
        self.assertEqual(self.reason("sam@alumni.example.edu"), 'university')

    def test_active_process_subjects_are_skipped(self):
        for subject in ["Acme Recruiter Screen Confirmation", "Your interview with Acme", "Assessment for your role"]:
            self.assertEqual(self.reason(subject=subject), 'active process', subject)

    def test_mass_emails_are_skipped(self):
        self.assertEqual(self.reason(bulk=True), 'mass email')

    def test_interview_blocks_the_whole_company(self):
        results = [{'sender': "Pat <pat@acme.com>", 'subject': "Schedule your interview with Acme"},
                   {'sender': "Sam <sam@ext.acme.com>", 'subject': "A note from your recruiting team"},
                   {'sender': "Kim <kim@globex.com>", 'subject': "Opportunity at Globex"}]
        companies = sender.active_process_companies(results)
        self.assertEqual(companies, {'acme'})
        self.assertIn(tracker.registered_label(sender.email_domain("sam@ext.acme.com")), companies)
        self.assertNotIn(tracker.registered_label(sender.email_domain("kim@globex.com")), companies)

    def test_personal_email_services_never_block_a_company(self):
        results = [{'sender': "Nick <nick@gmail.com>", 'subject': "Re: Acme Recruiter Screen Confirmation"},
                   {'sender': "Pat <pat@acme.com>", 'subject': "Your interview with Acme"}]
        self.assertEqual(sender.active_process_companies(results), {'acme'})

    def test_bulk_detection(self):
        self.assertTrue(fetcher.is_bulk_email([{'name': 'List-Unsubscribe', 'value': '<x>'}], "Hello"))
        self.assertTrue(fetcher.is_bulk_email([{'name': 'Precedence', 'value': 'bulk'}], "Hello"))
        self.assertTrue(fetcher.is_bulk_email([], "Click to unsubscribe."))
        self.assertFalse(fetcher.is_bulk_email([{'name': 'Subject', 'value': 'Hi'}], "Hello there"))


class RecruiterClassifierTests(unittest.TestCase):
    def answer(self, response):
        with mock.patch.object(recruiter_classifier, 'request_json', return_value={'response': response}):
            return recruiter_classifier.is_cold_outreach("sam@acme.com", "Hello", "Body")

    def test_clear_yes(self):
        self.assertTrue(self.answer('{"category": "recruiter_outreach"}'))

    def test_no_and_unclear_answers_never_draft(self):
        for response in ['{"category": "interview_process"}', '{"category": "other"}', '{"category": true}', 'not json', '', '[]']:
            self.assertFalse(self.answer(response), response)

    def test_only_a_local_address_is_used(self):
        self.assertTrue(recruiter_classifier.OLLAMA_URL.startswith('http://localhost:'))

    def test_long_email_is_cut_before_the_model(self):
        sent = {}
        def capture(path, payload):
            sent.update(payload)
            return {'response': '{"category": "other"}'}
        with mock.patch.object(recruiter_classifier, 'request_json', capture):
            recruiter_classifier.is_cold_outreach("s", "t", "x" * 50000)
        self.assertLess(len(sent['prompt']), 5000)
        self.assertEqual(sent['options']['temperature'], 0)

    def test_missing_model_stops_the_run(self):
        with mock.patch.object(recruiter_classifier, 'request_json', return_value={'models': []}):
            with self.assertRaises(recruiter_classifier.ModelUnavailable):
                recruiter_classifier.require_model()

    def test_unreachable_ollama_is_a_clear_error(self):
        with mock.patch('urllib.request.urlopen', side_effect=recruiter_classifier.urllib.error.URLError('down')):
            with self.assertRaises(recruiter_classifier.ModelUnavailable):
                recruiter_classifier.require_model()


class RecruiterFetchTests(unittest.TestCase):
    def fetched(self, ids, messages):
        with mock.patch.object(gmail_client, 'search_message_ids', lambda s, q: iter(ids)), \
                mock.patch.object(gmail_client, 'get_message', lambda s, i: messages[i]):
            return fetcher.fetch_account_emails(object(), '2026/09/30')

    def test_reads_every_result_past_the_old_limit(self):
        ids = [str(i) for i in range(60)]
        messages = {i: message("sam@acme.com", "Hiring", "A job for you.") for i in ids}
        self.assertEqual(len(self.fetched(ids, messages)), 60)

    def test_badly_encoded_email_does_not_crash(self):
        bad = message("sam@acme.com", "Hiring", "x")
        bad['payload']['body']['data'] = base64.urlsafe_b64encode(b"caf\xe9 job").decode()
        self.assertEqual(len(self.fetched(['1'], {'1': bad})), 1)

    def test_query_uses_the_given_start_date(self):
        queries = []
        with mock.patch.object(gmail_client, 'search_message_ids', lambda s, q: queries.append(q) or iter([])):
            fetcher.fetch_account_emails(object(), '2026/09/30')
        self.assertIn('after:2026/09/30', queries[0])

    def test_failed_account_is_not_marked_finished(self):
        def connect(account):
            if account == ACCOUNT_A:
                raise gmail_client.LoginNeeded("expired")
            return object()
        with mock.patch.object(gmail_client, 'connect', connect), \
                mock.patch.object(gmail_client, 'search_message_ids', lambda s, q: iter([])), \
                mock.patch.object(fetcher, 'load_settings', return_value={'recruiter_accounts': [ACCOUNT_A, ACCOUNT_B]}), \
                mock.patch.object(recruiter_state, 'load_state', return_value={'scannedThrough': {}, 'contacted': []}):
            _, finished = fetcher.fetch_emails()
        self.assertEqual(finished, [ACCOUNT_B])


class MergeTests(unittest.TestCase):
    TODAY = '2026-11-04'

    def email(self, **changes):
        row = {'id': 'app-1', 'company': 'Acme', 'role': 'Data Analyst', 'appliedDate': '2026-11-01', 'type': 'full-time',
               'status': 'applied', 'lastUpdate': '2026-11-01', 'sourceAccount': ACCOUNT_A, 'emailUrl': 'link-1',
               'needsReview': False, 'reviewReason': None}
        row.update(changes)
        return row

    def skill(self, doc_id='skill-r1101-01-acme', version=1, **changes):
        row = {'company': 'Acme (Tier 1)', 'role': 'Data Analyst, New Grad', 'appliedDate': '2026-11-01', 'type': 'full-time',
               'status': 'applied', 'lastUpdate': '2026-11-01', 'sourceAccount': None, 'emailUrl': None,
               'needsReview': False, 'reviewReason': None, 'origin': 'skill', 'jobsId': 'r1101-01-acme'}
        row.update(changes)
        return {doc_id: (row, version)}

    def merge(self, rows, existing):
        return sync_prepare.merge_writes(rows, existing, self.TODAY)

    def test_confirmation_fills_the_skill_row(self):
        writes = self.merge([self.email()], self.skill())
        self.assertEqual(len(writes), 1)
        data = writes[0]['data']
        self.assertEqual((writes[0]['doc_id'], writes[0]['if_version']), ('skill-r1101-01-acme', 1))
        self.assertEqual((data['emailUrl'], data['sourceAccount'], data['origin'], data['jobsId']), ('link-1', ACCOUNT_A, 'skill', 'r1101-01-acme'))
        self.assertEqual((data['role'], data['company']), ('Data Analyst, New Grad', 'Acme (Tier 1)'))

    def test_rejection_moves_the_skill_row_forward(self):
        writes = self.merge([self.email(status='rejected', lastUpdate='2026-11-03')], self.skill())
        self.assertEqual((writes[0]['data']['status'], writes[0]['data']['lastUpdate']), ('rejected', '2026-11-03'))

    def test_status_never_moves_back(self):
        writes = self.merge([self.email(status='applied')], self.skill(status='interview'))
        self.assertEqual(writes[0]['data']['status'], 'interview')
        email_on_page = {'app-1': (dict(sync_prepare.email_document(self.email()), status='rejected'), 2)}
        self.assertEqual(self.merge([self.email(status='applied')], email_on_page), [])

    def test_subsidiary_counts_as_the_same_company(self):
        skill = self.skill(company='Acme Cloud Services', role='Platform Engineer')
        writes = self.merge([self.email(company='Acme', role='Platform Engineer I')], skill)
        self.assertEqual(writes[0]['doc_id'], 'skill-r1101-01-acme')

    def test_email_with_no_role_matches_one_skill_row(self):
        writes = self.merge([self.email(role=None)], self.skill())
        self.assertEqual(writes[0]['doc_id'], 'skill-r1101-01-acme')

    def test_outside_the_date_window_is_a_new_application(self):
        writes = self.merge([self.email(appliedDate='2026-11-20')], self.skill())
        self.assertEqual([w['doc_id'] for w in writes], ['app-1'])
        self.assertEqual(writes[0]['data']['origin'], 'email')
        self.assertFalse(writes[0]['data']['needsReview'])

    def test_no_match_adds_an_email_row(self):
        writes = self.merge([self.email(company='Globex')], self.skill())
        self.assertEqual([w['doc_id'] for w in writes], ['app-1'])

    def test_different_role_same_company_goes_to_review(self):
        writes = self.merge([self.email(role='Sales Manager')], self.skill())
        self.assertEqual([w['doc_id'] for w in writes], ['app-1'])
        self.assertEqual((writes[0]['data']['needsReview'], writes[0]['data']['reviewReason']), (True, 'possible_duplicate'))

    def test_two_possible_skill_rows_go_to_review(self):
        existing = {**self.skill('skill-a'), **self.skill('skill-b', jobsId='r1101-02-acme')}
        writes = self.merge([self.email(role=None)], existing)
        self.assertEqual(writes[0]['data']['reviewReason'], 'possible_duplicate')

    def test_each_skill_row_takes_one_email(self):
        rows = [self.email(), self.email(id='app-2', emailUrl='link-2')]
        writes = self.merge(rows, self.skill())
        self.assertEqual(sorted(w['doc_id'] for w in writes), ['app-2', 'skill-r1101-01-acme'])

    def test_rejection_without_a_date_matches_an_earlier_skill_row(self):
        writes = self.merge([self.email(appliedDate=None, status='rejected', lastUpdate='2026-11-03')], self.skill())
        self.assertEqual(writes[0]['doc_id'], 'skill-r1101-01-acme')

    def test_nothing_is_ever_deleted(self):
        email_on_page = {'app-9': (sync_prepare.email_document(self.email(id='app-9')), 3)}
        writes = self.merge([], {**email_on_page, **self.skill()})
        self.assertNotIn('delete', [w['op'] for w in writes])

    def test_email_row_gone_from_results_is_flagged(self):
        email_on_page = {'app-9': (sync_prepare.email_document(self.email(id='app-9')), 3)}
        writes = self.merge([], email_on_page)
        self.assertEqual((writes[0]['data']['needsReview'], writes[0]['data']['reviewReason'], writes[0]['if_version']),
                         (True, 'not_in_latest_scan', 3))
        self.assertEqual(self.merge([], {'app-9': (writes[0]['data'], 4)}), [])

    def test_skill_row_without_email_becomes_no_response_after_30_days(self):
        writes = self.merge([], self.skill(appliedDate='2026-10-01', lastUpdate='2026-10-01'))
        self.assertEqual(writes[0]['data']['status'], 'no response')
        self.assertEqual(self.merge([], self.skill(appliedDate='2026-10-20')), [])

    def test_unchanged_matched_row_writes_nothing(self):
        first = self.merge([self.email()], self.skill())[0]
        self.assertEqual(self.merge([self.email()], {first['doc_id']: (first['data'], 2)}), [])

    def test_existing_email_row_without_origin_gets_it(self):
        legacy = {'app-1': (sync_prepare.page_document(self.email()), 2)}
        writes = self.merge([self.email()], legacy)
        self.assertEqual(writes[0]['data']['origin'], 'email')

    def test_review_rows_are_never_matched(self):
        writes = self.merge([self.email(needsReview=True, reviewReason='unclear_status')], self.skill())
        self.assertEqual([w['doc_id'] for w in writes], ['app-1'])


class SyncTests(unittest.TestCase):
    def row(self, **changes):
        row = {'id': 'app-1', 'company': 'Acme', 'role': None, 'appliedDate': '2026-02-01', 'type': 'unknown',
               'status': 'applied', 'lastUpdate': '2026-02-01', 'sourceAccount': ACCOUNT_A, 'needsReview': False}
        row.update(changes)
        return row

    def saved(self, row, version=3):
        return {row['id']: (sync_prepare.page_document(row), version)}

    def test_new_row_is_created_without_a_version(self):
        writes = sync_prepare.build_writes([self.row()], {})
        self.assertEqual(writes[0]['op'], 'set')
        self.assertNotIn('if_version', writes[0])

    def test_unchanged_row_writes_nothing(self):
        self.assertEqual(sync_prepare.build_writes([self.row()], self.saved(self.row())), [])

    def test_changed_row_updates_and_never_duplicates(self):
        writes = sync_prepare.build_writes([self.row(status='rejected')], self.saved(self.row()))
        self.assertEqual(len(writes), 1)
        self.assertEqual((writes[0]['doc_id'], writes[0]['if_version']), ('app-1', 3))

    def test_row_gone_from_results_is_deleted(self):
        writes = sync_prepare.build_writes([], self.saved(self.row()))
        self.assertEqual((writes[0]['op'], writes[0]['if_version']), ('delete', 3))

    def test_saved_rows_get_their_tracked_version(self):
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, 'app-1.json'), 'w') as document:
                json.dump(sync_prepare.page_document(self.row()), document)
            with open(os.path.join(folder, 'app-2.json'), 'w') as document:
                json.dump(sync_prepare.page_document(self.row()), document)
            existing = sync_prepare.load_existing(folder, {'app-1': 4})
        self.assertEqual((existing['app-1'][1], existing['app-2'][1]), (4, 1))

    def test_applied_plan_updates_the_local_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            mirror, doc_path, plan_path = os.path.join(folder, 'mirror'), os.path.join(folder, 'doc.json'), os.path.join(folder, 'plan.json')
            sync_prepare.save_mirror_row(mirror, 'gone', {'company': 'Old'})
            with open(doc_path, 'w') as document:
                json.dump({'company': 'Acme'}, document)
            with open(plan_path, 'w') as plan_file:
                json.dump([[{'op': 'set', 'doc_id': 'new', 'file_path': doc_path},
                            {'op': 'delete', 'doc_id': 'gone', 'if_version': 1}]], plan_file)
            sync_prepare.mark_applied(plan_path, os.path.join(folder, 'versions.json'), mirror)
            self.assertEqual(sorted(os.listdir(mirror)), ['new.json'])
            self.assertEqual(sync_prepare.load_existing(mirror)['new'][0], {'company': 'Acme'})
            self.assertEqual(os.stat(os.path.join(mirror, 'new.json')).st_mode & 0o777, 0o600)

    def test_seed_refuses_to_overwrite_a_local_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            target = {'folder': folder, 'collection': 'c', 'source': os.path.join(folder, 'results.json'), 'rows': 'rows',
                      'fields': sync_prepare.PAGE_FIELDS}
            with open(target['source'], 'w') as results:
                json.dump({'rows': [self.row()]}, results)
            sync_prepare.seed_mirror(target)
            self.assertEqual(list(sync_prepare.load_existing(sync_prepare.mirror_folder(target))), ['app-1'])
            with self.assertRaises(SystemExit):
                sync_prepare.seed_mirror(target)

    def test_versions_follow_an_applied_plan(self):
        plan = [[{'op': 'set', 'doc_id': 'new'}, {'op': 'set', 'doc_id': 'kept', 'if_version': 2},
                 {'op': 'delete', 'doc_id': 'gone', 'if_version': 1}]]
        self.assertEqual(sync_prepare.versions_after(plan, {'gone': 1, 'kept': 2}), {'new': 1, 'kept': 3})

    def test_marking_applied_twice_builds_on_saved_versions(self):
        with tempfile.TemporaryDirectory() as folder:
            plan_path, versions_path = os.path.join(folder, 'plan.json'), os.path.join(folder, 'versions.json')
            with open(plan_path, 'w') as plan_file:
                json.dump([[{'op': 'set', 'doc_id': 'kept', 'if_version': 1}]], plan_file)
            sync_prepare.mark_applied(plan_path, versions_path)
            with open(plan_path, 'w') as plan_file:
                json.dump([[{'op': 'set', 'doc_id': 'kept', 'if_version': 2}]], plan_file)
            sync_prepare.mark_applied(plan_path, versions_path)
            self.assertEqual(sync_prepare.load_versions(versions_path), {'kept': 3})

    def test_only_agreed_fields_reach_the_page(self):
        document = sync_prepare.page_document(self.row(messageId='secret'))
        self.assertNotIn('messageId', document)
        self.assertNotIn('id', document)

    def test_batches_respect_the_limit(self):
        self.assertEqual([len(b) for b in sync_prepare.batches(list(range(120)))], [50, 50, 20])


class FakeList:
    def __init__(self, pages, calls):
        self.pages, self.calls = pages, calls

    def execute(self, num_retries=0):
        self.calls.append(1)
        return self.pages[len(self.calls) - 1]


class FakeMessages:
    def __init__(self, pages):
        self.pages, self.calls, self.tokens = pages, [], []

    def list(self, userId, q, maxResults, pageToken):
        self.tokens.append(pageToken)
        return FakeList(self.pages, self.calls)


class FakeService:
    def __init__(self, pages):
        self.fake_messages = FakeMessages(pages)

    def users(self):
        return self

    def messages(self):
        return self.fake_messages


class GmailClientTests(unittest.TestCase):
    def test_paging_reaches_the_last_page(self):
        service = FakeService([
            {'messages': [{'id': '1'}], 'nextPageToken': 'p2'},
            {'messages': [{'id': '2'}]},
        ])
        self.assertEqual(list(gmail_client.search_message_ids(service, 'q')), ['1', '2'])
        self.assertEqual(service.fake_messages.tokens, [None, 'p2'])

    def test_empty_search(self):
        self.assertEqual(list(gmail_client.search_message_ids(FakeService([{}]), 'q')), [])

    def test_body_in_deeply_nested_part(self):
        text = "x" * 300
        payload = {'mimeType': 'multipart/mixed', 'body': {}, 'parts': [
            {'mimeType': 'multipart/related', 'body': {}, 'parts': [
                {'mimeType': 'multipart/alternative', 'body': {}, 'parts': [
                    {'mimeType': 'text/plain', 'body': {'data': encode(text)}},
                ]},
            ]},
        ]}
        self.assertEqual(gmail_client.get_email_body(payload), text)

    def test_invalid_utf8_does_not_crash(self):
        data = base64.urlsafe_b64encode(b"caf\xe9").decode()
        self.assertIn("caf", gmail_client.get_email_body({'mimeType': 'text/plain', 'body': {'data': data}}))

    def test_short_plain_stub_prefers_html(self):
        payload = {'mimeType': 'multipart/alternative', 'body': {}, 'parts': [
            {'mimeType': 'text/plain', 'body': {'data': encode("View in browser")}},
            {'mimeType': 'text/html', 'body': {'data': encode("<p>The real email</p>")}},
        ]}
        self.assertIn("real email", gmail_client.get_email_body(payload))

    def test_token_path_is_per_account(self):
        self.assertNotEqual(gmail_client.token_path(ACCOUNT_A), gmail_client.token_path(ACCOUNT_B))
        self.assertTrue(gmail_client.token_path(ACCOUNT_A).startswith("tokens/token_gmail_"))


class SafetyTests(unittest.TestCase):
    def test_tracker_is_read_only(self):
        self.assertEqual(GMAIL_READONLY_SCOPES, ["https://www.googleapis.com/auth/gmail.readonly"])
        with open(gmail_client.__file__) as source:
            self.assertNotIn("COMPOSE", source.read())

    def test_broader_token_is_not_read_only(self):
        self.assertTrue(gmail_client.is_read_only(SimpleNamespace(scopes=GMAIL_READONLY_SCOPES)))
        self.assertFalse(gmail_client.is_read_only(SimpleNamespace(scopes=GMAIL_COMPOSE_SCOPES)))
        self.assertFalse(gmail_client.is_read_only(SimpleNamespace(scopes=GMAIL_READONLY_SCOPES + GMAIL_COMPOSE_SCOPES)))
        self.assertFalse(gmail_client.is_read_only(SimpleNamespace(scopes=None)))

    def test_no_code_can_send_or_delete(self):
        forbidden = re.compile(r"\.(send|delete|trash|untrash|modify|batchModify|batchDelete|import_|insert)\(")
        for name in ['gmail_client.py', 'track_applications.py', 'recruiter_stats.py', 'recruiter_state.py', 'recruiter_classifier.py', 'fetcher.py', 'sender.py', 'run.py', 'settings.py']:
            with open(os.path.join(ROOT, name)) as source:
                self.assertIsNone(forbidden.search(source.read()), name)

    def test_only_sender_touches_drafts_or_compose(self):
        for name in ['gmail_client.py', 'track_applications.py', 'recruiter_stats.py', 'recruiter_state.py', 'recruiter_classifier.py', 'fetcher.py', 'run.py', 'settings.py']:
            with open(os.path.join(ROOT, name)) as source:
                self.assertNotRegex(source.read(), r"drafts\(\)|GMAIL_COMPOSE_SCOPES", name)

    def test_tracker_only_run_never_loads_sender(self):
        code = "import sys, run; assert 'sender' not in sys.modules and 'fetcher' not in sys.modules"
        subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True)

    def test_private_files_stay_out_of_git(self):
        private = ['settings.json', 'draft_body.html', 'credentials.json', 'tokens/token_gmail_x.json',
                   'data/tracker_state.json', 'data/applications.json']
        for path in private:
            result = subprocess.run(['git', 'check-ignore', path], cwd=ROOT, capture_output=True)
            self.assertEqual(result.returncode, 0, path)

    def test_public_files_hold_no_personal_addresses(self):
        tracked = subprocess.run(['git', 'ls-files'], cwd=ROOT, capture_output=True, text=True).stdout.split()
        names = ['config.py', 'README.md', 'settings.example.json', 'fetcher.py', 'sender.py']
        for name in set(tracked) | set(names):
            if os.path.basename(name) == os.path.basename(__file__):
                continue
            with open(os.path.join(ROOT, name)) as source:
                self.assertNotRegex(source.read(), r"[\w.]+@(?:gmail|njit)\.", name)

    def test_query_reaches_start_date_and_keeps_all_categories(self):
        query = tracker.build_query()
        self.assertIn("after:2025/09/01", query)
        self.assertNotIn("category:", query)


if __name__ == "__main__":
    unittest.main()

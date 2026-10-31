import argparse
import sys

import gmail_client
from settings import load_settings

ORDER = ['tracker', 'recruiter']


# A command wins. With no command, settings decide, which is what a schedule uses.
def choose_features(command, features):
    if command == 'all':
        return list(ORDER)
    if command:
        return [command]
    enabled = {'tracker': features['application_tracker'], 'recruiter': features['recruiter']}
    return [name for name in ORDER if enabled[name]]


def main():
    parser = argparse.ArgumentParser(description="Run the application tracker and/or the recruiter finder.")
    parser.add_argument('command', nargs='?', choices=['tracker', 'recruiter', 'all'],
                        help="Leave out to run whatever is switched on in settings.json")
    parser.add_argument('--full', action='store_true', help="Tracker: rescan from the start date")
    parser.add_argument('--limit', type=int, help="Tracker: read at most this many emails per account, saving no progress")
    args = parser.parse_args()

    settings = load_settings()
    chosen = choose_features(args.command, settings['features'])
    if not chosen:
        print("Nothing is switched on in settings.json.")
        return

    # Imported only when used, so a tracker run never loads the draft code
    failed = []
    try:
        if 'tracker' in chosen:
            import track_applications
            failed = track_applications.run(settings, full=args.full, limit=args.limit)
        if 'recruiter' in chosen:
            import sender
            sender.main()
    except gmail_client.LoginNeeded as error:
        print(error)
        sys.exit(2)

    # The working accounts are saved, but a schedule should still hear about this
    if failed:
        print(f"Skipped accounts: {', '.join(failed)}")
        sys.exit(2)


if __name__ == "__main__":
    main()

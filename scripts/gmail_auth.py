"""One-time Gmail sign-in: opens a browser, saves a refresh token to secrets/gmail_token.json.

Does not send anything. Run from the repo root:
    .venv/Scripts/python scripts/gmail_auth.py
"""

import json
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

CLIENT_FILE = Path("secrets/gmail_client.json")
TOKEN_FILE = Path("secrets/gmail_token.json")
SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]
# Must match an "Authorized redirect URI" on the OAuth client exactly.
PORT = 8765


def main() -> None:
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_FILE), SCOPES)
    # prompt=consent forces Google to return a refresh token even on a repeat sign-in.
    creds = flow.run_local_server(port=PORT, access_type="offline", prompt="consent")

    if not creds.refresh_token:
        raise SystemExit("Google returned no refresh token. Remove the app's access at "
                         "https://myaccount.google.com/permissions and run this again.")

    TOKEN_FILE.write_text(creds.to_json())

    profile = build("gmail", "v1", credentials=creds).users().getProfile(userId="me").execute()
    print(f"Signed in as: {profile['emailAddress']}")
    print(f"Scopes granted: {', '.join(json.loads(creds.to_json())['scopes'])}")
    print(f"Refresh token saved to {TOKEN_FILE}")


if __name__ == "__main__":
    main()

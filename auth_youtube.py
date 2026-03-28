"""
YouTube OAuth2 Authentication Helper.

Run this locally ONCE to generate token.json,
then store it as YOUTUBE_TOKEN_JSON secret in GitHub.

Usage:
    python auth_youtube.py

Prerequisites:
    1. Go to Google Cloud Console → APIs & Services → Credentials
    2. Create an OAuth 2.0 Client ID (Desktop application)
    3. Download the JSON and save as client_secret.json
    4. Enable YouTube Data API v3
"""

import json
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def authenticate():
    """Run OAuth2 flow and save token.json."""
    client_secret = Path("client_secret.json")

    if not client_secret.exists():
        print("❌ client_secret.json not found!")
        print("   Download it from Google Cloud Console → Credentials")
        print("   Save it in this directory as 'client_secret.json'")
        return

    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_secret), SCOPES
    )
    credentials = flow.run_local_server(port=0)

    token_data = {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": credentials.scopes,
    }

    token_file = Path("token.json")
    with open(token_file, "w", encoding="utf-8") as f:
        json.dump(token_data, f, indent=2)

    print(f"✅ Token saved to {token_file}")
    print()
    print("📋 To use in GitHub Actions, copy this as YOUTUBE_TOKEN_JSON secret:")
    print(json.dumps(token_data))
    print()
    print("⚠️ DO NOT commit token.json or client_secret.json to git!")


if __name__ == "__main__":
    authenticate()

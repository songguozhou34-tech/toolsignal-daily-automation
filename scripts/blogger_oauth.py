from __future__ import annotations

import argparse
import http.server
import json
import secrets
import threading
import urllib.parse
import urllib.request
import webbrowser


REDIRECT_URI = "http://127.0.0.1:8765/callback"
SCOPE = "https://www.googleapis.com/auth/blogger"


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    code = ""
    state = ""
    event = threading.Event()

    def do_GET(self) -> None:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if query.get("state", [""])[0] != self.state:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"State mismatch")
            return
        self.code = query.get("code", [""])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"<h1>Authorization received.</h1><p>You can close this tab.</p>")
        self.event.set()

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--client-secret", required=True)
    args = parser.parse_args()
    state = secrets.token_urlsafe(24)
    CallbackHandler.state = state
    params = {
        "client_id": args.client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
    server = http.server.HTTPServer(("127.0.0.1", 8765), CallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print("Opening Google authorization page...")
    webbrowser.open(url)
    CallbackHandler.event.wait(timeout=300)
    server.shutdown()
    if not CallbackHandler.code:
        raise SystemExit("Authorization timed out or failed.")
    request = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=urllib.parse.urlencode(
            {
                "client_id": args.client_id,
                "client_secret": args.client_secret,
                "code": CallbackHandler.code,
                "grant_type": "authorization_code",
                "redirect_uri": REDIRECT_URI,
            }
        ).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        token = json.loads(response.read().decode("utf-8"))
    print("GOOGLE_REFRESH_TOKEN=" + token.get("refresh_token", ""))


if __name__ == "__main__":
    main()


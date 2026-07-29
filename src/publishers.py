from __future__ import annotations

import datetime as dt
import json
import os
import smtplib
import ssl
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from typing import Any


USER_AGENT = "ToolSignalDaily/1.0"


def _json_request(
    url: str,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    method: str = "POST",
    timeout: int = 45,
) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json", **(headers or {})},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
        return json.loads(body) if body else {}


def _form_request(url: str, payload: dict[str, str], timeout: int = 45) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(payload).encode("utf-8"),
        method="POST",
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def publish_blogger(article: dict[str, str], draft: bool) -> dict[str, Any]:
    required = {
        "blog_id": os.getenv("BLOGGER_BLOG_ID", ""),
        "client_id": os.getenv("GOOGLE_CLIENT_ID", ""),
        "client_secret": os.getenv("GOOGLE_CLIENT_SECRET", ""),
        "refresh_token": os.getenv("GOOGLE_REFRESH_TOKEN", ""),
    }
    missing = [key for key, value in required.items() if not value.strip()]
    if missing:
        return {"status": "skipped", "reason": f"missing:{','.join(missing)}"}
    token = _form_request(
        "https://oauth2.googleapis.com/token",
        {
            "client_id": required["client_id"],
            "client_secret": required["client_secret"],
            "refresh_token": required["refresh_token"],
            "grant_type": "refresh_token",
        },
    )["access_token"]
    url = (
        f"https://www.googleapis.com/blogger/v3/blogs/{urllib.parse.quote(required['blog_id'])}/posts/"
        f"?isDraft={'true' if draft else 'false'}"
    )
    result = _json_request(
        url,
        {"kind": "blogger#post", "title": article["title"], "content": article["html"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    return {
        "status": "drafted" if draft else "published",
        "id": result.get("id"),
        "url": result.get("url"),
    }


def _bluesky_facets(text: str) -> list[dict[str, Any]]:
    facets: list[dict[str, Any]] = []
    for token in text.split():
        if not token.startswith(("https://", "http://")):
            continue
        clean = token.rstrip(".,)")
        start = text.encode("utf-8").find(clean.encode("utf-8"))
        if start < 0:
            continue
        facets.append(
            {
                "index": {"byteStart": start, "byteEnd": start + len(clean.encode("utf-8"))},
                "features": [{"$type": "app.bsky.richtext.facet#link", "uri": clean}],
            }
        )
    return facets


def publish_bluesky(text: str) -> dict[str, Any]:
    handle = os.getenv("BLUESKY_HANDLE", "").strip()
    password = os.getenv("BLUESKY_APP_PASSWORD", "").strip()
    if not handle or not password:
        return {"status": "skipped", "reason": "missing_credentials"}
    session = _json_request(
        "https://bsky.social/xrpc/com.atproto.server.createSession",
        {"identifier": handle, "password": password},
    )
    record = {
        "$type": "app.bsky.feed.post",
        "text": text,
        "createdAt": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "facets": _bluesky_facets(text),
    }
    result = _json_request(
        "https://bsky.social/xrpc/com.atproto.repo.createRecord",
        {"repo": session["did"], "collection": "app.bsky.feed.post", "record": record},
        headers={"Authorization": f"Bearer {session['accessJwt']}"},
    )
    return {"status": "published", "uri": result.get("uri"), "cid": result.get("cid")}


def publish_threads(text: str) -> dict[str, Any]:
    user_id = os.getenv("THREADS_USER_ID", "").strip()
    token = os.getenv("THREADS_ACCESS_TOKEN", "").strip()
    if not user_id or not token:
        return {"status": "skipped", "reason": "missing_credentials"}
    create = _form_request(
        f"https://graph.threads.net/v1.0/{urllib.parse.quote(user_id)}/threads",
        {"media_type": "TEXT", "text": text, "access_token": token},
    )
    result = _form_request(
        f"https://graph.threads.net/v1.0/{urllib.parse.quote(user_id)}/threads_publish",
        {"creation_id": str(create["id"]), "access_token": token},
    )
    return {"status": "published", "id": result.get("id")}


def publish_pinterest(title: str, description: str, article_url: str, image_url: str) -> dict[str, Any]:
    token = os.getenv("PINTEREST_ACCESS_TOKEN", "").strip()
    board_id = os.getenv("PINTEREST_BOARD_ID", "").strip()
    if not token or not board_id or not image_url:
        return {"status": "skipped", "reason": "missing_credentials_or_public_image"}
    result = _json_request(
        "https://api.pinterest.com/v5/pins",
        {
            "board_id": board_id,
            "title": title[:100],
            "description": description[:500],
            "link": article_url,
            "media_source": {"source_type": "image_url", "url": image_url},
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    return {"status": "published", "id": result.get("id"), "link": result.get("link")}


def send_report_email(subject: str, body: str) -> dict[str, Any]:
    recipient = os.getenv("REPORT_EMAIL_TO", "").strip()
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_APP_PASSWORD", "").strip()
    if not recipient or not username or not password:
        return {"status": "skipped", "reason": "missing_smtp_credentials"}
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = username
    message["To"] = recipient
    message.set_content(body)
    context = ssl.create_default_context()
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
        server.starttls(context=context)
        server.login(username, password)
        server.send_message(message)
    return {"status": "sent", "to": recipient}


def guarded(callable_, *args: Any, **kwargs: Any) -> dict[str, Any]:
    try:
        return callable_(*args, **kwargs)
    except (OSError, KeyError, ValueError, urllib.error.URLError, smtplib.SMTPException) as exc:
        return {"status": "error", "error": str(exc)}


from __future__ import annotations

import datetime as dt
import email.utils
import hashlib
import html
import json
import os
import re
import textwrap
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


USER_AGENT = "ToolSignalDaily/1.0 (+source-linked editorial digest)"


@dataclass
class FeedItem:
    source: str
    source_weight: float
    title: str
    url: str
    summary: str
    published_at: str
    score: float = 0.0

    @property
    def domain(self) -> str:
        return urllib.parse.urlparse(self.url).netloc.lower().removeprefix("www.")

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.url.encode("utf-8")).hexdigest()[:20]


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def request_bytes(url: str, timeout: int = 25) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def strip_html(value: str) -> str:
    value = re.sub(r"<script\b[^>]*>.*?</script>", " ", value or "", flags=re.I | re.S)
    value = re.sub(r"<style\b[^>]*>.*?</style>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def clean_summary(value: str) -> str:
    """Remove feed boilerplate that is not useful as an editorial fact."""
    cleaned = strip_html(value)
    cleaned = re.sub(r"^\s*(image|photo|video)\s*:\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\b(read more|continue reading)\b.*$", "", cleaned, flags=re.I)
    if re.match(r"^(illustration|image|photo|graphic|screenshot)\b", cleaned, flags=re.I):
        return ""
    if len(cleaned.split()) < 8:
        return ""
    return cleaned[:1200]


def parse_date(value: str) -> dt.datetime:
    if not value:
        return utc_now()
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        if parsed:
            return parsed.astimezone(dt.timezone.utc)
    except (TypeError, ValueError, OverflowError):
        pass
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except ValueError:
        return utc_now()


def _first_text(element: ET.Element, names: tuple[str, ...]) -> str:
    for child in element.iter():
        tag = child.tag.split("}")[-1].lower()
        if tag in names and child.text:
            return child.text.strip()
    return ""


def parse_feed(payload: bytes, source: dict[str, Any]) -> list[FeedItem]:
    root = ET.fromstring(payload)
    items: list[FeedItem] = []
    entries = [
        element
        for element in root.iter()
        if element.tag.split("}")[-1].lower() in {"item", "entry"}
    ]
    for entry in entries:
        title = _first_text(entry, ("title",))
        summary = _first_text(entry, ("description", "summary", "content"))
        published = _first_text(entry, ("pubdate", "published", "updated", "date"))
        link = ""
        for child in entry:
            if child.tag.split("}")[-1].lower() != "link":
                continue
            candidate = child.attrib.get("href") or (child.text or "")
            rel = child.attrib.get("rel", "alternate")
            if candidate and rel in {"alternate", ""}:
                link = candidate.strip()
                break
        if not link:
            link = _first_text(entry, ("guid", "id"))
        if not title or not link.startswith(("http://", "https://")):
            continue
        items.append(
            FeedItem(
                source=str(source["name"]),
                source_weight=float(source.get("weight", 0.5)),
                title=strip_html(title),
                url=link,
                summary=clean_summary(summary),
                published_at=parse_date(published).isoformat(),
            )
        )
    return items


def fetch_all_sources(sources: list[dict[str, Any]]) -> tuple[list[FeedItem], list[dict[str, str]]]:
    items: list[FeedItem] = []
    errors: list[dict[str, str]] = []
    for source in sources:
        try:
            items.extend(parse_feed(request_bytes(str(source["url"])), source))
        except (OSError, ET.ParseError, urllib.error.URLError) as exc:
            errors.append({"source": str(source.get("name", "unknown")), "error": str(exc)})
    return items, errors


def score_items(items: list[FeedItem], settings: dict[str, Any]) -> list[FeedItem]:
    now = utc_now()
    keywords = [str(value).lower() for value in settings.get("keywords", [])]
    excluded = [str(value).lower() for value in settings.get("excluded_topics", [])]
    business_signals = {
        "api",
        "automation",
        "business",
        "developer",
        "enterprise",
        "security",
        "team",
        "workflow",
        "work",
    }
    consumer_signals = {
        "enjoy",
        "games",
        "holiday",
        "photos",
        "shopping",
        "travel",
        "vacation",
    }
    lookback = float(settings.get("lookback_hours", 72))
    accepted: list[FeedItem] = []
    seen_urls: set[str] = set()
    for item in items:
        if item.url in seen_urls:
            continue
        seen_urls.add(item.url)
        haystack = f"{item.title} {item.summary}".lower()
        if any(term in haystack for term in excluded):
            continue
        if any(term in haystack for term in consumer_signals) and not any(
            term in haystack for term in business_signals
        ):
            continue
        age_hours = max(0.0, (now - parse_date(item.published_at)).total_seconds() / 3600)
        if age_hours > lookback:
            continue
        keyword_hits = sum(1 for word in keywords if word in haystack)
        recency = max(0.0, 1.0 - age_hours / lookback)
        item.score = round(item.source_weight * 3 + keyword_hits * 0.8 + recency * 2, 3)
        accepted.append(item)
    return sorted(accepted, key=lambda item: (item.score, item.published_at), reverse=True)


def select_balanced(items: list[FeedItem], maximum: int) -> list[FeedItem]:
    selected: list[FeedItem] = []
    source_counts: dict[str, int] = {}
    for item in items:
        if source_counts.get(item.source, 0) >= 2:
            continue
        selected.append(item)
        source_counts[item.source] = source_counts.get(item.source, 0) + 1
        if len(selected) >= maximum:
            break
    return selected


def practical_angle(item: FeedItem) -> str:
    haystack = f"{item.title} {item.summary}".lower()
    if "security" in haystack or "privacy" in haystack:
        return "Small teams should review access controls, data handling, and rollout risk before enabling this change."
    if "api" in haystack or "developer" in haystack:
        return "This may reduce integration work, but teams should check limits, compatibility, and migration notes before adopting it."
    if "agent" in haystack or "automation" in haystack:
        return "The useful test is whether it removes a repeatable task without creating a new review or reliability burden."
    if "price" in haystack or "plan" in haystack:
        return "Compare the full workflow cost rather than the headline price, including seats, usage limits, and switching effort."
    return "The practical value depends on whether the update saves measurable time or improves a workflow your team already runs."


def deterministic_article(items: list[FeedItem], settings: dict[str, Any], date_label: str) -> dict[str, str]:
    title = f"AI Tool Pulse — {date_label}: {items[0].title[:72]}"
    sections: list[str] = [
        "<p><strong>ToolSignal Daily</strong> reviews official product and engineering updates, then translates them into practical actions for small teams. This briefing uses source-linked announcements rather than copied news text.</p>",
        "<p>Each item below is treated as a decision input, not a recommendation. The goal is to help a small team decide what deserves a controlled test, what should wait, and what can be ignored.</p>",
        "<h2>Today’s signal</h2>",
        "<p>The common theme across today’s updates is operational leverage: useful AI adoption is less about adding another tool and more about reducing a specific recurring task while keeping costs, permissions, and reliability visible.</p>",
    ]
    for index, item in enumerate(items, 1):
        summary = item.summary or "The official source published a new product or engineering update."
        sections.extend(
            [
                f"<h2>{index}. {html.escape(item.title)}</h2>",
                f"<p>{html.escape(summary[:700])}</p>",
                f"<p><strong>Why it matters:</strong> {html.escape(practical_angle(item))}</p>",
                "<p><strong>Try this:</strong> Define one task, its current time cost, the expected improvement, and a rollback point. Test with non-sensitive data before expanding access.</p>",
                "<p><strong>Decision check:</strong> Ask who owns the workflow, what data the tool can access, how a result will be reviewed, and what happens if the feature changes or disappears. A useful pilot should create evidence within one week without locking the team into a new process.</p>",
                f'<p><a href="{html.escape(item.url, quote=True)}" rel="nofollow noopener">Read the official source: {html.escape(item.source)}</a></p>',
            ]
        )
    sections.extend(
        [
            "<h2>A 20-minute action plan</h2>",
            "<ol><li>Choose one update that maps to a real weekly task.</li><li>Record the current time, cost, and error rate.</li><li>Run a small test with a clear success condition.</li><li>Keep the change only if the result is measurable.</li></ol>",
            "<h2>What not to do</h2>",
            "<p>Do not connect sensitive customer data, replace a dependable process, or buy a larger plan before the pilot has a measurable result. New capabilities often look impressive in a demonstration while adding hidden review work in daily use.</p>",
            f"<p>{html.escape(str(settings.get('call_to_action', '')))}</p>",
            "<p><em>Editorial note: This is an independent practical digest. Product capabilities and prices can change; verify details on the linked official pages.</em></p>",
        ]
    )
    body = "\n".join(sections)
    return {
        "title": title,
        "html": body,
        "excerpt": "A source-linked digest of practical AI and automation updates for small teams.",
        "provider": "rules",
    }


def _gemini_prompt(items: list[FeedItem], settings: dict[str, Any], date_label: str) -> str:
    facts = [
        {
            "source": item.source,
            "title": item.title,
            "summary": item.summary,
            "url": item.url,
            "published_at": item.published_at,
        }
        for item in items
    ]
    return textwrap.dedent(
        f"""
        Write an original English article for {settings['brand']} dated {date_label}.
        Audience: owners and operators of small businesses and small digital teams.
        Use only the supplied source facts. Do not invent features, prices, quotes, metrics, customers, or outcomes.
        Do not copy source wording beyond unavoidable product names.
        Provide 750-1100 words of useful analysis with:
        - a concise headline
        - a two-paragraph introduction
        - one section per important update
        - a practical "why it matters" and low-risk test for each
        - a short action checklist
        - a Sources section with every supplied URL
        - a disclosure that details can change and readers should verify official pages
        Avoid politics, medical advice, investment advice, hype, income promises, and generic filler.
        Return strict JSON with keys: title, html, excerpt. The html must use only p, h2, h3, ul, ol, li, strong, em, and a tags.

        SOURCE FACTS:
        {json.dumps(facts, ensure_ascii=False)}
        """
    ).strip()


def generate_with_gemini(items: list[FeedItem], settings: dict[str, Any], date_label: str) -> dict[str, str] | None:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        return None
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
    endpoint = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(model)}:"
        f"generateContent?key={urllib.parse.quote(api_key)}"
    )
    payload = {
        "contents": [{"parts": [{"text": _gemini_prompt(items, settings, date_label)}]}],
        "generationConfig": {
            "temperature": 0.35,
            "responseMimeType": "application/json",
        },
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
        raw = result["candidates"][0]["content"]["parts"][0]["text"]
        article = json.loads(raw)
        return {
            "title": strip_html(str(article["title"]))[:160],
            "html": str(article["html"]),
            "excerpt": strip_html(str(article["excerpt"]))[:320],
            "provider": f"gemini:{model}",
        }
    except (KeyError, ValueError, OSError, urllib.error.URLError, json.JSONDecodeError):
        return None


def article_word_count(article_html: str) -> int:
    return len(re.findall(r"\b[\w’'-]+\b", strip_html(article_html)))


def quality_check(article: dict[str, str], items: list[FeedItem], settings: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    words = article_word_count(article["html"])
    unique_domains = len({item.domain for item in items})
    source_links = sum(1 for item in items if item.url in article["html"])
    if len(items) < int(settings.get("minimum_sources", 3)):
        reasons.append("insufficient_sources")
    if unique_domains < int(settings.get("minimum_unique_domains", 2)):
        reasons.append("insufficient_source_diversity")
    if source_links < len(items):
        reasons.append("missing_source_links")
    if words < int(settings.get("minimum_words", 650)):
        reasons.append("article_too_short")
    lower = strip_html(article["html"]).lower()
    if any(term.lower() in lower for term in settings.get("excluded_topics", [])):
        reasons.append("excluded_topic_detected")
    if article.get("provider") == "rules":
        reasons.append("rules_mode_requires_editorial_review")
    return {
        "pass": not reasons,
        "reasons": reasons,
        "word_count": words,
        "source_count": len(items),
        "unique_domains": unique_domains,
        "source_links": source_links,
    }


def safe_slug(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return value[:72] or "daily-brief"


def render_card_svg(article: dict[str, str], items: list[FeedItem], date_label: str) -> str:
    headline = html.escape(article["title"][:88])
    bullets = [html.escape(item.title[:78]) for item in items[:3]]
    bullet_nodes = "\n".join(
        f'<text x="92" y="{480 + index * 150}" class="bullet">• {bullet}</text>'
        for index, bullet in enumerate(bullets)
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="1500" viewBox="0 0 1000 1500">
<defs>
  <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#101828"/><stop offset="1" stop-color="#1d2939"/></linearGradient>
</defs>
<rect width="1000" height="1500" rx="48" fill="url(#bg)"/>
<circle cx="860" cy="120" r="220" fill="#7f56d9" opacity=".28"/>
<text x="80" y="110" class="brand">TOOLSIGNAL DAILY</text>
<text x="80" y="180" class="date">{html.escape(date_label)}</text>
<foreignObject x="78" y="245" width="840" height="230">
  <div xmlns="http://www.w3.org/1999/xhtml" style="font:700 54px Arial;color:white;line-height:1.12">{headline}</div>
</foreignObject>
{bullet_nodes}
<rect x="78" y="1110" width="844" height="2" fill="#475467"/>
<text x="80" y="1190" class="small">Official sources • Practical analysis • No hype</text>
<text x="80" y="1290" class="cta">Save 20 minutes. Test one useful update.</text>
<style>
.brand{{font:700 30px Arial;letter-spacing:5px;fill:#b692f6}}
.date{{font:400 26px Arial;fill:#d0d5dd}}
.bullet{{font:600 31px Arial;fill:#f2f4f7}}
.small{{font:400 28px Arial;fill:#98a2b3}}
.cta{{font:700 36px Arial;fill:#e9d7fe}}
</style>
</svg>"""


def render_card_png(
    article: dict[str, str],
    items: list[FeedItem],
    date_label: str,
    output_path: Path,
) -> None:
    from PIL import Image, ImageDraw, ImageFont

    width, height = 1000, 1500
    image = Image.new("RGB", (width, height), "#101828")
    draw = ImageDraw.Draw(image)
    draw.ellipse((650, -160, 1130, 320), fill="#352f69")

    regular_candidates = (
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    )
    bold_candidates = (
        Path("C:/Windows/Fonts/arialbd.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    )
    regular_path = next((path for path in regular_candidates if path.exists()), None)
    bold_path = next((path for path in bold_candidates if path.exists()), regular_path)
    if not regular_path or not bold_path:
        raise RuntimeError("No supported TrueType font was found")

    brand_font = ImageFont.truetype(str(bold_path), 31)
    date_font = ImageFont.truetype(str(regular_path), 27)
    title_font = ImageFont.truetype(str(bold_path), 54)
    bullet_font = ImageFont.truetype(str(bold_path), 31)
    small_font = ImageFont.truetype(str(regular_path), 26)
    cta_font = ImageFont.truetype(str(bold_path), 36)

    def wrap_pixels(text: str, font: Any, max_width: int, max_lines: int) -> list[str]:
        words = text.split()
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            right = draw.textbbox((0, 0), candidate, font=font)[2]
            if right <= max_width:
                current = candidate
                continue
            if current:
                lines.append(current)
            current = word
            if len(lines) >= max_lines:
                break
        if current and len(lines) < max_lines:
            lines.append(current)
        if len(lines) == max_lines and len(" ".join(lines).split()) < len(words):
            lines[-1] = lines[-1].rstrip(" .") + "…"
        return lines

    draw.text((80, 82), "TOOLSIGNAL DAILY", font=brand_font, fill="#b692f6")
    draw.text((80, 148), date_label, font=date_font, fill="#d0d5dd")
    title_lines = wrap_pixels(article["title"], title_font, 835, 4)
    y = 245
    for line in title_lines:
        draw.text((80, y), line, font=title_font, fill="white")
        y += 66

    y = max(530, y + 42)
    for item in items[:3]:
        bullet_lines = wrap_pixels(item.title, bullet_font, 790, 2)
        draw.text((82, y), "•", font=bullet_font, fill="#f2f4f7")
        for line_index, line in enumerate(bullet_lines):
            draw.text((120, y + line_index * 43), line, font=bullet_font, fill="#f2f4f7")
        y += max(150, len(bullet_lines) * 43 + 78)

    draw.line((80, 1120, 920, 1120), fill="#475467", width=2)
    draw.text((80, 1180), "Official sources • Practical analysis • No hype", font=small_font, fill="#98a2b3")
    draw.text((80, 1280), "Save 20 minutes.", font=cta_font, fill="#e9d7fe")
    draw.text((80, 1330), "Test one useful update.", font=cta_font, fill="#e9d7fe")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="PNG", optimize=True)


def as_serializable(items: list[FeedItem]) -> list[dict[str, Any]]:
    return [asdict(item) for item in items]

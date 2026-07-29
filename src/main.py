from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from content_engine import (
    as_serializable,
    deterministic_article,
    fetch_all_sources,
    generate_with_gemini,
    quality_check,
    render_card_png,
    render_card_svg,
    safe_slug,
    score_items,
    select_balanced,
)
from publishers import (
    guarded,
    publish_blogger,
    publish_bluesky,
    publish_pinterest,
    publish_threads,
    send_report_email,
)


ROOT = Path(__file__).resolve().parents[1]


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def ensure_directories() -> None:
    for name in ("data", "output", "assets", "public", "public/assets", "reports", "logs"):
        (ROOT / name).mkdir(parents=True, exist_ok=True)


def append_log(event: dict[str, Any]) -> None:
    event = {"at": dt.datetime.now(dt.timezone.utc).isoformat(), **event}
    path = ROOT / "logs" / "runs.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def make_public_index(article: dict[str, str], slug: str, date_label: str) -> None:
    article_path = ROOT / "public" / f"{slug}.html"
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{article['title']}</title><meta name="description" content="{article['excerpt']}">
<style>body{{font:18px/1.7 system-ui;margin:0;background:#f8fafc;color:#101828}}main{{max-width:820px;margin:40px auto;padding:36px;background:white;border-radius:20px}}a{{color:#6941c6}}.meta{{color:#667085}}h1,h2{{line-height:1.2}}</style></head>
<body><main><p class="meta">ToolSignal Daily · {date_label}</p><h1>{article['title']}</h1>{article['html']}</main></body></html>"""
    article_path.write_text(document, encoding="utf-8")
    index = ROOT / "public" / "index.html"
    index.write_text(
        f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ToolSignal Daily</title></head>
<body style="font:18px system-ui;max-width:760px;margin:60px auto;padding:20px"><h1>ToolSignal Daily</h1><p>Practical, source-linked AI tool updates for small teams.</p><h2>Latest</h2><p><a href="./{slug}.html">{article['title']}</a></p></body></html>""",
        encoding="utf-8",
    )


def report_text(run: dict[str, Any]) -> str:
    publishing = run.get("publishing", {})
    lines = [
        f"ToolSignal Daily 每日经营日报｜{run['date']}",
        "",
        f"抓取条目：{run['metrics']['fetched']}",
        f"合格候选：{run['metrics']['qualified']}",
        f"采用来源：{run['metrics']['selected']}",
        f"文章来源：{run['article']['provider']}",
        f"文章字数：{run['quality']['word_count']}",
        f"质量检查：{'通过' if run['quality']['pass'] else '未通过'}",
        f"质量原因：{', '.join(run['quality']['reasons']) or '无'}",
        "",
        "发布状态：",
    ]
    for platform, result in publishing.items():
        lines.append(f"- {platform}: {result.get('status', 'unknown')} {result.get('reason', '')}".rstrip())
    lines.extend(
        [
            "",
            f"数据源异常：{len(run.get('source_errors', []))}",
            "收入：0（尚未接入并通过 AdSense）",
            "今日优化：自动去重、来源多样性、禁止高风险主题、最低字数与来源链接检查。",
            "下一步：连接 Blogger，审核首批草稿质量，再开启低风险内容自动公开发布。",
            "",
            "说明：没有编造流量、收入或转化数据。",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    ensure_directories()
    settings = load_json(ROOT / "config" / "settings.json", {})
    sources = load_json(ROOT / "config" / "sources.json", [])
    state = load_json(ROOT / "data" / "state.json", {"seen": {}, "runs": 0})
    now = dt.datetime.now().astimezone()
    date_label = now.strftime("%Y-%m-%d")
    fetched, source_errors = fetch_all_sources(sources)
    unseen = [item for item in fetched if item.fingerprint not in state.get("seen", {})]
    qualified = score_items(unseen, settings)
    maximum = int(os.getenv("MAX_ITEMS", settings.get("max_items", 5)))
    selected = select_balanced(qualified, maximum)
    if len(selected) < int(settings.get("minimum_sources", 3)):
        run = {
            "date": date_label,
            "status": "no_publish",
            "reason": "insufficient_fresh_sources",
            "metrics": {"fetched": len(fetched), "qualified": len(qualified), "selected": len(selected)},
            "source_errors": source_errors,
        }
        write_json(ROOT / "reports" / f"{date_label}.json", run)
        append_log(run)
        print(json.dumps(run, ensure_ascii=False, indent=2))
        return 2

    article = generate_with_gemini(selected, settings, date_label)
    if article is None:
        article = deterministic_article(selected, settings, date_label)
    quality = quality_check(article, selected, settings)
    slug = f"{date_label}-{safe_slug(article['title'])}"
    output = {
        "date": date_label,
        "slug": slug,
        "article": article,
        "items": as_serializable(selected),
        "quality": quality,
    }
    write_json(ROOT / "output" / f"{slug}.json", output)
    (ROOT / "output" / f"{slug}.html").write_text(article["html"], encoding="utf-8")
    svg = render_card_svg(article, selected, date_label)
    svg_path = ROOT / "assets" / f"{slug}.svg"
    svg_path.write_text(svg, encoding="utf-8")
    asset_path = ROOT / "assets" / f"{slug}.png"
    render_card_png(article, selected, date_label, asset_path)
    shutil.copy2(asset_path, ROOT / "public" / "assets" / asset_path.name)
    make_public_index(article, slug, date_label)

    dry_run = env_bool("DRY_RUN", True)
    auto_publish = env_bool("AUTO_PUBLISH", False)
    public_allowed = auto_publish and quality["pass"] and not dry_run
    public_base = os.getenv("PUBLIC_ASSET_BASE_URL", "").rstrip("/")
    article_url = f"{public_base}/{slug}.html" if public_base else ""
    image_url = f"{public_base}/assets/{asset_path.name}" if public_base else ""
    social_text = f"{article['title']}\n\n{article['excerpt']}\n{article_url}".strip()

    publishing: dict[str, Any] = {}
    publishing["blogger"] = guarded(publish_blogger, article, not public_allowed)
    if public_allowed:
        publishing["bluesky"] = guarded(publish_bluesky, social_text[:290])
        publishing["threads"] = guarded(publish_threads, social_text[:500])
        publishing["pinterest"] = guarded(
            publish_pinterest,
            article["title"],
            article["excerpt"],
            article_url,
            image_url,
        )
    else:
        for platform in ("bluesky", "threads", "pinterest"):
            publishing[platform] = {
                "status": "skipped",
                "reason": "dry_run_or_quality_gate",
            }

    run = {
        "date": date_label,
        "status": "completed",
        "metrics": {"fetched": len(fetched), "qualified": len(qualified), "selected": len(selected)},
        "article": {"title": article["title"], "provider": article["provider"], "slug": slug},
        "quality": quality,
        "publishing": publishing,
        "source_errors": source_errors,
        "revenue_usd": 0,
    }
    report = report_text(run)
    (ROOT / "reports" / f"{date_label}.txt").write_text(report, encoding="utf-8")
    write_json(ROOT / "reports" / f"{date_label}.json", run)
    email_result = guarded(
        send_report_email,
        f"ToolSignal Daily 每日经营日报｜{date_label}",
        report,
    )
    run["report_email"] = email_result
    write_json(ROOT / "reports" / f"{date_label}.json", run)

    if quality["pass"]:
        for item in selected:
            state.setdefault("seen", {})[item.fingerprint] = {
                "url": item.url,
                "drafted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            }
    state["runs"] = int(state.get("runs", 0)) + 1
    state["last_run"] = dt.datetime.now(dt.timezone.utc).isoformat()
    state["last_slug"] = slug
    write_json(ROOT / "data" / "state.json", state)
    append_log(run)
    print(json.dumps(run, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

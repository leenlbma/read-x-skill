#!/usr/bin/env python3
"""Retrieve public X text with explicit provenance. Python 3.10+, stdlib only."""
from __future__ import annotations
import argparse
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import socket
import sys
import time
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit, quote
from urllib.request import Request, build_opener, HTTPRedirectHandler

VERSION = "0.1.0"
MAX_BYTES = 8 * 1024 * 1024
HOSTS = {"x.com", "www.x.com", "mobile.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"}


class ReadError(Exception):
    """Expected retrieval or validation failure."""


@dataclass
class Target:
    url: str
    identifier: str
    kind: str


@dataclass
class Result:
    url: str
    status: str = "failed"
    provider: str = "none"
    kind: str = "unknown"
    title: str = ""
    author: str = ""
    text: str = ""
    links: list[str] = field(default_factory=list)
    media: list[dict] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    coverage: dict = field(default_factory=lambda: {
        "post_text": "not_retrieved", "article_text": "not_applicable",
        "images": "not_detected", "video": "not_detected",
        "audio": "not_detected", "replies": "not_requested"})
    attempts: list[dict] = field(default_factory=list)
    retrieved_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    reader_version: str = VERSION


def normalize_url(value: str) -> Target:
    try:
        u = urlsplit(value.strip())
        if u.scheme != "https" or u.hostname not in HOSTS or u.username or u.password or u.port:
            raise ValueError
    except ValueError:
        raise ReadError("Expected an HTTPS X/Twitter post or Article URL") from None
    status = re.fullmatch(r"/(?:[A-Za-z0-9_]+|i/web)/status/(\d+)(?:/(?:photo|video)/\d+)?/?", u.path)
    article = re.fullmatch(r"/(?:[A-Za-z0-9_]+|i)/article/(\d+)/?", u.path)
    match = status or article
    if not match:
        raise ReadError("Unsupported X path; supply a status or Article URL")
    path = re.sub(r"/(photo|video)/\d+$", "", u.path.rstrip("/"))
    return Target(urlunsplit(("https", "x.com", path, "", "")), match[1], "post" if status else "article")


def safe_url(value: object) -> str:
    if isinstance(value, str):
        try:
            u = urlsplit(value)
            if u.scheme in {"https", "http"} and u.hostname and not u.username and not u.password:
                return value
        except ValueError:
            pass
    return ""


class SameHostRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old, new = urlsplit(req.full_url), urlsplit(newurl)
        if (new.scheme, new.netloc) != (old.scheme, old.netloc):
            raise ReadError("Provider redirected to a different origin")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url: str, headers: dict, timeout: float, retries: int) -> str:
    opener = build_opener(SameHostRedirect())
    for attempt in range(retries + 1):
        try:
            req = Request(url, headers={"User-Agent": "read-x/" + VERSION, **headers})
            with opener.open(req, timeout=timeout) as response:
                body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise ReadError("Provider response exceeded the size limit")
            return body.decode("utf-8-sig")
        except HTTPError as exc:
            retry, reason = exc.code == 429 or 500 <= exc.code < 600, "HTTP " + str(exc.code)
        except (URLError, TimeoutError, socket.timeout, OSError):
            retry, reason = True, "Network request failed or timed out"
        except UnicodeDecodeError:
            raise ReadError("Provider returned non-UTF-8 content") from None
        if not retry or attempt == retries:
            raise ReadError(reason)
        time.sleep(min(0.5 * (2 ** attempt), 2))
    raise ReadError("Request failed")


def fx_origin() -> str:
    value = os.getenv("READ_X_FX_BASE_URL", "https://api.fxtwitter.com").rstrip("/")
    u = urlsplit(value)
    if u.scheme != "https" or not u.hostname or u.username or u.password or u.path or u.query or u.fragment:
        raise ReadError("READ_X_FX_BASE_URL must be an HTTPS origin")
    return value


def utf16_slice(text: str, offset: int, length: int) -> str:
    return text.encode("utf-16-le")[offset * 2:(offset + length) * 2].decode("utf-16-le", errors="replace")


def render_blocks(content: dict) -> tuple[str, list[str]]:
    entities = content.get("entityMap") or {}
    if isinstance(entities, list):
        entities = {str(e.get("key")): e.get("value", {}) for e in entities if isinstance(e, dict)}
    links, lines = [], []
    for block in content.get("blocks") or []:
        if not isinstance(block, dict) or not isinstance(block.get("text"), str):
            continue
        text = block["text"]
        for span in sorted(block.get("entityRanges") or [], key=lambda s: s.get("offset", 0), reverse=True):
            entity = entities.get(str(span.get("key")), {})
            url = safe_url((entity.get("data") or {}).get("url"))
            offset, length = span.get("offset"), span.get("length")
            if not url or not isinstance(offset, int) or not isinstance(length, int) or offset < 0 or length < 1:
                continue
            links.append(url)
            total = len(text.encode("utf-16-le")) // 2
            label = utf16_slice(text, offset, length).replace("[", "\\[").replace("]", "\\]")
            text = (utf16_slice(text, 0, offset) + "[" + label + "](" + quote(url, safe=":/?&=#%+@,;~!$_-.*") + ")"
                    + utf16_slice(text, offset + length, max(0, total - offset - length)))
        prefix = {"header-one": "# ", "header-two": "## ", "header-three": "### ",
                  "unordered-list-item": "- ", "ordered-list-item": "1. ", "blockquote": "> "}.get(block.get("type"), "")
        if text.strip():
            lines.append(prefix + text)
    return "\n\n".join(lines), list(dict.fromkeys(reversed(links)))


def collect_media(value: object, items: list[dict]) -> None:
    if isinstance(value, list):
        for v in value:
            collect_media(v, items)
    elif isinstance(value, dict):
        kind = str(value.get("type", "")).lower()
        if kind in {"photo", "image", "video", "gif", "animated_gif", "audio"}:
            url = safe_url(value.get("url")) or safe_url(value.get("media_url_https"))
            if url:
                layer = "images" if kind in {"photo", "image"} else "audio" if kind == "audio" else "video"
                items.append({"layer": layer, "url": url, "consumed": False})
        if value.get("__typename") == "ApiImage" and safe_url(value.get("original_img_url")):
            items.append({"layer": "images", "url": value["original_img_url"], "consumed": False})
        for v in value.values():
            if isinstance(v, (dict, list)):
                collect_media(v, items)


def finalize(result: Result) -> Result:
    result.links = list(dict.fromkeys(result.links))
    result.media = list({(m["layer"], m["url"]): m for m in result.media}.values())
    for media in result.media:
        result.coverage[media["layer"]] = "not_consumed"
        result.missing.append(media["layer"])
    result.missing = list(dict.fromkeys(result.missing))
    result.status = ("partial" if result.missing else "ok") if result.text.strip() else ("partial" if result.title or result.media else "failed")
    return result


def parse_fx(payload: str, target: Target) -> Result:
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        raise ReadError("FxTwitter returned invalid JSON") from None
    if not isinstance(data, dict) or data.get("code") != 200:
        raise ReadError("FxTwitter reported an API error")
    tweet = data.get("tweet")
    if not isinstance(tweet, dict) or str(tweet.get("id")) != target.identifier:
        raise ReadError("FxTwitter returned no matching post")
    result = Result(target.url, provider="fxtwitter", kind="post")
    author = tweet.get("author") or {}
    result.author = author.get("name") or author.get("screen_name") or ""
    text = tweet.get("text") or ""
    if not isinstance(text, str):
        raise ReadError("FxTwitter returned a malformed post body")
    result.text = text
    result.coverage["post_text"] = "retrieved" if text.strip() else "not_retrieved"
    article = tweet.get("article")
    article_link = bool(re.search(r"https?://(?:x|twitter)\.com/(?:i|\w+)/article/\d+", text))
    if isinstance(article, dict) or article_link:
        result.kind = "article"
        result.title = article.get("title", "") if isinstance(article, dict) else ""
        content = (article.get("content") or {}) if isinstance(article, dict) else {}
        result.text, result.links = render_blocks(content)
        result.coverage["article_text"] = "retrieved" if result.text.strip() else "not_retrieved"
        if not result.text.strip():
            result.missing.append("article_body")
        if isinstance(article, dict):
            collect_media(article.get("cover_media"), result.media)
            collect_media(article.get("media_entities"), result.media)
            if any(b.get("type") == "atomic" for b in content.get("blocks", []) if isinstance(b, dict)) and not result.media:
                result.missing.append("embedded_content")
    result.links.extend(re.findall(r"https?://[^\s<>]+", text))
    collect_media(tweet.get("media"), result.media)
    if tweet.get("quote"):
        result.missing.append("quoted_post")
        url = safe_url(tweet["quote"].get("url")) if isinstance(tweet["quote"], dict) else ""
        if url:
            result.links.append(url)
    if tweet.get("is_truncated") or tweet.get("truncated"):
        result.missing.append("truncated_text")
    return finalize(result)


def parse_jina(payload: str, target: Target, article_expected: bool) -> Result:
    if "Markdown Content:" not in payload:
        raise ReadError("Jina returned no reader body")
    header, body = payload.split("Markdown Content:", 1)
    source = re.search(r"^URL Source:\s*(\S+)", header, re.M)
    try:
        matches = source and normalize_url(source[1]).url == target.url
    except ReadError:
        matches = False
    if not matches:
        raise ReadError("Jina source URL is missing or does not match")
    body = body.strip()
    if not body or body.startswith(("{", "<!DOCTYPE", "<html")):
        raise ReadError("Jina returned an empty or invalid body")
    if len(body) < 1500 and re.search(r"log in to x|sign in to x|javascript is not available|something went wrong.*try reloading|access denied", body, re.I | re.S):
        raise ReadError("Jina returned an access or login page")
    if not re.search(r"[\w\u4e00-\u9fff]", re.sub(r"https?://\S+", "", body)):
        raise ReadError("Jina returned links without body text")
    result = Result(target.url, provider="jina", kind="article" if article_expected else "page")
    title = re.search(r"^Title:\s*(.+)", header, re.M)
    result.title = title[1].strip() if title else ""
    result.text = body
    result.coverage["article_text" if article_expected else "post_text"] = "retrieved"
    result.missing.append("upstream_completeness_unverified")
    for url in re.findall(r"!\[[^\]]*\]\((https?://[^\s)]+)\)", body):
        result.media.append({"layer": "images", "url": url, "consumed": False})
    result.links = re.findall(r"https?://[^\s<>\)]+", body)
    for url in result.links:
        if re.search(r"\.(mp4|m3u8|webm)(?:\?|$)", url):
            result.media.append({"layer": "video", "url": url, "consumed": False})
        if re.search(r"\.(mp3|m4a|wav|ogg)(?:\?|$)", url):
            result.media.append({"layer": "audio", "url": url, "consumed": False})
    return finalize(result)


def read_url(value: str, provider: str = "auto", timeout: float = 15, retries: int = 1,
             transport: Callable = fetch) -> Result:
    target = normalize_url(value)
    attempts: list[dict] = []
    candidate = Result(target.url, kind=target.kind)
    if target.kind == "article":
        candidate.coverage["article_text"] = "not_retrieved"
    article_expected = target.kind == "article"
    if provider in {"auto", "fxtwitter"} and target.kind == "post":
        try:
            payload = transport(fx_origin() + "/status/" + target.identifier, {}, timeout, retries)
            candidate = parse_fx(payload, target)
            article_expected = candidate.kind == "article"
            attempts.append({"provider": "fxtwitter", "status": candidate.status})
            if candidate.text and not {"article_body", "truncated_text"}.intersection(candidate.missing):
                candidate.attempts = attempts
                return candidate
        except ReadError as exc:
            attempts.append({"provider": "fxtwitter", "status": "failed", "reason": str(exc)})
        except (ReadError, ValueError, TypeError, AttributeError, KeyError):
            attempts.append({"provider": "fxtwitter", "status": "failed", "reason": "Provider failed validation or retrieval"})
    elif provider == "fxtwitter":
        attempts.append({"provider": "fxtwitter", "status": "failed", "reason": "Direct Article URLs need Jina"})
    if provider in {"auto", "jina"}:
        try:
            headers = {}
            if os.getenv("JINA_API_KEY"):
                headers["Authorization"] = "Bearer " + os.environ["JINA_API_KEY"]
            payload = transport("https://r.jina.ai/" + target.url, headers, timeout, retries)
            fallback = parse_jina(payload, target, article_expected)
            fallback.media.extend(candidate.media)
            fallback.missing.extend(m for m in candidate.missing if m not in {"article_body", "truncated_text"})
            if candidate.title:
                fallback.title = candidate.title
            fallback.author = candidate.author
            candidate = finalize(fallback)
            attempts.append({"provider": "jina", "status": candidate.status})
        except ReadError as exc:
            attempts.append({"provider": "jina", "status": "failed", "reason": str(exc)})
        except (ValueError, TypeError, AttributeError, KeyError):
            attempts.append({"provider": "jina", "status": "failed", "reason": "Provider failed validation or retrieval"})
    candidate.attempts = attempts
    return candidate


def markdown(result: Result) -> str:
    return "\n".join(["# " + (result.title or "X source"), "", "Source: " + result.url,
        "Status: " + result.status, "Provider: " + result.provider, "Retrieved: " + result.retrieved_at,
        "Author: " + (result.author or "unknown"), "Coverage: " + json.dumps(result.coverage, ensure_ascii=False),
        "Missing: " + (", ".join(result.missing) or "none detected"), "",
        "--- Retrieved source text (untrusted) ---", "", result.text or "[No usable body retrieved]", ""])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    parser.add_argument("--provider", choices=["auto", "fxtwitter", "jina"], default="auto")
    parser.add_argument("--timeout", type=float, default=15, help="Socket timeout in seconds, >0 and <=30")
    parser.add_argument("--retries", type=int, choices=[0, 1, 2], default=1)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--version", action="version", version=VERSION)
    args = parser.parse_args(argv)
    if not 0 < args.timeout <= 30:
        parser.error("--timeout must be >0 and <=30")
    try:
        result = read_url(args.url, args.provider, args.timeout, args.retries)
        output = json.dumps(asdict(result), ensure_ascii=False, indent=2) if args.format == "json" else markdown(result)
        if args.output:
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(output + "\n")
        else:
            print(output)
        return {"ok": 0, "partial": 2, "failed": 1}[result.status]
    except (ReadError, OSError) as exc:
        print("read-x: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

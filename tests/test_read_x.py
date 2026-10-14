"""Deterministic tests with synthetic source data; no network or credentials."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "skills/read-x/scripts/read_x.py"
spec = importlib.util.spec_from_file_location("read_x", SCRIPT)
rx = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = rx
spec.loader.exec_module(rx)
URL = "https://x.com/example/status/123"


def post(**extra):
    return json.dumps({"code": 200, "tweet": {"id": "123", "text": "A synthetic public post.", **extra}})


def jina(body="Synthetic article body.", source=URL):
    return "Title: Example\nURL Source: " + source + "\n\nMarkdown Content:\n" + body


class ReaderTests(unittest.TestCase):
    def test_normalize_aliases_tracking_and_attachment_suffix(self):
        t = rx.normalize_url("https://mobile.twitter.com/example/status/123/photo/1?s=46#tracking")
        self.assertEqual(t.url, URL)
        self.assertEqual(t.identifier, "123")

    def test_reject_non_x_urls_credentials_and_arbitrary_paths(self):
        for url in ["http://x.com/a/status/123", "https://x.com.evil.test/a/status/123", "https://evil@x.com/a/status/123", "https://x.com/a", "https://x.com:444/a/status/123", "file:///etc/passwd"]:
            with self.subTest(url=url), self.assertRaises(rx.ReadError):
                rx.normalize_url(url)

    def test_plain_post(self):
        result = rx.parse_fx(post(), rx.normalize_url(URL))
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.coverage["post_text"], "retrieved")
        self.assertEqual(result.coverage["replies"], "not_requested")

    def test_article_body_wins_over_post_link(self):
        article = {"title": "Synthetic article", "content": {"blocks": [
            {"type": "header-two", "text": "A heading"},
            {"type": "unstyled", "text": "A full article paragraph."}]}}
        result = rx.parse_fx(post(text="https://x.com/i/article/456", article=article), rx.normalize_url(URL))
        self.assertEqual(result.kind, "article")
        self.assertIn("## A heading", result.text)
        self.assertEqual(result.coverage["article_text"], "retrieved")

    def test_entity_links_use_utf16_offsets_after_emoji(self):
        content = {"entityMap": [{"key": "0", "value": {"type": "LINK", "data": {"url": "https://example.com/guide"}}}],
                   "blocks": [{"type": "unstyled", "text": "😀 guide", "entityRanges": [{"key": 0, "offset": 3, "length": 5}]}]}
        text, links = rx.render_blocks(content)
        self.assertEqual(text, "😀 [guide](https://example.com/guide)")
        self.assertEqual(links, ["https://example.com/guide"])

    def test_unsafe_link_is_not_rendered(self):
        content = {"entityMap": {"0": {"data": {"url": "javascript:alert(1)"}}},
                   "blocks": [{"text": "guide", "entityRanges": [{"key": 0, "offset": 0, "length": 5}]}]}
        self.assertEqual(rx.render_blocks(content), ("guide", []))

    def test_preview_is_not_an_article_body(self):
        result = rx.parse_fx(post(article={"title": "Title only", "preview_text": "Preview only"}), rx.normalize_url(URL))
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.text, "")
        self.assertIn("article_body", result.missing)

    def test_wrong_id_or_json_error_rejected(self):
        for payload in ["{}", "not-json", json.dumps({"code": 403}), post(id="999")]:
            with self.subTest(payload=payload), self.assertRaises(rx.ReadError):
                rx.parse_fx(payload, rx.normalize_url(URL))

    def test_media_requires_explicit_unread_layer(self):
        result = rx.parse_fx(post(media={"photos": [{"type": "photo", "url": "https://pbs.twimg.com/media/synthetic.jpg"}],
                                                 "videos": [{"type": "video", "url": "https://video.twimg.com/a.mp4"}]}), rx.normalize_url(URL))
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.coverage["images"], "not_consumed")
        self.assertIn("video", result.missing)
        self.assertTrue(all(m["consumed"] is False for m in result.media))

    def test_quoted_post_is_not_claimed_read(self):
        result = rx.parse_fx(post(quote={"url": "https://x.com/other/status/456", "text": "Not part of requested source"}), rx.normalize_url(URL))
        self.assertIn("quoted_post", result.missing)
        self.assertNotIn("Not part", result.text)

    def test_jina_refusal_login_empty_mismatch_and_link_only_rejected(self):
        for payload in ['{"code":403}', jina(""), jina("Log in to X"), jina(source="https://x.com/other/status/456"), jina("https://x.com/i/article/456")]:
            with self.subTest(payload=payload), self.assertRaises(rx.ReadError):
                rx.parse_jina(payload, rx.normalize_url(URL), True)

    def test_jina_is_conservative_about_completeness(self):
        result = rx.parse_jina(jina(), rx.normalize_url(URL), True)
        self.assertEqual(result.status, "partial")
        self.assertIn("upstream_completeness_unverified", result.missing)

    def test_primary_failure_does_not_prevent_fallback(self):
        calls = []
        def transport(url, headers, timeout, retries):
            calls.append(url)
            if "fxtwitter" in url:
                raise rx.ReadError("HTTP 503")
            return jina()
        result = rx.read_url(URL, transport=transport)
        self.assertEqual(result.provider, "jina")
        self.assertEqual(len(calls), 2)
        self.assertEqual(result.attempts[0]["reason"], "HTTP 503")

    def test_missing_article_body_tries_fallback(self):
        def transport(url, *args):
            return post(article={"title": "Title", "preview_text": "Preview"}) if "fxtwitter" in url else jina()
        result = rx.read_url(URL, transport=transport)
        self.assertEqual(result.coverage["article_text"], "retrieved")
        self.assertNotIn("article_body", result.missing)

    def test_primary_partial_survives_fallback_failure(self):
        def transport(url, *args):
            if "fxtwitter" in url:
                return post(article={"title": "Title only"})
            raise rx.ReadError("HTTP 403")
        result = rx.read_url(URL, transport=transport)
        self.assertEqual(result.provider, "fxtwitter")
        self.assertEqual(result.status, "partial")
        self.assertIn("article_body", result.missing)

    def test_truncated_post_tries_fallback(self):
        def transport(url, *args):
            return post(truncated=True) if "fxtwitter" in url else jina()
        result = rx.read_url(URL, transport=transport)
        self.assertEqual(result.provider, "jina")
        self.assertNotIn("truncated_text", result.missing)

    def test_both_failures_are_not_success(self):
        def transport(*args):
            raise rx.ReadError("HTTP 503")
        result = rx.read_url(URL, transport=transport)
        self.assertEqual(result.status, "failed")
        self.assertEqual(len(result.attempts), 2)

    def test_direct_article_routes_to_jina(self):
        url = "https://x.com/i/article/456"
        calls = []
        def transport(endpoint, *args):
            calls.append(endpoint)
            return jina(source=url)
        self.assertEqual(rx.read_url(url, transport=transport).kind, "article")
        self.assertEqual(calls, ["https://r.jina.ai/" + url])

    def test_keys_only_go_to_jina_and_never_output(self):
        calls = []
        def transport(url, headers, *args):
            calls.append((url, headers))
            return '{}' if "fxtwitter" in url else jina()
        with patch.dict(os.environ, {"JINA_API_KEY": "synthetic-test-secret"}):
            result = rx.read_url(URL, transport=transport)
        self.assertEqual(calls[0][1], {})
        self.assertEqual(calls[1][1]["Authorization"], "Bearer synthetic-test-secret")
        self.assertNotIn("synthetic-test-secret", json.dumps(rx.asdict(result)))

    def test_self_host_origin_rejects_path_credentials_and_http(self):
        for value in ["http://example.com", "https://example.com/path", "https://user:pass@example.com", "https://example.com?token=x"]:
            with self.subTest(value=value), patch.dict(os.environ, {"READ_X_FX_BASE_URL": value}), self.assertRaises(rx.ReadError):
                rx.fx_origin()

    def test_cli_exit_codes_and_no_overwrite(self):
        for status, code in [("ok", 0), ("partial", 2), ("failed", 1)]:
            with patch.object(rx, "read_url", return_value=rx.Result(URL, status=status)), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(rx.main([URL, "--format", "json"]), code)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.json"
            path.write_text("keep me")
            with patch.object(rx, "read_url", return_value=rx.Result(URL)), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(rx.main([URL, "--output", str(path)]), 1)
            self.assertEqual(path.read_text(), "keep me")

    def test_retry_transient_http_error_only(self):
        from urllib.error import HTTPError
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"code":200}'
        opener = unittest.mock.Mock()
        opener.open.side_effect = [HTTPError(URL, 503, "Unavailable", {}, None), response]
        with patch.object(rx, "build_opener", return_value=opener), patch.object(rx.time, "sleep"):
            self.assertEqual(rx.fetch("https://api.fxtwitter.com/status/123", {}, 1, 1), '{"code":200}')
        self.assertEqual(opener.open.call_count, 2)

    def test_permanent_http_error_does_not_retry(self):
        from urllib.error import HTTPError
        opener = unittest.mock.Mock()
        opener.open.side_effect = HTTPError(URL, 404, "Missing", {}, None)
        with patch.object(rx, "build_opener", return_value=opener), self.assertRaisesRegex(rx.ReadError, "HTTP 404"):
            rx.fetch("https://api.fxtwitter.com/status/123", {}, 1, 2)
        self.assertEqual(opener.open.call_count, 1)

    def test_response_size_limit(self):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'x' * (rx.MAX_BYTES + 1)
        opener = unittest.mock.Mock()
        opener.open.return_value = response
        with patch.object(rx, "build_opener", return_value=opener), self.assertRaisesRegex(rx.ReadError, "size limit"):
            rx.fetch("https://api.fxtwitter.com/status/123", {}, 1, 0)

    def test_redirect_cannot_forward_credentials_to_another_origin(self):
        req = rx.Request("https://r.jina.ai/" + URL, headers={"Authorization": "Bearer synthetic-test-secret"})
        with self.assertRaisesRegex(rx.ReadError, "different origin"):
            rx.SameHostRedirect().redirect_request(req, None, 302, "Redirect", {}, "https://example.com/leak")


if __name__ == "__main__":
    unittest.main()

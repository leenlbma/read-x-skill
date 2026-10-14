# Provider notes

- FxEmbed API: https://docs.fxembed.com/api/introduction/
- FxEmbed source: https://github.com/FxEmbed/FxEmbed
- Jina Reader: https://github.com/jina-ai/reader

Use the original X link for attribution. Third-party providers receive the requested public URL. Availability, schemas, access rules, and pricing can change; owning this code does not remove upstream dependencies.

The default legacy route is `https://api.fxtwitter.com/status/<id>`. Article blocks and heading/list links were verified on 2026-10-14. Not all posts contain them. The documented v2 quota does not establish the legacy route's quota.

Set `READ_X_FX_BASE_URL=https://your-instance.example` for a trusted, legacy-compatible self-hosted API. Only an HTTPS origin without credentials, path, query, or fragment is accepted. No X credentials are forwarded. Jina remains independent at `https://r.jina.ai/<canonical-x-url>`; `JINA_API_KEY` is optional and never logged.

Require a matching Jina `URL Source:` header, a nonempty `Markdown Content:` body, and no recognized access-error shell. A marker alone is insufficient proof of a complete read. Jina text is always reported as `partial` with `upstream_completeness_unverified`.

Coverage uses `retrieved`, `not_retrieved`, and `not_applicable` for text; `not_consumed` or `not_detected` for media. Replies are `not_requested`. Links are discoveries, not pages read. Missing-layer flags include `article_body`, `images`, `video`, `audio`, `quoted_post`, and `upstream_completeness_unverified`. Absence of detection is not proof of absence.

The reader does not analyze images, transcribe media, expand replies, authenticate to X, or verify claims. Keep these boundaries visible when relevant.

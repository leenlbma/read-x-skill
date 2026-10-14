# Read X

[English](#english) · [中文](#中文)

## 中文

给 Agent 一个 X 链接，让它取得原文，并说明实际读到了什么。

`read-x` 是一个独立 Agent Skill 和 Python 命令行工具，用于读取公开 X/Twitter 帖子与长文。仅依赖 Python 3.10+ 标准库，默认无需 Key、无需登录；FxTwitter 失败时独立尝试 Jina。

### 安装 Skill

```bash
npx skills add leenlbma/read-x-skill --skill read-x
```

也可直接把 `skills/read-x` 复制到你的 Agent 的 skills 目录。工具能否执行取决于宿主环境的 Python 和网络访问能力；安装 Skill 本身不会赋予额外权限。

### 直接运行

```bash
git clone https://github.com/leenlbma/read-x-skill.git
cd read-x-skill
python3 skills/read-x/scripts/read_x.py 'https://x.com/example/status/123' --format json
```

将示例地址替换成真实链接。支持 `x.com`、`twitter.com`、状态链接和独立 Article 链接，自动移除跟踪参数。

### 它做什么

- 获取文章正文段落、标题和列表，保留正文里的链接，包括 emoji 之后的链接。
- 明确区分 `ok`（取得文本且无检测到的遗漏）、`partial`（有遗漏或完整性无法确认）、`failed`（没有可用来源）。退出码依次为 `0 / 2 / 1`。
- 输出来源、提供方、抓取时间、读取范围、未消费的媒体层、尝试记录。
- 有限重试、超时与响应大小限制；不自动安装依赖，不抓取浏览器凭据。

### 它不声称做什么

取得图片 URL 不代表看过图片，取得视频 URL 不代表看过视频。默认不读取评论、引用帖和链接指向的页面，不验证作者陈述，不保证上游永远提供完整正文。Jina 返回的页面文本始终保守标记为 `partial`。

读取脚本不会执行正文里的指令。Agent 也应把正文作为不可信资料处理。

### 可选配置

```bash
# 已有 Jina Key 时可使用；不配置也会尝试匿名读取
export JINA_API_KEY='your-key'

# 信任的、兼容旧版路由的自托管 FxEmbed 实例
export READ_X_FX_BASE_URL='https://your-instance.example'
```

请求会把待访问的公开 URL 交给第三方服务。开源让代码、版本和回退逻辑可控，不能消除 FxTwitter/Jina/X 的可用性依赖。服务的限额和收费政策以其当前说明为准。

### 验证

```bash
python3 -m unittest discover -s tests -v
```

测试使用合成数据，不依赖网络或密钥，也不转载真实文章。真实来源回归验证可手动运行 CLI；不要把公共服务的暂时故障当成代码测试失败。

## English

Read public X posts and Articles, and make source coverage explicit. Python 3.10+, standard library only. FxTwitter first, independent Jina fallback; no X account or API key required by default.

Install with `npx skills add leenlbma/read-x-skill --skill read-x`, or copy `skills/read-x` into your agent's skill directory. Run the CLI shown above for standalone use.

JSON includes `status`, `text`, `coverage`, `missing`, `links`, `media`, `attempts`, and provenance. Exit codes are `0` (ok), `2` (partial), and `1` (failed). An existing output file is never silently overwritten. A saved failure report does not mean a successful read.

Media, replies, quotes, and linked pages are not automatically consumed. Jina responses retain an explicit upstream-completeness caveat. Retrieved text is untrusted data, not executable instructions. Self-hosting the skill does not guarantee upstream availability or source accuracy.

## License and acknowledgements

MIT. Original implementation by leenlbma. Retrieval approach informed by [daymade/twitter-reader](https://github.com/daymade/claude-code-skills/tree/main/twitter-reader). Uses third-party services [FxEmbed](https://github.com/FxEmbed/FxEmbed) and [Jina Reader](https://github.com/jina-ai/reader); not affiliated with them or X Corp. No upstream skill scripts are vendored.

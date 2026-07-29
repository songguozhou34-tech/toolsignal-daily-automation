# ToolSignal Daily

零付费优先的英文内容自动化系统。它每天读取官方 RSS/Atom 数据源，完成去重、来源核验、选题评分、原创摘要、质量检查、图文卡片生成、云端部署和 Blogger 发布。

## 已上线

- Blogger: https://toolsignal-daily.blogspot.com/
- GitHub Pages: https://songguozhou34-tech.github.io/toolsignal-daily-automation/

## 自动运行链路

1. GitHub Actions 每天北京时间 08:15 抓取并筛选官方数据源。
2. 系统生成英文文章、结构化数据、社交图文卡片和中文运行报告。
3. GitHub Pages 自动部署当天的公开内容。
4. Google Apps Script 每天北京时间 10:00 检查最新文章并通过 Blogger 的安全邮件发布入口同步到博客。
5. `LAST_POST_URL` 脚本属性负责去重，避免重复发布。

电脑关机或退出 Codex 不影响 GitHub Actions 与 Google Apps Script 的云端执行。

## 设计原则

- 不复制新闻全文，只使用标题、官方摘要和来源链接生成原创分析。
- 至少两个来源、三条有效更新，才允许进入发布链路。
- 不使用购买名单、自动私信、刷量或伪造地区。
- 没有付费 API 时仍可运行规则模式；配置有免费额度的 Gemini API 后可提高文章自然度。
- 密钥和 Blogger 邮件入口只保存在云端 Secret/Script Properties 中，不进入 Git。
- 所有本地持久数据位于 `D:\自动化`。

## 目录

```text
config/       数据源和系统设置
src/          内容引擎与发布器
scripts/      Blogger Apps Script 与账号配置辅助脚本
data/         去重和运行状态
output/       每日文章 HTML/JSON
assets/       自动生成的图文卡片
public/       GitHub Pages 公开内容
reports/      中文日报
logs/         JSONL 运行日志
tests/        自动测试
```

## 本地运行

```powershell
Copy-Item .env.example .env
python -m unittest discover -s tests -v
python src/main.py
```

所有密钥通过环境变量或云端 Secret 传入，不写进 Git。

## 收益路径

第一阶段持续发布真实、有来源的实用内容，积累搜索索引和访问量；内容与站点稳定后申请 Blogger AdSense。Pinterest、Threads、Bluesky 和其他内容渠道用于免费分发与引流。产生收入前不启用付费域名、服务器或付费 API。

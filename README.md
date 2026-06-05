# 每日美股信息

每天北京时间 08:00 通过 GitHub Actions 自动生成美股收盘日报，并发送到飞书群机器人。

正常交易日会生成包含静态图表的 HTML 报告，并通过 GitHub Pages 发布。飞书群收到摘要卡片和完整报告链接。休市日只发送简短提示，不覆盖上一个交易日的网页。

GitHub Actions 使用 UTC 时间：

- `0 0 * * *` = 北京时间每天 08:00

GitHub Secrets：

- `FEISHU_WEBHOOK_URL`
- `OPENAI_API_KEY`

HTML 页面：

- `https://maomao8701.github.io/daily-us-stock-report/`

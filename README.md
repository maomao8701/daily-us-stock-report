# 每日美股信息

每天北京时间 08:00 通过 GitHub Actions 自动生成美股收盘日报，并发送到飞书群机器人。

GitHub Actions 使用 UTC 时间：

- `0 0 * * *` = 北京时间每天 08:00

飞书 Webhook 保存在 GitHub Secret：

- `FEISHU_WEBHOOK_URL`

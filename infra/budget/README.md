# 成本告警（AWS Budgets）

月度成本预算 `explainer-video-platform-monthly`，上限 $1000。实际花费超过 $200、$500、$800、$1000 时各发一封邮件。

- 告警只通知，不会自动停止或删除任何资源。
- 统计的是整个 AWS 账号当月的花费，不只是本平台的资源。
- AWS Budgets 的数据每天更新几次，告警可能比实际花费晚几个小时。

## 重建

本仓库是公开的，所以 `notifications.json` 里的收件人写的是占位符 `ALERT_EMAIL`，真实邮箱只存在 AWS 里。重建时先替换成真实地址：

```bash
cd infra/budget
sed 's/ALERT_EMAIL/you@example.com/g' notifications.json > notifications.local.json
aws budgets create-budget --account-id <ACCOUNT_ID> \
  --budget file://budget.json \
  --notifications-with-subscribers file://notifications.local.json
```

`*.local.json` 已写进 `.gitignore`，不会被提交。

## 检查

```bash
aws budgets describe-notifications-for-budget --account-id <ACCOUNT_ID> \
  --budget-name explainer-video-platform-monthly
```

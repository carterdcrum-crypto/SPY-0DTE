# SPY 0DTE: separate Slack notification channels

The notification worker reads durable broker and paper records. It cannot
submit, preview, cancel, or modify a Webull order.

Channels already created in the spy0dte Slack workspace:
- #live-trades (C0CAE4ZH1D4) — genuine Webull order updates.
- #paper-trades (C0CAFTPNVCJ) — committed simulated entries and exits.
- #system-alerts (C0CBAER8EN4) — Railway/Webull faults and bounded signal rejections.

## Owner setup: three Slack incoming-webhook URLs

1. Visit https://api.slack.com/apps. Create New App > From scratch.
   Select the existing spy0dte workspace. Name the app SPY 0DTE Alerts.
2. In the Slack app, activate Incoming Webhooks.
3. Select Add New Webhook to Workspace. Choose #live-trades, authorize,
   and copy the generated URL. Repeat separately for #paper-trades
   and #system-alerts. Each URL is unique and grants posting access.
4. Store those URLs as sensitive Railway environment variables on the
   EXISTING trading API service:
   SLACK_LIVE_WEBHOOK_URL = #live-trades incoming-webhook URL
   SLACK_PAPER_WEBHOOK_URL = #paper-trades incoming-webhook URL
   SLACK_SYSTEM_WEBHOOK_URL = #system-alerts incoming-webhook URL
   START_SLACK_ALERTS = true
   The START_SLACK_ALERTS variable may already be set.
5. Deploy a tested main branch after active trading hours. The Slack
   worker starts only when all three URLs are configured. Check
   Railway logs for the worker's channels=live,paper,system startup.

## Railway platform alerts, including complete API outage

Also create a native Railway project webhook pointing to the SAME
#system-alerts incoming-webhook URL (Railway project > Settings >
Webhooks). Select Deployment.failed, Deployment.crashed and
Deployment.oom_killed. Optionally include Deployment.deployed.
Railway recognizes a Slack webhook URL and formats its notifications.
This platform webhook is independent of the Python trading service.

## Accuracy and security

- LIVE notifications distinguish working/unknown orders from broker-confirmed
  fills. Unknown and working order messages are NOT fill confirmations.
- PAPER notifications come from committed cash-ledger trades. They are
  always simulations and never Webull executions.
- System messages are rate-limited in fifteen-minute time buckets;
  ordinary missing V5 signals are not classified as outages.
- Slack messages contain no account ID, API key, token or webhook URL.
- If Slack is unavailable, trading continues; notifications retry.
- A rare network failure after Slack acceptance may duplicate an ALERT.
  It does not create another broker order.
- The notifier cannot prove real Webull order submission has ever worked.
  Only an actual broker response and reconciliation can establish that.
- Never paste webhook URLs into public Slack channels or commit them
  to GitHub. Treat them like passwords and rotate if exposed.

Tests: pytest -q tests/test_slack_alerts.py

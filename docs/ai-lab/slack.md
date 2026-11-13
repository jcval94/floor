# Slack enablement and recovery

**Live integration is not enabled or verified in this checkpoint. No workspace was accessed; no channel or credential was created.** `post_message` is an actual Web API adapter, not a simulator. Automated tests inject a fake sender to verify outbox behavior; those ts values are fixtures, not Slack evidence.

An authorized workspace administrator must:

1. Select the workspace and create/approve a Slack App. Add bot scope `chat:write`; install with workspace authorization. Avoid broad history/admin scopes. Incoming webhook is simpler, but Web API is selected for acknowledgement `ts` and experiment threads.
2. Create or select a private channel (e.g. floor-experiments), explicitly invite the bot, and record the channel ID. A channel name alone does not prove existence or membership.
3. Put the bot token in the worker's managed secret environment as `FLOOR_SLACK_BOT_TOKEN`; configure `FLOOR_SLACK_CHANNEL_ID`. Do not paste tokens in prompts, Git, PRs, config examples or logs. Configure token in MCP only if `floor_test_slack_connection` is wanted; computation children never receive it.
4. Run `floor_test_slack_connection` as runner. It invokes `auth.test`: verifies the configured token, **not** delivery permissions to the selected private channel.
5. Submit a reviewed registered experiment. Keep the independent worker alive. Inspect ACCEPTED, RUNNING and terminal outbox rows. `DELIVERED` requires Slack `ok=true` and returned `ts`. Confirm the actual message in the private channel. Record workspace/channel reference, safe message ts, run ID and evidence under access controls.
6. Repeat at least ten real E2E deliveries including success, controlled failure, rate-limit recovery and notification outage. Current count is zero. Do not count fixtures or auth.test as delivered experiment reports.

Significant events only; ACCEPTED creates the thread and later events reply when that parent acknowledgement exists. Global channel pacing at least 1.1s; HTTP429 honors bounded Retry-After, capped to 3600s; up to five automatic attempts for known retryable rejections. Experiment success and notification failure remain independent. Outbox stays durable on the same volume and resumes when worker restarts/config is provided.

Timeout/connection loss/5xx can mean Slack accepted a message but acknowledgement was lost. The outbox records UNKNOWN and stops automatic retries. Operator must reconcile the channel before deliberately replaying; stable `client_msg_id` helps correlate but is not advertised as an exactly-once guarantee. A failed terminal notification can be requeued with `floor_resend_experiment_report`; no experiment reruns. A DELIVERED message is not duplicated by that tool. No automatic fallback to unapproved recipients or public channels.

Reports are local/protected references until an approved artifact host exists. No invalid public link is fabricated. Publishing a durable secure artifact URL is part of remote staging acceptance. No Slack file-upload API is used; if uploads are later added, use `files.getUploadURLExternal` + `files.completeUploadExternal`, not obsolete `files.upload`.

Official sources checked 2026-11-13: https://docs.slack.dev/reference/methods/chat.postMessage/ ; https://docs.slack.dev/apis/web-api/rate-limits ; https://docs.slack.dev/reference/methods/auth.test/ ; https://docs.slack.dev/reference/methods/files.getUploadURLExternal/ .

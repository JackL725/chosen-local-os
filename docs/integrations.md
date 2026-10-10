# Integrations

Claude records here what it confirmed in each service's current API docs: endpoints, webhook
events and payload fields, auth method, rate limits, with links and the date checked.

| Service | What we use | Confirmed facts | Doc link | Checked |
| --- | --- | --- | --- | --- |
| Close | leads, calls, recordings, texts, webhooks | | https://developer.close.com/ | |
| Cal.com | bookings, webhooks | | | |
| Slack | incoming webhook, interactive approvals | | | |
| QuickBooks | invoice drafts | | | |
| Meta | Conversions API, ad insights | | | |
| TikTok | ad insights | | | |

## Model choices

| Job | Chosen | Score on eval | Date |
| --- | --- | --- | --- |
| Language model | Qwen3.6 27B Q4_K_M (baseline) | | |
| Transcription | | | |
| Diarization | pyannote speaker-diarization-community-1 | | |

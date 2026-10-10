# Message templates

Founders write these. One file per template: `templates/<id>.v<N>.md`.

```
---
id: booking-link-sms
version: 1
channel: sms            # sms | email | slack
audience: prospect      # prospect | coach
placeholders: [first_name, booking_url]
---
Hi {first_name}, thanks for reaching out to Chosen Local. Pick a time to talk here: {booking_url}
```

A template is sendable only after a founder approves that version in the Ledger.

# Alert interactivity (workaround)

Discord failure alerts are sent as **text messages** with links and instructions. Buttons (e.g. "Open Dashboard", "Acknowledge", "Pause", "Retry Ticket") are not implemented in the message payload.

## What to do instead

Use **slash commands** or **prefix commands** in the same channel:

| Action | Command |
|--------|--------|
| Open Dashboard | Click the `http://localhost:8765/runs/<run_id>` link in the alert (or open in browser). |
| Acknowledge | Optional: reply in thread or run `/run <run_id>` to confirm you’ve seen it. |
| Pause new work | `/pause` or prefix `/pause` |
| Retry ticket | `/ticket retry <ticket_id>` or prefix `/ticket retry TKT-000001` |

If you later add Discord message components (buttons), you can attach them to the same channel send in `_send_monitoring_alert()` and handle interactions via `on_interaction` (e.g. button custom_id `ack`, `pause`, `retry:<ticket_id>`).

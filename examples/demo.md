# Radar — offline walkthrough

Version 0.34.0

All messages, organisations and decisions below are invented. Review is scripted; this does not measure AI selection quality or exercise the live Twenty UI.

## Input messages

### Message 1

📌 Project A — A small repair shop needs incoming calls routed to the right team. A paid two-week automation pilot; call volume and budget still need confirmation.

Project B — A local studio wants appointment reminders by phone. Looking for a contractor to prototype an opt-in voice workflow; schedule is not specified.

### Message 2

Anyone around for lunch today?

## Reviewed cards

### Incoming-call routing pilot

**Exact source block**

> 📌 Project A — A small repair shop needs incoming calls routed to the right team. A paid two-week automation pilot; call volume and budget still need confirmation.

**Unknown:** budget, call volume, author confirmation.

**Scripted decision:** APPROVE

### Appointment reminder prototype

**Exact source block**

> Project B — A local studio wants appointment reminders by phone. Looking for a contractor to prototype an opt-in voice workflow; schedule is not specified.

**Unknown:** budget, call volume, author confirmation.

**Scripted decision:** REJECT

## Verified behaviour

- Both input messages receive an explicit review result.
- Two non-overlapping source positions produce two cards with stable IDs.
- A retry creates no duplicates and preserves both previous decisions.
- Nothing is collected from Telegram or written to Twenty.

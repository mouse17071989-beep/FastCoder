# Telegram bot + Access control

## Main bot
- Requires channel subscription (`REQUIRED_CHANNEL`) or paid access flag in DB.
- Paid flow is scaffolded (for future stars payment), currently managed via admin bot.

## Setup
1. Fill `.env` from `.env.example`.
2. Run main bot:
```bash
python bot.py
```
3. Run mini app server (public HTTPS required for Telegram WebApp):
```bash
uvicorn mini_app:app --host 0.0.0.0 --port 8000
```
4. Run admin bot (separate token):
```bash
python admin_bot.py
```

## Admin bot commands
- `/stats`
- `/grant <user_id> <days>`
- `/revoke <user_id>`
- `/user <user_id>`
- `/recent`

## Notes
- To check channel membership reliably, add the main bot as admin in your channel.
- DB file path is `ACCESS_DB_PATH` (default `data/access.db`).
- For the WebApp button to work, set `MINI_APP_URL` to your public HTTPS URL.

# AI Live Commerce Copilot v2 Deployment

## Recommended: Render

This repo includes `render.yaml`, so Render can read the web service configuration automatically.

1. Push this project to GitHub.
2. Go to [Render](https://render.com).
3. Create **New > Blueprint** or **New > Web Service** from your GitHub repo.
4. Use these commands if Render asks:

```bash
pip install -r requirements.txt
```

```bash
uvicorn main:app --host 0.0.0.0 --port $PORT
```

5. Set environment variables in Render:

```env
OPENAI_API_KEY=your-openai-key
SERPAPI_API_KEY=your-serpapi-key
APP_PASSWORD=your-shared-login-password
APP_SECRET=long-random-secret
LIVE_INGEST_TOKEN=long-random-ingest-token
SERPAPI_CACHE_TTL_SECONDS=86400
LATEST_EXTENSION_VERSION=0.1.3
```

`LIVE_INGEST_TOKEN` is **required in production**. Without it, `/api/live-ingest` accepts unauthenticated POST requests and fake live metrics can pollute director decisions and training data. After deploy, open the Chrome extension popup and enter the same token in **Ingest Token**.

6. Deploy. Render will give you a public HTTPS URL. Anyone with the URL and `APP_PASSWORD` can log in.

Render's official FastAPI docs use the same Uvicorn start pattern: `uvicorn main:app --host 0.0.0.0 --port $PORT`.

## Local network sharing

Run the app on all network interfaces:

```bash
.venv/bin/uvicorn main:app --reload --host 0.0.0.0 --port 8001
```

Find your Mac LAN IP:

```bash
ipconfig getifaddr en0
```

People on the same Wi-Fi can open:

```text
http://YOUR_LAN_IP:8001
```

## Password protection

Set these in `.env` before sharing the link:

```env
APP_PASSWORD=choose-a-password
APP_SECRET=choose-a-long-random-secret
OPENAI_API_KEY=your-openai-key
SERPAPI_API_KEY=your-serpapi-key
```

If `APP_PASSWORD` is empty, the app runs without login for local development.

## Cloud deployment

Use this start command:

```bash
uvicorn main:app --host 0.0.0.0 --port $PORT
```

Configure environment variables in the cloud dashboard:

```env
APP_PASSWORD=
APP_SECRET=
LIVE_INGEST_TOKEN=
OPENAI_API_KEY=
SERPAPI_API_KEY=
```

Keep real keys in environment variables only. Do not paste them into source files or frontend code.

For any public deployment, set a strong `LIVE_INGEST_TOKEN` and configure the same value in the Chrome extension popup before going live.

## Current production notes

- The app is suitable for MVP/internal/customer pilot usage.
- It does not store user uploads permanently.
- It does not yet have per-user accounts or database-backed workspaces.
- For broader external usage, add HTTPS, persistent user accounts, request logging, and rate limits.

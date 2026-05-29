# 30-Minute Real Live Trial Checklist

Goal: prove the closed loop works end to end:

Real Taobao live data -> AI Director decision -> host execution -> training sample -> model readiness.

## Before Going Live

1. Confirm Render environment variables:
   - `APP_PASSWORD`
   - `APP_SECRET`
   - `LIVE_INGEST_TOKEN`
   - `APP_COOKIE_SECURE=true`
   - `MODEL_V0_MIN_CONFIDENCE=0.4`

2. Install or reload the Chrome extension:
   - Download from `/download/chrome-extension`
   - Unzip the package
   - Open `chrome://extensions`
   - Enable Developer Mode
   - Click `Load unpacked`
   - Select the unzipped extension folder

3. Configure the extension popup:
   - API address: cloud app base URL, for example `https://ai-live-commerce-copilot.onrender.com`
   - Ingest Token: same value as `LIVE_INGEST_TOKEN`
   - Workspace ID: store or boss binding code

4. Open these pages:
   - Host page: `/live?workspace_id=...&host_id=...`
   - Boss page: `/boss`
   - Model page: `/admin/model`
   - Taobao live backend realtime data page

## During The 30-Minute Trial

Every 5 minutes, confirm:

- Extension popup shows:
  - `Content script = yes`
  - `Page hook = yes`
  - `Target API captured = yes`
  - `Payload parsed = yes`
  - `Sent to app = yes`

- Host page shows:
  - Payload source is extension
  - Current viewers / heat / CTR / CVR are not stale
  - AI Director action updates
  - Action code is visible

- Host workflow:
  - Host follows the current action
  - Host clicks `我已照做` after executing
  - Host does not manually paste payload unless extension fails

## Pass Criteria

The trial passes when:

- `/api/live/sessions` shows the active room
- `/api/live/history?host_id=...` shows snapshots and actions
- `/api/model/training-dashboard` sample count increases
- `/api/model/readiness` shows updated dashboard data
- `/api/model/offline-evaluation` returns evaluated samples when enough quality samples exist
- `/api/model/train-v0` either trains successfully or returns `not_enough_data` with an expected sample count

## Failure Triage

If extension data is not arriving:

1. Check the extension popup error.
2. Confirm API address has no trailing path, only the base URL.
3. Confirm Ingest Token matches Render.
4. Confirm Taobao backend page is open and refreshing realtime data.
5. Confirm Render has `LIVE_INGEST_TOKEN`; production rejects ingest without it.

If samples are low quality:

1. Confirm the host clicks `我已照做` soon after executing.
2. Avoid switching product during the 30-second effect window.
3. Confirm product name is present.
4. Confirm product-level metrics are connected before trusting CTR/CVR/GMV effect scores.

## After The Trial

Review:

- `/admin/model`
- `/admin/live`
- `/boss`
- `/api/model/training-dashboard`
- `/api/model/offline-evaluation`

Record:

- Total snapshots
- Total host feedback actions
- High-quality samples
- Rejected samples and reasons
- Best action codes
- Any extension errors

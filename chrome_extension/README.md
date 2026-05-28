# AI Live Director Taobao Connector

This Chrome Extension captures only the response body for:

`mtop.taobao.tblive.portal.live.user.assistant.data.get`

It does not collect cookies, request headers, or authentication data.

## Cloud setup for hosts

The extension sends live metrics to the Render app first:

`POST https://ai-live-commerce-copilot.onrender.com/api/live-ingest`

If cloud posting fails, it falls back to local development endpoints:

- `POST http://localhost:8000/api/live-ingest`
- `POST http://127.0.0.1:8000/api/live-ingest`

Host workflow:

1. Install this folder with Chrome `Load unpacked`.
2. Open the cloud app and log in:
   `https://ai-live-commerce-copilot.onrender.com`
3. Open the Taobao live backend realtime data page while logged in.
4. Click the extension icon and confirm:
   `Content script = yes`, `Page hook = yes`, `Target API captured = yes`, `Sent to app = yes`.

## Local setup

1. Run the FastAPI app locally on port `8000`:

   ```bash
   .venv/bin/uvicorn main:app --reload --port 8000
   ```

2. Open Chrome Extensions:

   `chrome://extensions`

3. Enable Developer Mode.

4. Click "Load unpacked".

5. Select this folder:

   `chrome_extension`

6. Open the Taobao live backend page while logged in.

The extension will send normalized live metrics every 5 seconds to cloud first, then local fallback:

`POST https://ai-live-commerce-copilot.onrender.com/api/live-ingest`

Request body:

```json
{
  "source": "chrome_extension",
  "liveId": "...",
  "timestamp": "...",
  "metrics": {},
  "events": []
}
```

## Normalized fields

- `online_uv`
- `pv`
- `uv`
- `stay_time_pu`
- `pay_byr_rate`
- `pay_buyer_cnt`
- `pay_item_qty`
- `pay_amt`
- `heat_score`
- `ipv_uv_rate`
- `comment_uv`
- `refund_amt`
- `atn_uv`
- `dataRegion`
- `interactSecKill`

The parser maps metrics only by exact `valueType`. For example, `uv` becomes total viewers, `online_uv` becomes current concurrent online users, and fields such as `look_uv_not_fans_rate` are ignored.

## Debug panel

Click the extension icon to see the connector status:

- `Content script`: whether the extension injected into `liveplatform.taobao.com`
- `Page hook`: whether response interception was installed
- `Target API captured`: whether the target mtop API returned
- `Payload parsed`: whether JSON/JSONP parsing succeeded
- `Sent to app`: whether `POST /api/live-ingest` succeeded
- `Last captured` / `Last sent`: timestamps for the latest successful steps

If `Target API captured` is `no`, refresh or enter the Taobao live data console. If `Sent to app` is `no`, make sure the local FastAPI app is running on port `8000`.

Product-level metrics are prepared in the backend schema. If Taobao does not return them yet, the Live Director will show:

`Product-level metrics not connected yet.`

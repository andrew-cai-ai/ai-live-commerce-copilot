# AI Live Director Taobao Connector

This Chrome Extension captures only the response body for:

`mtop.taobao.tblive.portal.live.user.assistant.data.get`

It does not collect cookies, request headers, or authentication data.

## Cloud setup for hosts

By default, the extension sends live metrics to local development first:

- `POST http://localhost:8000/api/live-ingest`
- `POST http://127.0.0.1:8000/api/live-ingest`

For a host using the cloud app, enter the cloud base URL in the extension popup:

```text
https://ai-live-commerce-copilot.onrender.com
```

After that, the extension sends to:

`POST https://ai-live-commerce-copilot.onrender.com/api/live-ingest`

If the backend sets `LIVE_INGEST_TOKEN`, enter the same token in the extension popup. The connector sends it as `X-Live-Ingest-Token`.

Host workflow:

1. Install this folder with Chrome `Load unpacked`.
2. Open the cloud app and log in:
   `https://ai-live-commerce-copilot.onrender.com`
3. Click the extension icon and set:
   - API 地址: `https://ai-live-commerce-copilot.onrender.com`
   - Ingest Token: the same value as `LIVE_INGEST_TOKEN`
4. Open the Taobao live backend realtime data page while logged in. The connector supports both:
   - `liveplatform.taobao.com` live control pages
   - `market.m.taobao.com/app/mtb/live-professional-screen/...` professional screen pages
5. Click the extension icon and confirm:
   `Content script = yes`, `Page hook = yes`, `Target API captured = yes` 或 `DOM fallback = yes`, `Sent to app = yes`.

If `Target API captured = no` and `DOM fallback = yes`, Taobao did not expose the full `mtop...assistant.data.get` response on that page. The app will use visible room data such as成交额、在线、进入、点击, but heat / CTR / CVR / watch duration still require the real `totalStats` / `dataRegion` API.

If `Endpoint` shows `localhost` even after setting the cloud API address, the cloud request failed and the connector fell back to local. Check `Endpoint warning`, then verify the cloud API address and `Ingest Token`.

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

The extension will send normalized live metrics every 5 seconds to local first unless API 地址 is configured.

`POST http://localhost:8000/api/live-ingest`

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

- `Content script`: whether the extension injected into the Taobao live control or professional screen page
- `Page hook`: whether response interception was installed
- `Target API captured`: whether the target mtop API returned
- `DOM fallback`: whether visible dashboard numbers were read from the page when Taobao did not expose the target API
- `Payload parsed`: whether JSON/JSONP parsing succeeded
- `Sent to app`: whether `POST /api/live-ingest` succeeded
- `Last captured` / `Last sent`: timestamps for the latest successful steps

If both `Target API captured` and `DOM fallback` are `no`, refresh or enter the Taobao live data console/professional screen. If `Sent to app` is `no`, make sure the local FastAPI app is running on port `8000` or the cloud API address/token is set correctly.

Product-level metrics are prepared in the backend schema. If Taobao does not return them yet, the Live Director will show:

`Product-level metrics not connected yet.`

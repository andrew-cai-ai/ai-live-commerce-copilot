# AI Live Director Taobao Connector

This Chrome Extension captures only the response body for:

`mtop.taobao.tblive.portal.live.user.assistant.data.get`

It does not collect cookies, request headers, or authentication data.

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

The extension will send normalized live metrics every 5 seconds to:

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

Product-level metrics are prepared in the backend schema. If Taobao does not return them yet, the Live Director will show:

`Product-level metrics not connected yet.`

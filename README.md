# 直播选品助手

FastAPI V1 app for ranking Arc'teryx inventory and generating a Chinese livestream product selection report.

## PyCharm Setup

1. Open this folder in PyCharm.
2. Create a Python virtual environment.
3. Install dependencies:

```bash
pip install -r requirements.txt
```

4. Create `.env` from `.env.example`, then set keys:

```bash
cp .env.example .env
```

`.env`:

```text
OPENAI_API_KEY=
SERPAPI_API_KEY=
SERPAPI_CACHE_TTL_SECONDS=86400
APP_PASSWORD=
APP_SECRET=
```

`.env` is ignored by git. Keep real API keys in `.env`, never in source code.

`APP_PASSWORD` enables a simple login page for sharing the app with teammates or pilot customers. Leave it empty for local development without login.

5. Run the app:

```bash
uvicorn main:app --reload
```

Then open `http://127.0.0.1:8000`.

To let people on the same Wi-Fi access it, run:

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8001
```

Then share `http://YOUR_LAN_IP:8001`. See `DEPLOYMENT.md` for password protection and cloud deployment notes.

For external users, deploy to Render. This repo includes `render.yaml`; push to GitHub, create a Render Blueprint/Web Service, then set `OPENAI_API_KEY`, `SERPAPI_API_KEY`, `APP_PASSWORD`, and `APP_SECRET` in Render's environment settings.

## History, Export, and Cache

Every generated report is saved under `data/reports` and can be viewed at:

```text
/reports
```

Each report can be downloaded as a standalone HTML file.

SerpAPI responses are cached under `.cache/serpapi` for 24 hours by default. Change the cache TTL with:

```env
SERPAPI_CACHE_TTL_SECONDS=86400
```

## Paste Format

Paste one product per line:

```text
Alpha SV Jacket, 520, 8, 899
Beta AR Jacket, 390, 15, 649
Atom Hoody, 180, 24, 300
```

CSV, tab-separated, or pipe-separated rows are supported.

## Smart Excel / CSV Import

The upload flow supports `.xlsx` and `.csv` files. Click **预览 Excel/CSV** first to inspect normalized rows, then click **生成选品报告** after confirming.

Only `product_name` is required. Other fields may be blank.

Column headers are mapped by keywords:

- `商品名 / 品名 / product / title / name` -> `product_name`
- `SKU / 货号 / 编码 / 款号` -> `sku`
- `颜色 / color` -> `color`
- `成本 / 进价 / 供货价 / cost` -> `cost_price`
- `库存 / 数量 / stock / inventory` -> `inventory`
- `售价 / 目标售价 / 建议售价 / price` -> `target_price`
- `备注 / 卖点 / 活动 / tag / notes` -> `notes`

Extra columns and empty rows are ignored. If headers are messy, the importer uses content heuristics to guess product and numeric columns.

## Manual Research Override

The optional manual research box lets you add field research before Taobao, Dewu, Douyin, and Xiaohongshu APIs are integrated.

Recommended format:

```text
Product name | Dewu price | Taobao average price | Douyin heat notes | Xiaohongshu review notes | Competitor seller price
Alpha SV Jacket | 910 | 880 | 抖音：旗舰硬壳热度高；评论常问防水和真假 | 小红书：适合硬核户外穿搭；担心尺码偏大 | 860
```

Manual data is merged with provider data and receives higher confidence in ranking and report generation.

## Ranking Formula

V1 prioritizes livestream conversion potential over pure margin:

```text
score =
popularity_score * 0.25
+ profit_margin * 0.15
+ inventory_priority * 0.10
+ price_gap_score * 0.15
+ sellability_score * 0.35
```

`sellability_score` combines daily wear suitability, mainstream popularity, conversion difficulty, price accessibility, and gifting potential.

Google Shopping results are filtered before market price calculation:

- Reject noisy titles containing `Silver Label`, `Used`, `Outlet`, `Women's`, `Kids`, `Geartrade`, or `Resale`
- Reject results whose fuzzy title similarity to the input product is `<= 0.8`
- Remove price outliers below `median * 0.6` or above `median * 1.4`
- For Atom Hoody, reject noisy variants such as `SL`, `LT`, `Heavyweight`, `LEAF`, `Women's`, `XS`, and `Purple`

GMV potential:

```text
GMV score =
traffic_score * 0.4
+ conversion_score * 0.4
+ profit_score * 0.2
```

The report also recommends a livestream product order:

- Start product: easiest conversion
- Second product: highest traffic potential
- Third product: highest profit
- Final product: premium closer

## Live Mode

The report includes a mock livestream simulator that updates every 5 seconds:

- Current viewers
- CTR
- CVR
- Add-to-cart rate
- Average watch duration

It highlights one host action:

- Continue selling
- Switch product
- Explain sizing
- Show authenticity proof

Realtime suggestions include prompts such as `很多人问尺码`, `放近一点拍吊牌`, `开始讲通勤场景`, and `切换到下一件`.

Host Assistant Mode adds a floating right-side assistant panel showing:

- Current product
- Current viewers
- CTR
- CVR
- Watch duration
- GMV score

It refreshes every 5 seconds and suggests actions such as `切下一件`, `解释价格`, `展示吊牌和洗标`, `开始讲尺码`, and `继续讲90秒`, plus a next-sentence prompt for the host.

Current realtime rules:

- `CTR > 7%` and `CVR < 2%`: Explain value and pricing
- `watch_duration < 30s`: Switch product
- `authenticity_questions > 3`: Show tag and details
- `sizing_questions > 3`: Explain sizing
- `add_to_cart_rate > 5%`: Create urgency

Livestream order follows host decision priority: `Push hard` > `Mention briefly` > `Skip for today`, so Skip products stay late.

The report includes a Post-Live Analysis section with total viewers, estimated GMV, best product, worst product, reasons, and next livestream suggestions.

Final MVP polish:

- Product-specific script templates:
  - Atom: commuting and daily wear
  - Beta: weather protection
  - Cerium: warmth
  - Alpha: professional outdoor
- Audience Question Assistant:
  - Input viewer comments
  - Output short host reply, confidence, and suggested action
- Live Comment Input:
  - Paste live viewer comments during the session
  - Updates every 5 seconds
  - Generates reply, confidence, and next action
- Engagement suggestions:
  - `评论区扣1`
  - `想看上身扣2`
  - `175/70打身高`
  - `想看黑色扣3`

## Price Providers

V1 uses Google Shopping via SerpAPI as the first real working price provider.

Real provider:

- `app/providers/google_shopping_provider.py`

Placeholders only, no direct scraping yet:

- `app/providers/taobao_provider.py`
- `app/providers/dewu_provider.py`
- `app/providers/douyin_provider.py`
- `app/providers/xiaohongshu_provider.py`

Core service:

- `app/services/market_research.py`

If `SERPAPI_API_KEY` is missing or Google Shopping fails, the app continues with local mock prices and displays a warning in the report.

Provider dataclasses live in `app/providers/base.py`:

- `PriceResult`
- `SocialSignal`
- `MarketResearchResult`

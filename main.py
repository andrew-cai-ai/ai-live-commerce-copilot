from __future__ import annotations

import html
import io
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any
import zipfile

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from report import generate_product_reports, render_report_page
from app.services.audience_questions import answer_audience_questions
from app.services.auth import clear_auth_cookie, is_authenticated, password_matches, set_auth_cookie
from app.services.inventory_import import (
    SmartInventoryRow,
    build_inventory_items,
    parse_smart_inventory_file,
    smart_rows_to_inventory_text,
)
from app.services.live_data_connector import LiveDataConnector
from app.services.report_history import get_report_path, list_reports, save_report
from app.services.taobao_live_scoring import TaobaoLiveScoringService
from scoring import parse_manual_research_overrides, score_products

app = FastAPI(title="直播选品助手")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)
live_data_connector = LiveDataConnector()
BASE_DIR = Path(__file__).resolve().parent
CHROME_EXTENSION_DIR = BASE_DIR / "chrome_extension"

EXAMPLE_INVENTORY = ""

EXAMPLE_MANUAL_RESEARCH = ""

EXAMPLE_VIEWER_COMMENTS = """175 70kg穿啥
冬天够暖吗
真的假的
值得买吗"""

EXAMPLE_INVENTORY_PLACEHOLDER = """商品名, 成本, 库存, 目标售价, 可选成本币种
真实商品标题, CAD成本, 可售库存, CNY建议直播价, CAD"""

EXAMPLE_TAOBAO_JSON = """{
  "data": {
    "dataSource": [
      {
        "sourceProductTitle": "真实商品标题",
        "sourceProductImage": "真实图片URL",
        "price": 0,
        "adviseSalePriceHigh": 0,
        "adviseSalePriceLow": 0,
        "skuNumber": 0,
        "targetProductStatus": 1,
        "catName": "真实类目",
        "sourceProductId": "真实商品ID"
      }
    ]
  }
}"""


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> str:
    if not is_authenticated(request):
        return _render_login_form()
    return _render_form()


@app.get("/reports", response_class=HTMLResponse)
def reports_index(request: Request) -> str:
    if not is_authenticated(request):
        return _render_login_form()
    return _render_reports_index()


@app.get("/reports/{report_id}", response_class=HTMLResponse)
def view_report(request: Request, report_id: str):
    if not is_authenticated(request):
        return HTMLResponse(_render_login_form(), status_code=401)
    path = get_report_path(report_id)
    if path is None:
        return HTMLResponse("Report not found", status_code=404)
    return HTMLResponse(path.read_text(encoding="utf-8"))


@app.get("/reports/{report_id}/export")
def export_report(request: Request, report_id: str):
    if not is_authenticated(request):
        return HTMLResponse(_render_login_form(), status_code=401)
    path = get_report_path(report_id)
    if path is None:
        return HTMLResponse("Report not found", status_code=404)
    return FileResponse(
        path,
        media_type="text/html",
        filename=f"live-commerce-report-{report_id}.html",
    )


@app.get("/live", response_class=HTMLResponse)
def live_console(request: Request) -> str:
    if not is_authenticated(request):
        return _render_login_form()
    return _render_live_console()


@app.get("/install", response_class=HTMLResponse)
def install_guide(request: Request) -> str:
    if not is_authenticated(request):
        return _render_login_form()
    return _render_install_guide()


@app.post("/login")
def login(password: str = Form("")):
    if not password_matches(password):
        return HTMLResponse(_render_login_form(error="密码不正确。"), status_code=401)
    response = RedirectResponse("/", status_code=303)
    set_auth_cookie(response)
    return response


@app.post("/logout")
def logout() -> RedirectResponse:
    response = RedirectResponse("/", status_code=303)
    clear_auth_cookie(response)
    return response


@app.post("/api/live/decision")
async def live_decision(request: Request) -> dict[str, Any]:
    if not is_authenticated(request):
        return {"error": "unauthorized"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    decision = live_data_connector.get_decision(
        payload=body.get("payload"),
        products=body.get("products"),
        host_id=body.get("host_id") or body.get("hostId") or body.get("liveId"),
    )
    return asdict(decision)


@app.get("/api/live/sessions")
async def live_sessions(request: Request) -> dict[str, Any]:
    if not is_authenticated(request):
        return {"error": "unauthorized"}
    return {"sessions": live_data_connector.active_sessions()}


@app.post("/live-metrics")
async def live_metrics(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    decision = live_data_connector.ingest_live_metrics(payload)
    return {
        "ok": True,
        "source": "chrome_extension",
        "valid_live_metrics": decision.valid_live_metrics,
        "current_action": decision.current_action,
        "livestream_mode": decision.livestream_mode,
        "host_id": decision.host_id,
        "snapshot_count": len(live_data_connector.snapshots),
    }


@app.post("/api/live-ingest")
async def live_ingest(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    decision = live_data_connector.ingest_live_metrics(payload)
    return {
        "ok": True,
        "source": "chrome_extension",
        "valid_live_metrics": decision.valid_live_metrics,
        "current_action": decision.current_action,
        "livestream_mode": decision.livestream_mode,
        "host_id": decision.host_id,
        "snapshot_count": len(live_data_connector.snapshots),
        "last_updated": decision.snapshot.timestamp,
    }


@app.get("/download/chrome-extension")
async def download_chrome_extension(request: Request) -> Response:
    if not is_authenticated(request):
        return RedirectResponse("/", status_code=303)
    buffer = io.BytesIO()
    allowed_suffixes = {".html", ".js", ".json", ".md", ".css", ".png", ".svg"}
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in CHROME_EXTENSION_DIR.rglob("*"):
            if not path.is_file():
                continue
            if path.name.startswith(".") or path.suffix.lower() not in allowed_suffixes:
                continue
            archive.write(path, path.relative_to(CHROME_EXTENSION_DIR).as_posix())
    buffer.seek(0)
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="ai-live-copilot-chrome-extension.zip"'},
    )


@app.post("/analyze", response_class=HTMLResponse)
async def analyze(
    request: Request,
    inventory_text: str = Form(""),
    manual_research_text: str = Form(""),
    viewer_comments_text: str = Form(""),
    taobao_json_text: str = Form(""),
    inventory_excel: UploadFile | None = File(None),
) -> str:
    if not is_authenticated(request):
        return _render_login_form(error="请先登录。")
    try:
        excel_bytes = await inventory_excel.read() if inventory_excel and inventory_excel.filename else None
        items = build_inventory_items(
            inventory_text=inventory_text,
            taobao_json_text=taobao_json_text,
            excel_bytes=excel_bytes,
            excel_filename=inventory_excel.filename if inventory_excel else "",
        )
        manual_overrides = parse_manual_research_overrides(manual_research_text)
        products = score_products(items, manual_overrides=manual_overrides)
        reports = generate_product_reports(products)
        audience_answers = answer_audience_questions(viewer_comments_text)
        taobao_report = TaobaoLiveScoringService().build_report(pasted_json=taobao_json_text)
        report_html = render_report_page(
            products,
            reports,
            audience_answers=audience_answers,
            taobao_report=taobao_report,
        )
        record = save_report(report_html, [product.product_name for product in products])
        return _inject_report_history_banner(report_html, record.report_id)
    except ValueError as exc:
        return _render_form(
            error=str(exc),
            inventory_text=inventory_text,
            manual_research_text=manual_research_text,
            viewer_comments_text=viewer_comments_text,
            taobao_json_text=taobao_json_text,
        )


@app.post("/preview_inventory", response_class=HTMLResponse)
async def preview_inventory(
    request: Request,
    inventory_text: str = Form(""),
    manual_research_text: str = Form(""),
    viewer_comments_text: str = Form(""),
    taobao_json_text: str = Form(""),
    inventory_excel: UploadFile | None = File(None),
) -> str:
    if not is_authenticated(request):
        return _render_login_form(error="请先登录。")
    try:
        if not inventory_excel or not inventory_excel.filename:
            raise ValueError("请先选择一个 .xlsx 或 .csv 文件。")
        excel_bytes = await inventory_excel.read()
        preview_rows = parse_smart_inventory_file(excel_bytes, inventory_excel.filename)
        if not preview_rows:
            raise ValueError("没有识别到有效商品行。")
        return _render_form(
            inventory_text=smart_rows_to_inventory_text(preview_rows),
            manual_research_text=manual_research_text,
            viewer_comments_text=viewer_comments_text,
            taobao_json_text=taobao_json_text,
            preview_rows=preview_rows,
            preview_message=f"已识别 {len(preview_rows)} 个商品。确认无误后点击生成选品报告。",
        )
    except ValueError as exc:
        return _render_form(
            error=str(exc),
            inventory_text=inventory_text,
            manual_research_text=manual_research_text,
            viewer_comments_text=viewer_comments_text,
            taobao_json_text=taobao_json_text,
        )


def _render_form(
    error: str | None = None,
    inventory_text: str = EXAMPLE_INVENTORY,
    manual_research_text: str = EXAMPLE_MANUAL_RESEARCH,
    viewer_comments_text: str = EXAMPLE_VIEWER_COMMENTS,
    taobao_json_text: str = "",
    preview_rows: list[SmartInventoryRow] | None = None,
    preview_message: str = "",
) -> str:
    error_html = f'<div class="error">{html.escape(error)}</div>' if error else ""
    preview_html = _render_inventory_preview(preview_rows or [], preview_message)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>始祖鸟直播选品助手</title>
  <style>
    :root {{
      --ink: #16211f;
      --muted: #5b6764;
      --line: #d6dfdb;
      --paper: #f7f9f6;
      --panel: #ffffff;
      --accent: #0c6b58;
      --danger: #9f2f22;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--ink);
      background: var(--paper);
    }}
    main {{
      width: min(980px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 48px 0;
    }}
    h1 {{
      margin: 0;
      font-size: clamp(32px, 5vw, 58px);
      line-height: 1.02;
      letter-spacing: 0;
      max-width: 850px;
    }}
    p {{
      color: var(--muted);
      font-size: 18px;
      max-width: 720px;
    }}
    form {{
      margin-top: 28px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 22px;
      box-shadow: 0 18px 45px rgba(22, 33, 31, .06);
    }}
    label {{
      display: block;
      font-weight: 800;
      margin-bottom: 10px;
    }}
    textarea {{
      width: 100%;
      min-height: 280px;
      resize: vertical;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      color: var(--ink);
      font: 15px/1.55 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      background: #fbfdfb;
    }}
    .manual textarea {{
      min-height: 190px;
    }}
    .comments textarea {{
      min-height: 130px;
    }}
    .taobao textarea {{
      min-height: 240px;
    }}
    textarea:focus {{
      outline: 3px solid rgba(12, 107, 88, .16);
      border-color: var(--accent);
    }}
    input[type="file"] {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      background: #fbfdfb;
    }}
    button {{
      margin-top: 16px;
      border: 0;
      border-radius: 8px;
      background: var(--accent);
      color: white;
      font-weight: 900;
      font-size: 16px;
      padding: 12px 16px;
      cursor: pointer;
    }}
    button:hover {{ filter: brightness(.95); }}
    .hint {{
      margin-top: 10px;
      color: var(--muted);
      font-size: 14px;
    }}
    .error {{
      margin: 16px 0 0;
      padding: 12px 14px;
      border: 1px solid rgba(159, 47, 34, .3);
      background: rgba(159, 47, 34, .08);
      color: var(--danger);
      border-radius: 8px;
      font-weight: 700;
    }}
    .preview {{
      margin-top: 18px;
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow-x: auto;
      background: #fff;
    }}
    .preview table {{
      width: 100%;
      min-width: 900px;
      border-collapse: collapse;
    }}
    .preview th, .preview td {{
      padding: 9px 10px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
      font-size: 13px;
    }}
    .preview th {{
      color: var(--muted);
      font-weight: 900;
    }}
    .notice {{
      margin-top: 16px;
      padding: 12px 14px;
      border: 1px solid rgba(12, 107, 88, .22);
      background: rgba(12, 107, 88, .08);
      color: var(--accent);
      border-radius: 8px;
      font-weight: 800;
    }}
  </style>
</head>
<body>
  <main>
    <h1>始祖鸟直播选品助手</h1>
    <p>上传库存 Excel 或粘贴淘宝商品 JSON，系统会结合真实价格证据、真实社媒证据和 AI 推断，生成适合主播现场使用的选品与话术工作台。</p>
    <form method="post" action="/logout" style="margin-top: 14px; padding: 0; border: 0; box-shadow: none; background: transparent;">
      <button type="submit" style="margin-top: 0; background: #5b6764;">退出登录</button>
    </form>
    <p><a href="/live">打开主播控制台</a> · <a href="/install">插件安装教程</a> · <a href="/reports">查看历史报告 / 导出 HTML</a> · <a href="/download/chrome-extension">下载 Chrome 插件包</a></p>
    {error_html}
    <form method="post" action="/analyze" enctype="multipart/form-data">
      <label for="inventory_text">库存商品</label>
        <textarea id="inventory_text" name="inventory_text" spellcheck="false" placeholder="{html.escape(EXAMPLE_INVENTORY_PLACEHOLDER)}">{html.escape(inventory_text)}</textarea>
      <div class="hint">可选格式：商品名, 成本, 库存, 目标售价, 可选成本币种。成本默认按 CAD；目标售价默认按 CNY。支持 CNY/CAD/USD，系统会统一折算到 CNY 参与利润和排序。</div>
      <div>
        <label for="inventory_excel">Excel upload（可选）</label>
        <input id="inventory_excel" name="inventory_excel" type="file" accept=".xlsx,.csv">
        <div class="hint">支持乱表头自动识别：商品名/品名/product/title/name，SKU/货号/编码/款号，颜色，成本/进价/供货价，成本币种/cost_currency，库存/数量，售价/目标售价/建议售价，备注/卖点/活动。</div>
        <button type="submit" formaction="/preview_inventory">预览 Excel/CSV</button>
      </div>
      {preview_html}
      <div class="manual">
        <label for="manual_research_text">手动调研补充（可选）</label>
        <textarea id="manual_research_text" name="manual_research_text" spellcheck="false">{html.escape(manual_research_text)}</textarea>
        <div class="hint">推荐格式：商品名 | 得物价 | 淘宝均价 | 抖音热度笔记 | 小红书评价笔记 | 竞品卖家价。留空也可以。</div>
      </div>
      <div class="comments">
        <label for="viewer_comments_text">观众问题（可选）</label>
        <textarea id="viewer_comments_text" name="viewer_comments_text" spellcheck="false">{html.escape(viewer_comments_text)}</textarea>
        <div class="hint">每行一个问题，例如：175 70kg穿啥、冬天够暖吗、真的假的、值得买吗。</div>
      </div>
      <div class="taobao">
        <label for="taobao_json_text">Paste Taobao product JSON（可选）</label>
        <textarea id="taobao_json_text" name="taobao_json_text" spellcheck="false" placeholder="{html.escape(EXAMPLE_TAOBAO_JSON)}">{html.escape(taobao_json_text)}</textarea>
        <div class="hint">从 Chrome DevTools 复制接口响应粘贴到这里。系统会读取 data.dataSource，只保留 targetProductStatus = 1 的商品；留空时才尝试可选 API URL 模式。</div>
      </div>
      <button type="submit">生成选品报告</button>
    </form>
  </main>
</body>
</html>"""


def _render_reports_index() -> str:
    records = list_reports()
    rows = "".join(
        f"""
        <tr>
          <td>{html.escape(record.title)}</td>
          <td>{time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.created_at))}</td>
          <td>{record.product_count}</td>
          <td><a href="/reports/{record.report_id}">查看</a></td>
          <td><a href="/reports/{record.report_id}/export">下载 HTML</a></td>
        </tr>"""
        for record in records
    ) or '<tr><td colspan="5">暂无历史报告。</td></tr>'
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>历史报告</title>
  <style>
    body {{ margin: 0; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f7f9f6; color: #16211f; }}
    main {{ width: min(980px, calc(100vw - 32px)); margin: 0 auto; padding: 40px 0; }}
    table {{ width: 100%; border-collapse: collapse; background: white; border: 1px solid #d6dfdb; border-radius: 8px; overflow: hidden; }}
    th, td {{ padding: 12px; border-bottom: 1px solid #d6dfdb; text-align: left; }}
    th {{ color: #5b6764; font-size: 13px; }}
    a {{ color: #0c6b58; font-weight: 800; }}
  </style>
</head>
<body>
  <main>
    <h1>历史报告</h1>
    <p><a href="/">返回工作台</a></p>
    <table>
      <thead>
        <tr>
          <th>报告</th>
          <th>生成时间</th>
          <th>商品数</th>
          <th>查看</th>
          <th>导出</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
  </main>
</body>
</html>"""


def _render_live_console() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>主播实时控制台</title>
  <style>
    :root { color-scheme: light; --ink: #111827; --muted: #64706c; --line: #d9e2de; --paper: #f5f8f6; --panel: #fff; --accent: #0c6b58; --accent-soft: #e1f2eb; --warn: #a16207; --danger: #b42318; }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--paper); color: var(--ink); }
    main { max-width: 1320px; margin: 0 auto; padding: 18px; }
    header { display: flex; justify-content: space-between; gap: 16px; align-items: center; margin-bottom: 14px; }
    h1 { margin: 0; font-size: 24px; }
    a { color: var(--accent); font-weight: 800; text-decoration: none; }
    .status { display: inline-flex; align-items: center; gap: 8px; padding: 8px 12px; border-radius: 999px; background: var(--accent-soft); color: var(--accent); font-weight: 900; }
    .layout { display: grid; grid-template-columns: 1.25fr .85fr; gap: 14px; }
    .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 16px; }
    .hero { border: 2px solid var(--accent); background: #f2f8f5; min-height: 360px; display: grid; align-content: center; gap: 14px; }
    .label { display: block; color: var(--muted); font-size: 13px; font-weight: 900; text-transform: uppercase; letter-spacing: 0; }
    .action { font-size: clamp(34px, 6vw, 72px); line-height: 1.02; color: var(--accent); font-weight: 950; }
    .sentence { font-size: clamp(24px, 4vw, 44px); line-height: 1.18; font-weight: 950; }
    .reason { color: var(--muted); font-size: 18px; font-weight: 800; }
    .cards { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin-top: 14px; }
    .card { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 14px; min-height: 96px; }
    .card b { display: block; font-size: 28px; margin-top: 4px; overflow-wrap: anywhere; }
    .side { display: grid; gap: 14px; align-content: start; }
    .host-input { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; }
    input, textarea { width: 100%; border: 1px solid var(--line); border-radius: 8px; padding: 10px; font: inherit; background: #fbfdfb; }
    button { border: 0; border-radius: 8px; padding: 10px 12px; background: var(--accent); color: #fff; font-weight: 900; cursor: pointer; }
    .rooms { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
    .room { border: 1px solid var(--line); background: #fbfdfb; color: var(--accent); border-radius: 999px; padding: 6px 9px; font-weight: 900; cursor: pointer; }
    .queue { display: grid; gap: 8px; }
    .queue-item { display: grid; grid-template-columns: 60px minmax(0, 1fr); gap: 8px; border: 1px solid var(--line); border-radius: 8px; padding: 10px; background: #fbfdfb; }
    .timeline { display: grid; gap: 8px; max-height: 320px; overflow-y: auto; }
    .timeline-item { border: 1px solid var(--line); border-left: 5px solid var(--accent); border-radius: 8px; padding: 10px; background: #fff; }
    .timeline-item.danger { border-left-color: var(--danger); }
    .timeline-item.warn { border-left-color: var(--warn); }
    .comments { min-height: 90px; }
    .reply { margin-top: 10px; border: 1px solid var(--line); border-radius: 8px; padding: 10px; background: #fbfdfb; }
    .small { color: var(--muted); font-size: 13px; }
    .mode-toggle { display: inline-flex; gap: 6px; padding: 4px; border: 1px solid var(--line); border-radius: 999px; background: #fff; margin-right: 10px; }
    .mode-toggle button { border-radius: 999px; padding: 7px 10px; background: transparent; color: var(--muted); }
    .mode-toggle button.active { background: var(--accent); color: #fff; }
    .checklist { display: grid; gap: 8px; }
    .check { display: grid; grid-template-columns: 22px 1fr; gap: 8px; align-items: center; padding: 8px; border: 1px solid var(--line); border-radius: 8px; background: #fbfdfb; font-weight: 800; }
    .check i { width: 22px; height: 22px; border-radius: 999px; display: grid; place-items: center; background: #e5e7eb; color: var(--muted); font-style: normal; font-size: 12px; }
    .check.done i { background: #d1fae5; color: var(--accent); }
    @media (max-width: 900px) { .layout { grid-template-columns: 1fr; } .cards { grid-template-columns: repeat(2, minmax(0, 1fr)); } .action { font-size: 42px; } .sentence { font-size: 28px; } }
  </style>
</head>
<body>
  <main>
    <header>
      <div><h1>主播实时控制台</h1><div class="small">只看未来 10-30 秒该做什么</div></div>
      <div>
        <span class="mode-toggle"><button id="mode-real" type="button" class="active">真实</button><button id="mode-demo" type="button">演示</button></span>
        <span class="status" id="connection-status">等待插件数据...</span><a href="/" style="margin-left:12px;">返回选品</a>
      </div>
    </header>
    <section class="layout">
      <div>
        <section class="panel hero">
          <span class="label">Current action</span>
          <div class="action" id="current-action">等待数据</div>
          <span class="label">Next sentence</span>
          <div class="sentence" id="next-sentence">打开淘宝直播中控页，并确认插件已捕获数据。</div>
          <div class="reason" id="reason">--</div>
        </section>
        <section class="cards">
          <div class="card"><span class="label">当前直播间</span><b id="host-id-label">default</b></div>
          <div class="card"><span class="label">总观看</span><b id="viewer-count">--</b></div>
          <div class="card"><span class="label">在线</span><b id="online-uv">--</b></div>
          <div class="card"><span class="label">GMV</span><b id="pay-amt">--</b></div>
          <div class="card"><span class="label">热度</span><b id="heat-score">--</b></div>
          <div class="card"><span class="label">CTR</span><b id="ctr">--</b></div>
          <div class="card"><span class="label">CVR</span><b id="cvr">--</b></div>
          <div class="card"><span class="label">停留</span><b id="watch-time">--</b></div>
        </section>
      </div>
      <div class="side">
        <section class="panel">
          <span class="label">连接直播间</span>
          <div class="host-input"><input id="host-id-input" placeholder="default 或 liveId"><button id="save-host">连接</button></div>
          <div class="rooms" id="active-rooms"></div>
          <div class="small" id="last-updated" style="margin-top:8px;">Last updated: --</div>
        </section>
        <section class="panel">
          <span class="label">开播前 Checklist</span>
          <div class="checklist">
            <div class="check" id="check-mode"><i>1</i><span>选择真实/演示模式</span></div>
            <div class="check" id="check-connector"><i>2</i><span>插件或演示数据已连接</span></div>
            <div class="check" id="check-products"><i>3</i><span>商品队列已填写</span></div>
            <div class="check" id="check-comments"><i>4</i><span>评论助手可用</span></div>
            <div class="check" id="check-action"><i>5</i><span>已生成下一步动作</span></div>
          </div>
        </section>
        <section class="panel">
          <span class="label">推荐商品队列</span>
          <textarea class="comments" id="product-list" placeholder="粘贴今天要讲的商品，每行一个&#10;Kragg Shirt&#10;Atom Jacket&#10;Gamma Pant"></textarea>
          <div class="small">会参与“推荐下一件”决策，保存在本机浏览器。</div>
          <div class="queue" id="queue" style="margin-top:10px;"><div class="queue-item"><span>Now</span><b>等待商品池</b></div></div>
        </section>
        <section class="panel">
          <span class="label">观众问题助手</span>
          <textarea class="comments" id="comments" placeholder="粘贴评论，例如：175 70kg穿啥&#10;真的假的&#10;黑色有吗"></textarea>
          <div id="comment-replies"></div>
        </section>
        <section class="panel"><span class="label">AI Director Timeline</span><div class="timeline" id="timeline"><div class="timeline-item">等待实时动作...</div></div></section>
      </div>
    </section>
  </main>
  <script>
    let products = [];
    let liveMode = localStorage.getItem("ai_live_mode") || "real";
    let demoTick = 0;
    function fmtNumber(value) { const numeric = Number(value || 0); return Number.isFinite(numeric) && numeric ? Math.round(numeric).toLocaleString("zh-CN") : "--"; }
    function fmtMoney(value) { const numeric = Number(value || 0); return Number.isFinite(numeric) && numeric ? "¥" + Math.round(numeric).toLocaleString("zh-CN") : "--"; }
    function fmtPercent(value) { const numeric = Number(value || 0); return Number.isFinite(numeric) && numeric ? (numeric * 100).toFixed(1) + "%" : "--"; }
    function escapeHtml(value) { return String(value || "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char])); }
    function hostId() { const input = document.getElementById("host-id-input"); return (input && input.value.trim()) || localStorage.getItem("ai_live_host_id") || "default"; }
    function setHostId(value) { const next = value || "default"; document.getElementById("host-id-input").value = next; document.getElementById("host-id-label").textContent = next; localStorage.setItem("ai_live_host_id", next); }
    function setMode(mode) {
      liveMode = mode === "demo" ? "demo" : "real";
      localStorage.setItem("ai_live_mode", liveMode);
      document.getElementById("mode-real").classList.toggle("active", liveMode === "real");
      document.getElementById("mode-demo").classList.toggle("active", liveMode === "demo");
      document.getElementById("connection-status").textContent = liveMode === "demo" ? "演示模式运行中" : "等待插件数据...";
      refreshDecision();
      updateChecklist();
    }
    function bindProductList() {
      const input = document.getElementById("product-list");
      input.value = localStorage.getItem("ai_live_products") || "";
      input.addEventListener("input", () => {
        localStorage.setItem("ai_live_products", input.value);
        products = productsFromInput();
        refreshDecision();
        updateChecklist();
      });
      products = productsFromInput();
    }
    function productsFromInput() {
      const input = document.getElementById("product-list");
      return (input.value || "").split("\\n").map((line, index) => line.trim()).filter(Boolean).slice(0, 50).map((name, index) => ({
        name,
        score: 1 - index * 0.01,
        inventory: 1,
        profit_margin: 0
      }));
    }
    function normalizeAction(action) {
      const text = String(action || "");
      if (text.includes("No valid")) return "等待真实数据";
      if (text.includes("数据不完整")) return "等待补齐数据";
      if (text.includes("switch")) return "切换商品";
      if (text.includes("push")) return "加速逼单";
      if (text.includes("value")) return "解释价格";
      if (text.includes("sizing")) return "讲尺码";
      if (text.includes("authenticity")) return "展示正品细节";
      if (text.includes("continue")) return "继续讲";
      return text || "等待数据";
    }
    function nextSentence(action, nextAction) {
      const text = String(action || "").toLowerCase();
      if (text.includes("switch")) return "哥几个这件先过，我们切下一件更好成交的。";
      if (text.includes("value")) return "别光看价格，平时通勤能穿，买回去不会吃灰。";
      if (text.includes("sizing")) return "175/70 正常 M，里面加卫衣建议 L。";
      if (text.includes("authenticity")) return "镜头拉近看吊牌和洗标，细节我直接给你看。";
      if (text.includes("push")) return "现在已经有人在下单了，尺码合适的先锁。";
      return nextAction || "哥几个看一下，这件现在数据还不错，先继续讲 30 秒。";
    }
    async function refreshSessions() {
      const node = document.getElementById("active-rooms");
      if (liveMode === "demo") {
        node.innerHTML = '<span class="small">演示模式不需要插件</span>';
        return;
      }
      try {
        const response = await fetch("/api/live/sessions");
        const data = await response.json();
        const sessions = Array.isArray(data.sessions) ? data.sessions : [];
        const fresh = sessions.filter((item) => item.age_seconds === null || item.age_seconds <= 60).slice(0, 6);
        if (!fresh.length) { node.innerHTML = '<span class="small">暂无活跃插件数据</span>'; return; }
        if ((hostId() === "default" || !hostId()) && fresh.length === 1) setHostId(fresh[0].host_id);
        node.innerHTML = fresh.map((item) => '<button class="room" data-host-id="' + escapeHtml(item.host_id) + '">' + escapeHtml(item.host_id) + ' · ' + Math.round(item.age_seconds || 0) + 's</button>').join("");
        node.querySelectorAll(".room").forEach((button) => button.addEventListener("click", () => { setHostId(button.getAttribute("data-host-id")); refreshDecision(); }));
      } catch (error) { node.innerHTML = '<span class="small">读取活跃房间失败</span>'; }
    }
    async function refreshDecision() {
      if (liveMode === "demo") {
        renderDecision(buildDemoDecision());
        updateChecklist();
        return;
      }
      const comments = document.getElementById("comments").value || "";
      const response = await fetch("/api/live/decision", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          host_id: hostId(),
          products: productsFromInput(),
          payload: {
            host_id: hostId(),
            viewer_comments: comments
          }
        })
      });
      const data = await response.json();
      if (!data.error) renderDecision(data);
      updateChecklist();
    }
    function buildDemoDecision() {
      demoTick += 1;
      const productList = productsFromInput();
      const current = (productList[demoTick % Math.max(productList.length, 1)] || {}).name || "演示商品";
      const actions = ["continue product", "push harder", "explain value", "switch to sizing explanation", "show authenticity proof"];
      const action = actions[demoTick % actions.length];
      const payAmt = 8800 + demoTick * 320;
      return {
        valid_live_metrics: true,
        current_action: action,
        next_action: nextSentence(action, ""),
        recommended_next_product: (productList[(demoTick + 1) % Math.max(productList.length, 1)] || {}).name || "下一件演示商品",
        reason: ["demo data", "CTR up", "comments active"],
        host_id: "demo-room",
        source: "demo",
        snapshot: {
          timestamp: Date.now() / 1000,
          current_product: current,
          total_live_viewers: 1200 + demoTick * 35,
          uv: 1200 + demoTick * 35,
          online_uv: 86 + (demoTick % 7) * 5,
          pay_amt: payAmt,
          heat_score: 620 + (demoTick % 5) * 24,
          ipv_uv_rate: 0.08 + (demoTick % 4) * 0.01,
          pay_byr_rate: 0.018 + (demoTick % 3) * 0.006,
          stay_time_pu: 42 + (demoTick % 6) * 8
        },
        timeline: [{
          timestamp: Date.now() / 1000,
          decision: action,
          reason: ["演示模式", "用于培训和试播"],
          next_action: nextSentence(action, ""),
          event_type: action.includes("switch") ? "danger" : action.includes("explain") || action.includes("authenticity") ? "warning" : "positive"
        }]
      };
    }
    function renderDecision(data) {
      const snapshot = data.snapshot || {};
      const action = normalizeAction(data.current_action);
      document.getElementById("current-action").textContent = action;
      document.getElementById("next-sentence").textContent = nextSentence(data.current_action, data.next_action);
      document.getElementById("reason").textContent = (data.reason || []).slice(0, 3).join(" / ") || "--";
      document.getElementById("host-id-label").textContent = data.host_id || hostId();
      document.getElementById("viewer-count").textContent = fmtNumber(snapshot.total_live_viewers || snapshot.uv);
      document.getElementById("online-uv").textContent = fmtNumber(snapshot.online_uv);
      document.getElementById("pay-amt").textContent = fmtMoney(snapshot.pay_amt || snapshot.live_pay_amt);
      document.getElementById("heat-score").textContent = fmtNumber(snapshot.heat_score);
      document.getElementById("ctr").textContent = fmtPercent(snapshot.ipv_uv_rate);
      document.getElementById("cvr").textContent = fmtPercent(snapshot.pay_byr_rate);
      document.getElementById("watch-time").textContent = snapshot.stay_time_pu ? Math.round(snapshot.stay_time_pu) + "s" : "--";
      document.getElementById("connection-status").textContent = data.valid_live_metrics ? "真实数据已连接" : "等待有效直播数据";
      if (data.source === "demo") document.getElementById("connection-status").textContent = "演示模式运行中";
      document.getElementById("last-updated").textContent = snapshot.timestamp ? "Last updated: " + new Date(snapshot.timestamp * 1000).toLocaleTimeString("zh-CN", { hour12: false }) : "Last updated: --";
      renderTimeline(data.timeline || []);
      renderQueue(data);
      updateChecklist();
    }
    function renderTimeline(items) {
      const node = document.getElementById("timeline");
      if (!items.length) { node.innerHTML = '<div class="timeline-item">等待实时动作...</div>'; return; }
      node.innerHTML = items.slice(0, 8).map((item) => {
        const cls = item.event_type === "danger" ? " danger" : item.event_type === "warning" ? " warn" : "";
        const time = new Date((item.timestamp || Date.now() / 1000) * 1000).toLocaleTimeString("zh-CN", { hour12: false });
        return '<div class="timeline-item' + cls + '"><b>' + time + ' · ' + normalizeAction(item.decision) + '</b><div class="small">' + escapeHtml((item.reason || []).join(" / ")) + '</div><div>' + escapeHtml(nextSentence(item.decision, item.next_action)) + '</div></div>';
      }).join("");
    }
    function renderQueue(data) {
      const node = document.getElementById("queue");
      const current = (data.snapshot && data.snapshot.current_product) || (productsFromInput()[0] && productsFromInput()[0].name) || "当前商品";
      const next = data.recommended_next_product || "等待商品池";
      node.innerHTML = [["Now", current], ["Next", next], ["Action", normalizeAction(data.current_action)]].map((item) => '<div class="queue-item"><span>' + item[0] + '</span><b>' + escapeHtml(item[1]) + '</b></div>').join("");
    }
    function answerComment(text) {
      if (/175|170|180|尺码|多大|kg|斤/.test(text)) return "175/70 正常 M，里面加卫衣建议 L。";
      if (/真假|正品|真的假的/.test(text)) return "真假别听我空说，镜头拉近看吊牌、洗标、拉链和走线。";
      if (/黑色|颜色|有码/.test(text)) return "想看黑色扣3，我等下直接拿近镜头给你看。";
      if (/值|贵|价格/.test(text)) return "别光看价格，通勤能穿、场景多，买回去不会吃灰。";
      return "这个问题我先记一下，具体尺码和颜色直接打出来。";
    }
    function renderComments() {
      const input = document.getElementById("comments");
      const node = document.getElementById("comment-replies");
      const comments = input.value.split("\\n").map((line) => line.trim()).filter(Boolean).slice(0, 6);
      node.innerHTML = comments.map((comment) => '<div class="reply"><b>' + escapeHtml(comment) + '</b><div>' + escapeHtml(answerComment(comment)) + '</div></div>').join("");
      updateChecklist();
    }
    function setCheck(id, done) {
      const node = document.getElementById(id);
      if (node) node.classList.toggle("done", !!done);
    }
    function updateChecklist() {
      setCheck("check-mode", liveMode === "real" || liveMode === "demo");
      setCheck("check-connector", liveMode === "demo" || document.getElementById("connection-status").textContent.includes("真实数据"));
      setCheck("check-products", productsFromInput().length > 0);
      setCheck("check-comments", true);
      setCheck("check-action", document.getElementById("current-action").textContent !== "等待数据");
    }
    document.getElementById("mode-real").addEventListener("click", () => setMode("real"));
    document.getElementById("mode-demo").addEventListener("click", () => setMode("demo"));
    document.getElementById("save-host").addEventListener("click", () => { setHostId(hostId()); refreshDecision(); });
    document.getElementById("comments").addEventListener("input", () => { renderComments(); refreshDecision(); });
    setHostId(new URLSearchParams(location.search).get("host_id") || localStorage.getItem("ai_live_host_id") || "default");
    bindProductList();
    setMode(liveMode);
    refreshSessions(); refreshDecision();
    window.setInterval(refreshSessions, 10000);
    window.setInterval(refreshDecision, 5000);
    window.setInterval(renderComments, 5000);
  </script>
</body>
</html>"""


def _render_install_guide() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Chrome 插件安装教程</title>
  <style>
    :root { color-scheme: light; --ink: #16211f; --muted: #5b6764; --line: #d6dfdb; --paper: #f7f9f6; --panel: #fff; --accent: #0c6b58; --accent-soft: #e0f1ea; }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--paper); color: var(--ink); }
    main { max-width: 980px; margin: 0 auto; padding: 32px 20px 56px; }
    h1 { margin: 0 0 8px; font-size: 34px; }
    p { color: var(--muted); line-height: 1.6; }
    a { color: var(--accent); font-weight: 900; text-decoration: none; }
    .hero { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 22px; margin-bottom: 16px; }
    .download { display: inline-block; margin-top: 10px; background: var(--accent); color: #fff; border-radius: 8px; padding: 12px 16px; }
    .steps { display: grid; gap: 12px; counter-reset: step; }
    .step { counter-increment: step; display: grid; grid-template-columns: 44px minmax(0, 1fr); gap: 12px; background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 16px; }
    .step:before { content: counter(step); width: 38px; height: 38px; border-radius: 999px; display: grid; place-items: center; background: var(--accent-soft); color: var(--accent); font-weight: 950; }
    .step h2 { margin: 0 0 4px; font-size: 18px; }
    code { background: #edf4f1; border: 1px solid var(--line); border-radius: 6px; padding: 2px 6px; }
    .note { margin-top: 16px; padding: 14px; border-radius: 8px; border: 1px solid rgba(12, 107, 88, .22); background: rgba(12, 107, 88, .08); color: var(--accent); font-weight: 800; }
  </style>
</head>
<body>
  <main>
    <div class="hero">
      <h1>Chrome 插件安装教程</h1>
      <p>给主播电脑安装一次即可。插件只捕获淘宝直播中控页里的实时数据响应，不收集淘宝密码，不做登录自动化。</p>
      <a class="download" href="/download/chrome-extension">下载 Chrome 插件包</a>
      <p><a href="/live">打开主播控制台</a> · <a href="/">返回首页</a></p>
    </div>
    <div class="steps">
      <section class="step"><div><h2>下载并解压插件包</h2><p>点击上方下载，得到 zip 文件后先解压成文件夹。不要直接选择 zip。</p></div></section>
      <section class="step"><div><h2>打开 Chrome 扩展程序页面</h2><p>在 Chrome 地址栏输入 <code>chrome://extensions</code>，右上角打开 Developer Mode / 开发者模式。</p></div></section>
      <section class="step"><div><h2>加载插件文件夹</h2><p>点击 <code>Load unpacked</code> / 加载已解压的扩展程序，选择刚才解压出来的插件文件夹。</p></div></section>
      <section class="step"><div><h2>打开淘宝直播中控</h2><p>主播登录自己的淘宝账号，打开 <code>liveplatform.taobao.com</code> 的实时直播中控页面。</p></div></section>
      <section class="step"><div><h2>点击插件并检查 4 步状态</h2><p>插件弹窗里看到“捕获实时接口”和“发送到云端系统”完成后，回到主播控制台。</p></div></section>
      <section class="step"><div><h2>进入主播控制台</h2><p>打开 <a href="/live">/live</a>。如果只有一个活跃直播间，系统会自动连接；多人同时直播时，选择对应 Host / room ID。</p></div></section>
    </div>
    <div class="note">如果没有正在直播，插件可能抓不到目标接口。这不是报错，可以先在 /live 使用“演示模式”培训主播。</div>
  </main>
</body>
</html>"""


def _render_inventory_preview(rows: list[SmartInventoryRow], message: str) -> str:
    if not rows:
        return ""
    preview_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(row.product_name)}</td>
          <td>{html.escape(row.sku)}</td>
          <td>{html.escape(row.color)}</td>
          <td>{_display_optional(row.cost_price)}</td>
          <td>{html.escape(row.cost_currency)}</td>
          <td>{_display_optional(row.inventory)}</td>
          <td>{_display_optional(row.target_price)}</td>
          <td>{html.escape(row.notes)}</td>
        </tr>"""
        for row in rows[:80]
    )
    more = f"<p class=\"hint\">仅预览前 80 行；完整 {len(rows)} 行已填入库存商品输入框。</p>" if len(rows) > 80 else ""
    notice = f'<div class="notice">{html.escape(message)}</div>' if message else ""
    return f"""
      {notice}
      <div class="preview">
        <table>
          <thead>
            <tr>
              <th>product_name</th>
              <th>sku</th>
              <th>color</th>
              <th>cost_price</th>
              <th>cost_currency</th>
              <th>inventory</th>
              <th>target_price</th>
              <th>notes</th>
            </tr>
          </thead>
          <tbody>{preview_rows}</tbody>
        </table>
      </div>
      {more}
    """


def _display_optional(value: object) -> str:
    if value is None or value == "":
        return "unknown"
    return html.escape(str(value))


def _inject_report_history_banner(report_html: str, report_id: str) -> str:
    banner = f"""
    <section class="order-panel">
      <h2>报告已保存</h2>
      <p>报告 ID：{html.escape(report_id)}</p>
      <p><a href="/live">打开主播控制台</a> · <a href="/install">插件安装教程</a> · <a href="/reports/{html.escape(report_id)}">查看保存版本</a> · <a href="/reports/{html.escape(report_id)}/export">下载 HTML</a> · <a href="/reports">历史报告</a> · <a href="/download/chrome-extension">下载 Chrome 插件包</a></p>
    </section>"""
    return report_html.replace("<main>", f"<main>{banner}", 1)


def _render_login_form(error: str | None = None) -> str:
    error_html = f'<div class="error">{html.escape(error)}</div>' if error else ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI Live Commerce Copilot 登录</title>
  <style>
    :root {{
      --ink: #16211f;
      --muted: #5b6764;
      --line: #d6dfdb;
      --paper: #f7f9f6;
      --panel: #ffffff;
      --accent: #0c6b58;
      --danger: #9f2f22;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--ink);
      background: var(--paper);
    }}
    main {{
      width: min(460px, calc(100vw - 32px));
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 26px;
      box-shadow: 0 18px 45px rgba(22, 33, 31, .08);
    }}
    h1 {{ margin: 0 0 8px; font-size: 28px; letter-spacing: 0; }}
    p {{ color: var(--muted); line-height: 1.5; }}
    label {{ display: block; font-weight: 800; margin: 18px 0 8px; }}
    input {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      font-size: 16px;
      background: #fbfdfb;
    }}
    input:focus {{
      outline: 3px solid rgba(12, 107, 88, .16);
      border-color: var(--accent);
    }}
    button {{
      margin-top: 16px;
      width: 100%;
      border: 0;
      border-radius: 8px;
      background: var(--accent);
      color: white;
      font-weight: 900;
      font-size: 16px;
      padding: 12px 16px;
      cursor: pointer;
    }}
    .error {{
      margin: 14px 0 0;
      padding: 12px 14px;
      border: 1px solid rgba(159, 47, 34, .3);
      background: rgba(159, 47, 34, .08);
      color: var(--danger);
      border-radius: 8px;
      font-weight: 700;
    }}
  </style>
</head>
<body>
  <main>
    <h1>AI Live Commerce Copilot</h1>
    <p>请输入访问密码。API keys 只保存在服务器环境变量中，不会显示给使用者。</p>
    {error_html}
    <form method="post" action="/login">
      <label for="password">访问密码</label>
      <input id="password" name="password" type="password" autocomplete="current-password" autofocus>
      <button type="submit">进入工作台</button>
    </form>
  </main>
</body>
</html>"""

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
    <p><a href="/reports">查看历史报告 / 导出 HTML</a> · <a href="/download/chrome-extension">下载 Chrome 插件包</a></p>
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
      <p><a href="/reports/{html.escape(report_id)}">查看保存版本</a> · <a href="/reports/{html.escape(report_id)}/export">下载 HTML</a> · <a href="/reports">历史报告</a> · <a href="/download/chrome-extension">下载 Chrome 插件包</a></p>
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

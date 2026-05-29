from __future__ import annotations

import html
import io
import time
import csv
import json
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


@app.get("/live/prompter", response_class=HTMLResponse)
def live_prompter(request: Request) -> str:
    if not is_authenticated(request):
        return _render_login_form()
    return _render_live_prompter()


@app.get("/install", response_class=HTMLResponse)
def install_guide(request: Request) -> str:
    if not is_authenticated(request):
        return _render_login_form()
    return _render_install_guide()


@app.get("/workspace/{workspace_id}", response_class=HTMLResponse)
def workspace_onboarding(request: Request, workspace_id: str) -> str:
    if not is_authenticated(request):
        return _render_login_form()
    return _render_workspace_onboarding(workspace_id)


@app.get("/admin/live", response_class=HTMLResponse)
def admin_live(request: Request) -> str:
    if not is_authenticated(request):
        return _render_login_form()
    return _render_admin_live()


@app.get("/boss", response_class=HTMLResponse)
def boss_dashboard(request: Request) -> str:
    if not is_authenticated(request):
        return _render_login_form()
    return _render_boss_dashboard()


@app.get("/api/boss/dashboard")
async def boss_dashboard_api(request: Request, workspace_id: str = "") -> dict[str, Any]:
    if not is_authenticated(request):
        return {"error": "unauthorized"}
    return live_data_connector.boss_dashboard(workspace_id=workspace_id or None)


@app.get("/admin/live/{host_id}", response_class=HTMLResponse)
def admin_live_detail(request: Request, host_id: str) -> str:
    if not is_authenticated(request):
        return _render_login_form()
    return _render_admin_live_detail(host_id)


@app.get("/admin/live/{host_id}/export.csv")
def export_live_history(request: Request, host_id: str) -> Response:
    if not is_authenticated(request):
        return RedirectResponse("/", status_code=303)
    history = live_data_connector.session_history(host_id)
    rows = history.get("snapshots") or []
    output = io.StringIO()
    fieldnames = [
        "timestamp",
        "host_id",
        "session_name",
        "host_name",
        "target_gmv",
        "current_product",
        "online_uv",
        "total_viewers",
        "heat_score",
        "pay_amt",
        "ipv_uv_rate",
        "pay_byr_rate",
        "stay_time_pu",
        "comment_uv",
        "pay_item_qty",
        "pay_buyer_cnt",
        "item_click_rate",
        "item_conversion_rate",
        "item_add_cart_rate",
        "item_gmv",
        "source",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    metadata = history.get("metadata") or {}
    for row in rows:
        writer.writerow({
            **{key: row.get(key, "") for key in fieldnames},
            "session_name": metadata.get("session_name", ""),
            "host_name": metadata.get("host_name", ""),
            "target_gmv": metadata.get("target_gmv", ""),
        })
    return Response(
        content=output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="live-history-{html.escape(host_id)}.csv"'},
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
async def live_sessions(request: Request, workspace_id: str = "") -> dict[str, Any]:
    if not is_authenticated(request):
        return {"error": "unauthorized"}
    return {"sessions": live_data_connector.active_sessions(workspace_id=workspace_id or None)}


@app.get("/api/live/history")
async def live_history(request: Request, host_id: str = "default") -> dict[str, Any]:
    if not is_authenticated(request):
        return {"error": "unauthorized"}
    return live_data_connector.session_history(host_id)


@app.post("/api/live/session-meta")
async def live_session_meta(request: Request) -> dict[str, Any]:
    if not is_authenticated(request):
        return {"error": "unauthorized"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    host_id = body.get("host_id") or body.get("hostId") or "default"
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else body
    return live_data_connector.update_session_metadata(str(host_id), metadata)


@app.post("/api/live/host-feedback")
async def live_host_feedback(request: Request) -> dict[str, Any]:
    if not is_authenticated(request):
        return {"error": "unauthorized"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    host_id = body.get("host_id") or body.get("hostId")
    return live_data_connector.record_host_feedback(str(host_id or ""), body)


@app.post("/api/live/boss-intervention")
async def live_boss_intervention(request: Request) -> dict[str, Any]:
    if not is_authenticated(request):
        return {"error": "unauthorized"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    host_id = body.get("host_id") or body.get("hostId")
    return live_data_connector.set_boss_intervention(str(host_id or ""), body)


@app.get("/api/live/boss-intervention")
async def get_live_boss_intervention(request: Request, host_id: str = "default", workspace_id: str = "") -> dict[str, Any]:
    if not is_authenticated(request):
        return {"error": "unauthorized"}
    payload = {"workspace_id": workspace_id} if workspace_id else {}
    return live_data_connector.get_boss_intervention(host_id, payload)


@app.post("/api/live/boss-intervention/ack")
async def ack_live_boss_intervention(request: Request) -> dict[str, Any]:
    if not is_authenticated(request):
        return {"error": "unauthorized"}
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    host_id = body.get("host_id") or body.get("hostId")
    return live_data_connector.ack_boss_intervention(str(host_id or ""), body)


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
    <p><a href="/boss">老板总控看板</a> · <a href="/workspace/demo">客户交付页</a> · <a href="/live">打开主播控制台</a> · <a href="/live/prompter">主播大字提词器</a> · <a href="/admin/live">直播监控后台</a> · <a href="/install">插件安装教程</a> · <a href="/reports">查看历史报告 / 导出 HTML</a> · <a href="/download/chrome-extension">下载 Chrome 插件包</a></p>
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
    .timer { border: 1px solid var(--line); border-radius: 10px; padding: 14px; background: #fbfdfb; margin-top: 14px; }
    .timer b { display: block; font-size: 36px; color: var(--accent); }
    .timer.warn b { color: var(--warn); }
    .timer.danger b { color: var(--danger); }
    .product-cards { display: grid; gap: 8px; margin-top: 10px; }
    .product-card { border: 1px solid var(--line); border-radius: 8px; padding: 10px; background: #fbfdfb; }
    .product-card b { display: block; }
    .quick-actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
    .quick-actions .ghost { background: #e7efeb; color: var(--accent); }
    .toast { color: var(--accent); font-weight: 900; min-height: 20px; }
    .sticky-action { position: sticky; bottom: 12px; z-index: 4; margin-top: 14px; border: 1px solid rgba(12, 107, 88, .24); box-shadow: 0 14px 40px rgba(22, 33, 31, .14); }
    .boss-alert { display: none; border: 2px solid var(--danger); background: #fff1f2; color: var(--danger); border-radius: 10px; padding: 14px; margin-bottom: 14px; font-size: 22px; font-weight: 950; }
    .boss-alert.show { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
    .boss-alert button { background: var(--danger); color: #fff; white-space: nowrap; }
    @media (max-width: 900px) { .layout { grid-template-columns: 1fr; } .cards { grid-template-columns: repeat(2, minmax(0, 1fr)); } .action { font-size: 42px; } .sentence { font-size: 28px; } }
  </style>
</head>
<body>
  <main>
    <header>
      <div><h1>主播实时控制台</h1><div class="small">只看未来 10-30 秒该做什么</div></div>
      <div>
        <span class="mode-toggle"><button id="mode-real" type="button" class="active">真实</button><button id="mode-demo" type="button">演示</button></span>
        <span class="status" id="connection-status">等待插件数据...</span><a href="/live/prompter" style="margin-left:12px;">大字提词器</a><a href="/" style="margin-left:12px;">返回选品</a>
      </div>
    </header>
    <section class="boss-alert" id="boss-alert">
      <div id="boss-alert-message">等待老板指令</div>
      <button id="boss-alert-ack" type="button">收到，马上做</button>
    </section>
    <section class="layout">
      <div>
        <section class="panel hero">
          <span class="label">Current action</span>
          <div class="action" id="current-action">等待数据</div>
          <span class="label">Next sentence</span>
          <div class="sentence" id="next-sentence">打开淘宝直播中控页，并确认插件已捕获数据。</div>
          <div class="reason" id="reason">--</div>
          <div class="quick-actions">
            <button id="copy-sentence" type="button">复制下一句</button>
            <button id="mark-executed" type="button" class="ghost">标记已执行</button>
            <span class="small">快捷键：C 复制 · E 标记执行</span>
          </div>
          <div class="toast" id="host-toast"></div>
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
        <section class="timer" id="product-timer">
          <span class="label">当前商品讲解时长</span>
          <b id="product-elapsed">00:00</b>
          <div class="small" id="product-timer-hint">切换商品后自动重新计时。</div>
        </section>
        <section class="panel sticky-action">
          <span class="label">主播操作台</span>
          <div class="quick-actions">
            <button id="copy-sentence-sticky" type="button">复制话术</button>
            <button id="mark-executed-sticky" type="button" class="ghost">我已照做</button>
            <a href="/live/prompter" id="prompter-link">打开大字屏</a>
          </div>
          <div class="small">主播只需要：照着下一句讲，讲完点“我已照做”。</div>
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
          <span class="label">场次备注</span>
          <input id="session-name" placeholder="场次名，例如：5月28晚场">
          <input id="host-name" placeholder="主播，例如：Gigi" style="margin-top:8px;">
          <input id="target-gmv" placeholder="目标 GMV，例如：50000" style="margin-top:8px;">
          <button id="save-session-meta" type="button" style="margin-top:8px;">保存场次信息</button>
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
          <textarea class="comments" id="product-list" placeholder="每行一个商品；可用 | 分隔价格和卖点&#10;Kragg Shirt | ¥499 | 特价T恤，适合通勤&#10;Atom Jacket | ¥1709 | 日常保暖"></textarea>
          <div class="small">会参与“推荐下一件”决策，保存在本机浏览器。</div>
          <div class="product-cards" id="product-cards"></div>
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
    let currentProductName = "";
    let currentProductStartedAt = Date.now();
    const urlParams = new URLSearchParams(location.search);
    const workspaceId = urlParams.get("workspace_id") || localStorage.getItem("ai_live_workspace_id") || "";
    if (workspaceId) localStorage.setItem("ai_live_workspace_id", workspaceId);
    function fmtNumber(value) { const numeric = Number(value || 0); return Number.isFinite(numeric) && numeric ? Math.round(numeric).toLocaleString("zh-CN") : "--"; }
    function fmtMoney(value) { const numeric = Number(value || 0); return Number.isFinite(numeric) && numeric ? "¥" + Math.round(numeric).toLocaleString("zh-CN") : "--"; }
    function fmtPercent(value) { const numeric = Number(value || 0); return Number.isFinite(numeric) && numeric ? (numeric * 100).toFixed(1) + "%" : "--"; }
    function escapeHtml(value) { return String(value || "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char])); }
    function hostId() { const input = document.getElementById("host-id-input"); return (input && input.value.trim()) || localStorage.getItem("ai_live_host_id") || "default"; }
    function setHostId(value) { const next = value || "default"; document.getElementById("host-id-input").value = next; document.getElementById("host-id-label").textContent = next; localStorage.setItem("ai_live_host_id", next); bindSessionMeta(); }
    function metaKey(key) { return "ai_live_" + hostId() + "_" + key; }
    function bindSessionMeta() {
      document.getElementById("session-name").value = localStorage.getItem(metaKey("session_name")) || "";
      document.getElementById("host-name").value = localStorage.getItem(metaKey("host_name")) || "";
      document.getElementById("target-gmv").value = localStorage.getItem(metaKey("target_gmv")) || "";
    }
    async function saveSessionMeta() {
      const metadata = {
        session_name: document.getElementById("session-name").value.trim(),
        host_name: document.getElementById("host-name").value.trim(),
        target_gmv: document.getElementById("target-gmv").value.trim()
      };
      Object.keys(metadata).forEach((key) => localStorage.setItem(metaKey(key), metadata[key]));
      await fetch("/api/live/session-meta", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ host_id: hostId(), metadata })
      }).catch(() => {});
    }
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
        renderProductCards();
        refreshDecision();
        updateChecklist();
      });
      products = productsFromInput();
      renderProductCards();
    }
    function productsFromInput() {
      const input = document.getElementById("product-list");
      return (input.value || "").split("\\n").map((line) => line.trim()).filter(Boolean).slice(0, 50).map((line, index) => {
        const parts = line.split("|").map((part) => part.trim());
        const name = parts[0] || line;
        return {
          name,
          price: parts[1] || "",
          note: parts[2] || "",
          raw: line,
        score: 1 - index * 0.01,
        inventory: 1,
        profit_margin: 0
        };
      });
    }
    function renderProductCards() {
      const node = document.getElementById("product-cards");
      const list = productsFromInput().slice(0, 8);
      if (!list.length) {
        node.innerHTML = '<div class="small">等待商品队列...</div>';
        return;
      }
      node.innerHTML = list.map((product, index) => '<div class="product-card"><b>' + (index + 1) + '. ' + escapeHtml(product.name) + '</b><div class="small">' + escapeHtml([product.price, product.note].filter(Boolean).join(" · ") || "未填写价格/卖点") + '</div></div>').join("");
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
        const query = workspaceId ? "?workspace_id=" + encodeURIComponent(workspaceId) : "";
        const response = await fetch("/api/live/sessions" + query);
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
            workspace_id: workspaceId,
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
      updateProductTimer((snapshot.current_product || (productsFromInput()[0] && productsFromInput()[0].name) || "当前商品"));
      renderTimeline(data.timeline || []);
      renderQueue(data);
      updateChecklist();
    }
    function updateProductTimer(productName) {
      if (productName !== currentProductName) {
        currentProductName = productName;
        currentProductStartedAt = Date.now();
      }
      renderProductTimer();
    }
    function renderProductTimer() {
      const elapsed = Math.max(0, Math.floor((Date.now() - currentProductStartedAt) / 1000));
      const minutes = String(Math.floor(elapsed / 60)).padStart(2, "0");
      const seconds = String(elapsed % 60).padStart(2, "0");
      const panel = document.getElementById("product-timer");
      document.getElementById("product-elapsed").textContent = minutes + ":" + seconds;
      panel.classList.toggle("warn", elapsed >= 90 && elapsed < 150);
      panel.classList.toggle("danger", elapsed >= 150);
      document.getElementById("product-timer-hint").textContent = elapsed >= 150
        ? "已超过 150 秒，强烈建议切品或换话题。"
        : elapsed >= 90
        ? "已超过 90 秒，准备收口并切下一件。"
        : "讲解节奏正常。";
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
    async function copyNextSentence() {
      const text = document.getElementById("next-sentence").textContent.trim();
      if (!text) return;
      try {
        await navigator.clipboard.writeText(text);
        showHostToast("已复制，主播可以直接念。");
      } catch (_error) {
        showHostToast("复制失败，请手动选中文字。");
      }
    }
    async function markExecuted() {
      const action = document.getElementById("current-action").textContent.trim();
      const sentence = document.getElementById("next-sentence").textContent.trim();
      const currentProduct = currentProductName || "";
      await fetch("/api/live/host-feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          host_id: hostId(),
          workspace_id: workspaceId,
          action,
          sentence,
          current_product: currentProduct,
          source: "host_console"
        })
      }).catch(() => {});
      showHostToast("已记录执行，老板复盘能看到。");
    }
    function showHostToast(message) {
      const node = document.getElementById("host-toast");
      if (!node) return;
      node.textContent = message;
      window.clearTimeout(window.__hostToastTimer);
      window.__hostToastTimer = window.setTimeout(() => { node.textContent = ""; }, 2500);
    }
    async function refreshBossIntervention() {
      const query = "?host_id=" + encodeURIComponent(hostId()) + (workspaceId ? "&workspace_id=" + encodeURIComponent(workspaceId) : "");
      const response = await fetch("/api/live/boss-intervention" + query).catch(() => null);
      if (!response) return;
      const data = await response.json().catch(() => ({}));
      const alert = document.getElementById("boss-alert");
      const messageNode = document.getElementById("boss-alert-message");
      const intervention = data.intervention;
      if (!alert) return;
      if (!intervention || !intervention.message) {
        alert.classList.remove("show");
        window.__bossInterventionMessage = "";
        return;
      }
      window.__bossInterventionMessage = intervention.message;
      if (messageNode) messageNode.textContent = "老板提醒：" + intervention.message;
      alert.classList.add("show");
    }
    async function ackBossIntervention() {
      const message = window.__bossInterventionMessage || "";
      if (!message) return;
      await fetch("/api/live/boss-intervention/ack", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ host_id: hostId(), workspace_id: workspaceId, message, acknowledged_by: "host_console" })
      }).catch(() => {});
      window.__bossInterventionMessage = "";
      const alert = document.getElementById("boss-alert");
      if (alert) alert.classList.remove("show");
      toast("已确认老板指令");
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
    document.getElementById("save-host").addEventListener("click", () => { setHostId(hostId()); saveSessionMeta(); refreshDecision(); });
    document.getElementById("save-session-meta").addEventListener("click", () => { saveSessionMeta(); });
    document.getElementById("comments").addEventListener("input", () => { renderComments(); refreshDecision(); });
    document.getElementById("copy-sentence").addEventListener("click", copyNextSentence);
    document.getElementById("copy-sentence-sticky").addEventListener("click", copyNextSentence);
    document.getElementById("mark-executed").addEventListener("click", markExecuted);
    document.getElementById("mark-executed-sticky").addEventListener("click", markExecuted);
    document.getElementById("boss-alert-ack").addEventListener("click", ackBossIntervention);
    document.addEventListener("keydown", (event) => {
      const target = event.target && event.target.tagName ? event.target.tagName.toLowerCase() : "";
      if (target === "input" || target === "textarea") return;
      if (event.key.toLowerCase() === "c") copyNextSentence();
      if (event.key.toLowerCase() === "e") markExecuted();
    });
    setHostId(urlParams.get("host_id") || localStorage.getItem("ai_live_host_id") || "default");
    if (workspaceId) document.getElementById("prompter-link").href = "/live/prompter?workspace_id=" + encodeURIComponent(workspaceId);
    bindProductList();
    setMode(liveMode);
    refreshSessions(); refreshDecision();
    refreshBossIntervention();
    window.setInterval(refreshSessions, 10000);
    window.setInterval(refreshDecision, 5000);
    window.setInterval(refreshBossIntervention, 5000);
    window.setInterval(renderComments, 5000);
    window.setInterval(renderProductTimer, 1000);
  </script>
</body>
</html>"""


def _render_live_prompter() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>主播大字提词器</title>
  <style>
    :root { color-scheme: dark; --bg: #070b0a; --panel: #101816; --ink: #f6faf8; --muted: #9fb0aa; --line: #24322f; --good: #34d399; --warn: #fbbf24; --danger: #fb7185; --accent: #5eead4; }
    * { box-sizing: border-box; }
    html, body { min-height: 100%; }
    body { margin: 0; font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: radial-gradient(circle at top left, #14231f, var(--bg) 42%); color: var(--ink); }
    main { width: min(1500px, calc(100vw - 36px)); margin: 0 auto; padding: 20px 0 28px; }
    header { display: flex; justify-content: space-between; gap: 14px; align-items: center; margin-bottom: 16px; }
    h1 { margin: 0; font-size: 20px; color: var(--muted); font-weight: 850; }
    a { color: var(--accent); text-decoration: none; font-weight: 900; }
    button, input { border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; background: #0c1311; color: var(--ink); font: inherit; }
    button { cursor: pointer; font-weight: 950; }
    button.primary { background: var(--accent); color: #05201b; border-color: var(--accent); }
    .topbar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .pill { border: 1px solid var(--line); border-radius: 999px; padding: 8px 12px; background: rgba(255,255,255,.04); color: var(--muted); font-weight: 850; }
    .stage { min-height: calc(100vh - 190px); display: grid; grid-template-columns: minmax(0, 1fr) 360px; gap: 16px; }
    .main-card { border: 1px solid var(--line); border-radius: 16px; background: rgba(16, 24, 22, .88); padding: clamp(20px, 4vw, 56px); display: grid; align-content: center; gap: 24px; box-shadow: 0 24px 80px rgba(0,0,0,.28); }
    .label { display: block; color: var(--muted); font-size: clamp(14px, 1.3vw, 20px); font-weight: 950; text-transform: uppercase; letter-spacing: 0; }
    .action { font-size: clamp(54px, 10vw, 148px); line-height: .98; font-weight: 1000; color: var(--good); overflow-wrap: anywhere; }
    .action.warn { color: var(--warn); }
    .action.danger { color: var(--danger); }
    .sentence { font-size: clamp(34px, 5.5vw, 78px); line-height: 1.12; font-weight: 950; overflow-wrap: anywhere; }
    .reason { color: var(--muted); font-size: clamp(18px, 2vw, 28px); line-height: 1.35; font-weight: 850; }
    .side { display: grid; gap: 12px; align-content: start; }
    .panel { border: 1px solid var(--line); border-radius: 14px; background: rgba(16, 24, 22, .82); padding: 16px; }
    .metric-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    .metric { border: 1px solid var(--line); border-radius: 12px; padding: 12px; background: rgba(255,255,255,.035); min-height: 86px; }
    .metric span { display: block; color: var(--muted); font-size: 13px; font-weight: 900; }
    .metric b { display: block; font-size: 30px; margin-top: 6px; overflow-wrap: anywhere; }
    .queue { display: grid; gap: 8px; }
    .queue-row { display: grid; grid-template-columns: 64px minmax(0, 1fr); gap: 8px; border: 1px solid var(--line); border-radius: 10px; padding: 10px; background: rgba(255,255,255,.035); }
    .queue-row span { color: var(--muted); font-weight: 950; }
    .queue-row b { overflow-wrap: anywhere; }
    .tiny { color: var(--muted); font-size: 13px; line-height: 1.45; }
    .controls { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; margin-top: 8px; }
    .status-line { display: flex; justify-content: space-between; gap: 8px; color: var(--muted); font-weight: 850; margin-top: 10px; }
    .prompter-actions { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
    .prompter-actions .ghost { background: #14201d; color: var(--accent); }
    .toast { color: var(--accent); font-weight: 950; min-height: 22px; font-size: 18px; }
    .boss-alert { display: none; border: 2px solid var(--danger); background: rgba(251, 113, 133, .14); color: #fecdd3; border-radius: 14px; padding: 18px; margin-bottom: 16px; font-size: clamp(28px, 4vw, 56px); font-weight: 1000; line-height: 1.08; }
    .boss-alert.show { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 16px; align-items: center; }
    .boss-alert button { background: #fecdd3; color: #5f0f1d; border: 0; white-space: nowrap; font-size: 18px; font-weight: 950; }
    @media (max-width: 1000px) { .stage { grid-template-columns: 1fr; } .side { grid-template-columns: 1fr 1fr; } }
    @media (max-width: 720px) { main { width: calc(100vw - 20px); } header { align-items: flex-start; } .side { grid-template-columns: 1fr; } .metric-grid { grid-template-columns: 1fr 1fr; } .action { font-size: 56px; } .sentence { font-size: 34px; } }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>主播大字提词器</h1>
        <div class="tiny">开播时只看这一屏：当前动作、下一句话、是否该切品。</div>
      </div>
      <div class="topbar">
        <span class="pill" id="source-pill">等待数据</span>
        <a href="/live">主播控制台</a>
        <a href="/boss">老板看板</a>
      </div>
    </header>
    <section class="boss-alert" id="boss-alert">
      <div id="boss-alert-message">等待老板指令</div>
      <button id="boss-alert-ack" type="button">收到，马上做</button>
    </section>
    <section class="stage">
      <section class="main-card">
        <span class="label">Current action</span>
        <div class="action" id="prompter-action">等待真实数据</div>
        <span class="label">Next sentence</span>
        <div class="sentence" id="prompter-sentence">打开淘宝直播中控页，确认插件正在捕获实时数据。</div>
        <div class="reason" id="prompter-reason">没有真实指标时，这里不会给主播乱下指令。</div>
        <div class="prompter-actions">
          <button class="primary" id="copy-sentence" type="button">复制下一句</button>
          <button class="ghost" id="mark-executed" type="button">我已照做</button>
          <span class="tiny">快捷键：C 复制 · E 标记执行</span>
        </div>
        <div class="toast" id="host-toast"></div>
      </section>
      <aside class="side">
        <section class="panel">
          <span class="label">连接</span>
          <div class="controls">
            <input id="host-id-input" placeholder="default 或 liveId">
            <button class="primary" id="connect-host" type="button">连接</button>
          </div>
          <div class="status-line"><span>Host</span><b id="host-id-label">default</b></div>
          <div class="status-line"><span>更新</span><b id="last-updated">--</b></div>
          <div class="status-line"><span>模式</span><b id="mode-label">真实</b></div>
        </section>
        <section class="panel">
          <span class="label">直播指标</span>
          <div class="metric-grid">
            <div class="metric"><span>总观看</span><b id="viewer-count">--</b></div>
            <div class="metric"><span>在线</span><b id="online-uv">--</b></div>
            <div class="metric"><span>GMV</span><b id="pay-amt">--</b></div>
            <div class="metric"><span>热度</span><b id="heat-score">--</b></div>
            <div class="metric"><span>CTR</span><b id="ctr">--</b></div>
            <div class="metric"><span>CVR</span><b id="cvr">--</b></div>
          </div>
        </section>
        <section class="panel">
          <span class="label">商品节奏</span>
          <div class="metric" style="margin-top:10px;"><span>当前商品</span><b id="current-product">--</b></div>
          <div class="metric" style="margin-top:10px;"><span>讲解时长</span><b id="product-timer">00:00</b></div>
        </section>
        <section class="panel">
          <span class="label">推荐队列</span>
          <div class="queue" id="queue">
            <div class="queue-row"><span>Now</span><b>等待商品池</b></div>
          </div>
          <div class="tiny" style="margin-top:10px;">商品队列从主播控制台同步。没有商品池时，系统只做直播间级别判断。</div>
        </section>
      </aside>
    </section>
  </main>
  <script>
    let currentProduct = "";
    let productStartedAt = Date.now();
    let demoTick = 0;
    const urlParams = new URLSearchParams(location.search);
    const workspaceId = urlParams.get("workspace_id") || localStorage.getItem("ai_live_workspace_id") || "";
    if (workspaceId) localStorage.setItem("ai_live_workspace_id", workspaceId);
    function hostId() { return (document.getElementById("host-id-input").value.trim() || localStorage.getItem("ai_live_host_id") || "default"); }
    function setHostId(value) {
      const next = value || "default";
      localStorage.setItem("ai_live_host_id", next);
      document.getElementById("host-id-input").value = next;
      document.getElementById("host-id-label").textContent = next;
    }
    function productsFromStorage() {
      return (localStorage.getItem("ai_live_products") || "").split("\\n").map((line, index) => {
        const parts = line.trim().split("|").map((part) => part.trim());
        return parts[0] ? { name: parts[0], raw: line, score: 1 - index * 0.01, inventory: 1, profit_margin: 0 } : null;
      }).filter(Boolean).slice(0, 50);
    }
    function fmtNumber(value) { const numeric = Number(value || 0); return Number.isFinite(numeric) && numeric ? Math.round(numeric).toLocaleString("zh-CN") : "--"; }
    function fmtMoney(value) { const numeric = Number(value || 0); return Number.isFinite(numeric) && numeric ? "¥" + Math.round(numeric).toLocaleString("zh-CN") : "--"; }
    function fmtPercent(value) { const numeric = Number(value || 0); return Number.isFinite(numeric) && numeric ? (numeric * 100).toFixed(1) + "%" : "--"; }
    function escapeHtml(value) { return String(value || "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char])); }
    function normalizeAction(action) {
      const text = String(action || "").toLowerCase();
      if (text.includes("no valid") || text.includes("数据不完整")) return "等待数据";
      if (text.includes("switch")) return "切换商品";
      if (text.includes("push")) return "加速逼单";
      if (text.includes("value") || text.includes("price")) return "解释价格";
      if (text.includes("sizing")) return "讲尺码";
      if (text.includes("authenticity")) return "展示正品细节";
      if (text.includes("continue")) return "继续讲";
      return action || "等待数据";
    }
    function actionTone(action) {
      const text = String(action || "").toLowerCase();
      if (text.includes("switch") || text.includes("no valid") || text.includes("等待")) return "danger";
      if (text.includes("value") || text.includes("price") || text.includes("sizing") || text.includes("authenticity")) return "warn";
      return "good";
    }
    function sentenceFor(action, fallback) {
      const text = String(action || "").toLowerCase();
      if (text.includes("no valid")) return "先别乱切品，等插件数据进来再判断。";
      if (text.includes("switch")) return "哥几个这件先过，我们切下一件更好成交的。";
      if (text.includes("value") || text.includes("price")) return "别光看价格，平时通勤能穿，买回去不会吃灰。";
      if (text.includes("sizing")) return "175/70 正常 M，里面加卫衣建议 L。";
      if (text.includes("authenticity")) return "镜头拉近看吊牌和洗标，细节我直接给你看。";
      if (text.includes("push")) return "现在已经有人在下单了，尺码合适的先锁。";
      return fallback || "哥几个看一下，这件再讲 30 秒，看数据能不能继续顶上去。";
    }
    async function autoPickFreshHost() {
      try {
        const query = workspaceId ? "?workspace_id=" + encodeURIComponent(workspaceId) : "";
        const response = await fetch("/api/live/sessions" + query);
        const data = await response.json();
        const sessions = Array.isArray(data.sessions) ? data.sessions : [];
        const fresh = sessions.filter((item) => item.age_seconds === null || item.age_seconds <= 60);
        if ((hostId() === "default" || !hostId()) && fresh.length === 1) setHostId(fresh[0].host_id);
      } catch (_error) {}
    }
    async function refreshDecision() {
      const mode = localStorage.getItem("ai_live_mode") || "real";
      document.getElementById("mode-label").textContent = mode === "demo" ? "演示" : "真实";
      if (mode === "demo") {
        renderDecision(buildDemoDecision());
        return;
      }
      const response = await fetch("/api/live/decision", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ host_id: hostId(), products: productsFromStorage(), payload: { host_id: hostId(), workspace_id: workspaceId } })
      });
      const data = await response.json();
      if (!data.error) renderDecision(data);
    }
    function buildDemoDecision() {
      demoTick += 1;
      const products = productsFromStorage();
      const product = (products[demoTick % Math.max(products.length, 1)] || {}).name || "演示商品";
      const actions = ["continue product", "push harder", "explain value", "switch to sizing explanation", "show authenticity proof"];
      const action = actions[demoTick % actions.length];
      return {
        source: "demo",
        valid_live_metrics: true,
        current_action: action,
        next_action: sentenceFor(action, ""),
        recommended_next_product: (products[(demoTick + 1) % Math.max(products.length, 1)] || {}).name || "下一件演示商品",
        reason: ["演示数据", "用于培训主播", "每 5 秒刷新"],
        snapshot: {
          timestamp: Date.now() / 1000,
          current_product: product,
          total_live_viewers: 1200 + demoTick * 30,
          online_uv: 80 + demoTick,
          pay_amt: 8800 + demoTick * 260,
          heat_score: 620 + demoTick * 7,
          ipv_uv_rate: 0.08,
          pay_byr_rate: 0.022
        }
      };
    }
    function renderDecision(data) {
      const snapshot = data.snapshot || {};
      const rawAction = data.current_action || "";
      const action = normalizeAction(rawAction);
      const actionNode = document.getElementById("prompter-action");
      actionNode.textContent = action;
      actionNode.classList.remove("warn", "danger");
      const tone = actionTone(rawAction || action);
      if (tone === "warn") actionNode.classList.add("warn");
      if (tone === "danger") actionNode.classList.add("danger");
      document.getElementById("prompter-sentence").textContent = sentenceFor(rawAction, data.next_action);
      document.getElementById("prompter-reason").textContent = (data.reason || []).slice(0, 3).join(" / ") || "等待更多趋势数据。";
      document.getElementById("source-pill").textContent = data.valid_live_metrics ? "实时数据已连接" : "等待有效直播数据";
      if (data.source === "demo") document.getElementById("source-pill").textContent = "演示模式";
      document.getElementById("viewer-count").textContent = fmtNumber(snapshot.total_live_viewers || snapshot.uv);
      document.getElementById("online-uv").textContent = fmtNumber(snapshot.online_uv);
      document.getElementById("pay-amt").textContent = fmtMoney(snapshot.pay_amt || snapshot.live_pay_amt);
      document.getElementById("heat-score").textContent = fmtNumber(snapshot.heat_score);
      document.getElementById("ctr").textContent = fmtPercent(snapshot.ipv_uv_rate);
      document.getElementById("cvr").textContent = fmtPercent(snapshot.pay_byr_rate);
      document.getElementById("last-updated").textContent = snapshot.timestamp ? new Date(snapshot.timestamp * 1000).toLocaleTimeString("zh-CN", { hour12: false }) : "--";
      const product = snapshot.current_product || (productsFromStorage()[0] && productsFromStorage()[0].name) || "--";
      updateProduct(product);
      renderQueue(product, data.recommended_next_product, action);
    }
    function updateProduct(productName) {
      if (productName !== currentProduct) {
        currentProduct = productName;
        productStartedAt = Date.now();
      }
      document.getElementById("current-product").textContent = productName;
    }
    function renderTimer() {
      const elapsed = Math.max(0, Math.floor((Date.now() - productStartedAt) / 1000));
      const minutes = String(Math.floor(elapsed / 60)).padStart(2, "0");
      const seconds = String(elapsed % 60).padStart(2, "0");
      document.getElementById("product-timer").textContent = minutes + ":" + seconds;
    }
    function renderQueue(current, next, action) {
      const node = document.getElementById("queue");
      const rows = [["Now", current || "--"], ["Next", next || "等待推荐"], ["Action", action || "--"]];
      node.innerHTML = rows.map((row) => '<div class="queue-row"><span>' + row[0] + '</span><b>' + escapeHtml(row[1]) + '</b></div>').join("");
    }
    async function copyNextSentence() {
      const text = document.getElementById("prompter-sentence").textContent.trim();
      if (!text) return;
      try {
        await navigator.clipboard.writeText(text);
        showHostToast("已复制。");
      } catch (_error) {
        showHostToast("复制失败，请手动选中文字。");
      }
    }
    async function markExecuted() {
      const action = document.getElementById("prompter-action").textContent.trim();
      const sentence = document.getElementById("prompter-sentence").textContent.trim();
      const currentProductName = document.getElementById("current-product").textContent.trim();
      await fetch("/api/live/host-feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          host_id: hostId(),
          workspace_id: workspaceId,
          action,
          sentence,
          current_product: currentProductName,
          source: "host_prompter"
        })
      }).catch(() => {});
      showHostToast("已记录执行。");
    }
    function showHostToast(message) {
      const node = document.getElementById("host-toast");
      if (!node) return;
      node.textContent = message;
      window.clearTimeout(window.__hostToastTimer);
      window.__hostToastTimer = window.setTimeout(() => { node.textContent = ""; }, 2500);
    }
    async function refreshBossIntervention() {
      const query = "?host_id=" + encodeURIComponent(hostId()) + (workspaceId ? "&workspace_id=" + encodeURIComponent(workspaceId) : "");
      const response = await fetch("/api/live/boss-intervention" + query).catch(() => null);
      if (!response) return;
      const data = await response.json().catch(() => ({}));
      const alert = document.getElementById("boss-alert");
      const messageNode = document.getElementById("boss-alert-message");
      const intervention = data.intervention;
      if (!alert) return;
      if (!intervention || !intervention.message) {
        alert.classList.remove("show");
        window.__bossInterventionMessage = "";
        return;
      }
      window.__bossInterventionMessage = intervention.message;
      if (messageNode) messageNode.textContent = "老板提醒：" + intervention.message;
      alert.classList.add("show");
    }
    async function ackBossIntervention() {
      const message = window.__bossInterventionMessage || "";
      if (!message) return;
      await fetch("/api/live/boss-intervention/ack", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ host_id: hostId(), workspace_id: workspaceId, message, acknowledged_by: "host_prompter" })
      }).catch(() => {});
      window.__bossInterventionMessage = "";
      const alert = document.getElementById("boss-alert");
      if (alert) alert.classList.remove("show");
      toast("已确认老板指令");
    }
    document.getElementById("connect-host").addEventListener("click", () => { setHostId(hostId()); refreshDecision(); });
    document.getElementById("copy-sentence").addEventListener("click", copyNextSentence);
    document.getElementById("mark-executed").addEventListener("click", markExecuted);
    document.getElementById("boss-alert-ack").addEventListener("click", ackBossIntervention);
    document.addEventListener("keydown", (event) => {
      const target = event.target && event.target.tagName ? event.target.tagName.toLowerCase() : "";
      if (target === "input" || target === "textarea") return;
      if (event.key.toLowerCase() === "c") copyNextSentence();
      if (event.key.toLowerCase() === "e") markExecuted();
    });
    setHostId(urlParams.get("host_id") || localStorage.getItem("ai_live_host_id") || "default");
    autoPickFreshHost().then(() => { refreshDecision(); refreshBossIntervention(); });
    window.setInterval(refreshDecision, 5000);
    window.setInterval(refreshBossIntervention, 5000);
    window.setInterval(renderTimer, 1000);
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
      <p>给主播电脑安装一次即可。当前最新插件版本：<b>0.1.2</b>。插件只捕获淘宝直播中控页里的实时数据响应，不收集淘宝密码，不做登录自动化；主播可在插件里填写老板/门店绑定码。</p>
      <a class="download" href="/download/chrome-extension">下载 Chrome 插件包</a>
      <p><a href="/live">打开主播控制台</a> · <a href="/live/prompter">打开主播大字提词器</a> · <a href="/">返回首页</a></p>
    </div>
    <div class="steps">
      <section class="step"><div><h2>下载并解压插件包</h2><p>点击上方下载，得到 zip 文件后先解压成文件夹。不要直接选择 zip。</p></div></section>
      <section class="step"><div><h2>打开 Chrome 扩展程序页面</h2><p>在 Chrome 地址栏输入 <code>chrome://extensions</code>，右上角打开 Developer Mode / 开发者模式。</p></div></section>
      <section class="step"><div><h2>加载插件文件夹</h2><p>点击 <code>Load unpacked</code> / 加载已解压的扩展程序，选择刚才解压出来的插件文件夹。</p></div></section>
      <section class="step"><div><h2>填写老板/门店绑定码</h2><p>点击插件图标，在“老板/门店绑定码”里填老板给主播的代码，例如 <code>boss-a</code> 或 <code>store-01</code>，这样数据会自动归到对应工作区。</p></div></section>
      <section class="step"><div><h2>打开淘宝直播中控</h2><p>主播登录自己的淘宝账号，打开 <code>liveplatform.taobao.com</code> 的实时直播中控页面。</p></div></section>
      <section class="step"><div><h2>点击插件并检查 4 步状态</h2><p>插件弹窗里看到“捕获实时接口”和“发送到云端系统”完成后，回到主播控制台。</p></div></section>
      <section class="step"><div><h2>进入主播控制台</h2><p>打开 <a href="/live">/live</a>。如果只有一个活跃直播间，系统会自动连接；多人同时直播时，选择对应 Host / room ID。</p></div></section>
      <section class="step"><div><h2>给主播打开大字提词器</h2><p>开播时建议把 <a href="/live/prompter">/live/prompter</a> 放在主播旁边屏幕，只显示“现在做什么”和“下一句怎么说”。</p></div></section>
    </div>
    <div class="note">如果没有正在直播，插件可能抓不到目标接口。这不是报错，可以先在 /live 使用“演示模式”培训主播。</div>
  </main>
</body>
</html>"""


def _render_workspace_onboarding(workspace_id: str) -> str:
    clean_workspace_id = "".join(char if char.isalnum() or char in "_.:-" else "-" for char in workspace_id.strip())[:48] or "default"
    safe_workspace_id = html.escape(clean_workspace_id)
    boss_url = f"/boss?workspace_id={safe_workspace_id}"
    live_url = f"/live?workspace_id={safe_workspace_id}"
    prompter_url = f"/live/prompter?workspace_id={safe_workspace_id}"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>客户交付页 - {safe_workspace_id}</title>
  <style>
    :root {{ color-scheme: light; --ink: #111827; --muted: #64706c; --line: #d8e1dd; --paper: #f5f8f6; --panel: #fff; --accent: #0c6b58; --accent-soft: #e0f1ea; --warn: #a16207; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--paper); color: var(--ink); }}
    main {{ max-width: 1080px; margin: 0 auto; padding: 32px 18px 56px; }}
    header {{ display: flex; justify-content: space-between; gap: 18px; align-items: flex-start; margin-bottom: 18px; }}
    h1 {{ margin: 0; font-size: clamp(32px, 5vw, 56px); line-height: 1.04; }}
    h2 {{ margin: 0 0 10px; font-size: 20px; }}
    p {{ color: var(--muted); line-height: 1.6; }}
    a {{ color: var(--accent); font-weight: 900; text-decoration: none; }}
    .hero, .panel, .link-card {{ background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 18px; }}
    .hero {{ display: grid; gap: 10px; margin-bottom: 14px; }}
    .code {{ display: inline-flex; align-items: center; border-radius: 8px; background: #0f172a; color: #fff; padding: 12px 14px; font-size: 28px; font-weight: 950; letter-spacing: 0; width: fit-content; }}
    .grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin: 14px 0; }}
    .link-card b {{ display: block; font-size: 18px; margin-bottom: 6px; }}
    .button {{ display: inline-flex; justify-content: center; border-radius: 8px; background: var(--accent); color: #fff; padding: 11px 13px; margin-top: 10px; }}
    .steps {{ display: grid; gap: 10px; counter-reset: step; }}
    .step {{ counter-increment: step; display: grid; grid-template-columns: 42px minmax(0, 1fr); gap: 12px; border: 1px solid var(--line); border-radius: 10px; padding: 14px; background: #fff; }}
    .step:before {{ content: counter(step); width: 36px; height: 36px; border-radius: 999px; display: grid; place-items: center; background: var(--accent-soft); color: var(--accent); font-weight: 950; }}
    .step h3 {{ margin: 0 0 4px; font-size: 17px; }}
    .note {{ border: 1px solid rgba(161, 98, 7, .28); background: #fffbeb; color: var(--warn); border-radius: 10px; padding: 14px; font-weight: 850; }}
    code {{ background: #edf4f1; border: 1px solid var(--line); border-radius: 6px; padding: 2px 6px; }}
    @media (max-width: 900px) {{ header {{ display: block; }} .grid {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>客户交付页</h1>
        <p>把这一页发给老板或主播。主播按绑定码安装插件，老板用专属看板看自己的直播间。</p>
      </div>
      <div><a href="/">返回工作台</a> · <a href="/install">插件教程</a></div>
    </header>
    <section class="hero">
      <h2>老板/门店绑定码</h2>
      <div class="code" id="workspace-code">{safe_workspace_id}</div>
      <p>主播 Chrome 插件里填写这个绑定码后，实时数据会进入该工作区。老板看板可以只显示这个绑定码下面的直播间。</p>
    </section>
    <section class="grid">
      <article class="link-card">
        <b>老板专属看板</b>
        <p>看 GMV、风险直播间、主播执行评分。</p>
        <a class="button" href="{boss_url}">打开老板看板</a>
      </article>
      <article class="link-card">
        <b>主播控制台</b>
        <p>主播/运营填写商品队列、评论、查看实时建议。</p>
        <a class="button" href="{live_url}">打开主播控制台</a>
      </article>
      <article class="link-card">
        <b>大字提词器</b>
        <p>直播时放在旁边屏幕，只看当前动作和下一句话。</p>
        <a class="button" href="{prompter_url}">打开大字提词器</a>
      </article>
    </section>
    <section class="panel">
      <h2>给主播的安装步骤</h2>
      <div class="steps">
        <section class="step"><div><h3>下载 Chrome 插件包</h3><p>先下载并解压插件包，不要直接选择 zip。</p><a class="button" href="/download/chrome-extension">下载插件包</a></div></section>
        <section class="step"><div><h3>加载插件</h3><p>打开 <code>chrome://extensions</code>，开启开发者模式，点击 Load unpacked / 加载已解压的扩展程序。</p></div></section>
        <section class="step"><div><h3>填写绑定码</h3><p>点击插件图标，在“老板/门店绑定码”里填写 <code>{safe_workspace_id}</code>，然后保存。</p></div></section>
        <section class="step"><div><h3>打开淘宝直播中控</h3><p>主播登录自己的淘宝账号，打开 <code>liveplatform.taobao.com</code>，进入实时直播中控页。</p></div></section>
        <section class="step"><div><h3>确认连接</h3><p>插件显示“捕获实时接口”和“发送到云端系统”成功后，老板看板会出现该直播间。</p></div></section>
      </div>
    </section>
    <p class="note">交付建议：每个老板或门店使用一个独立绑定码，例如 <code>brand-a</code>、<code>store-vancouver</code>。不要让不同客户共用同一个绑定码。</p>
  </main>
</body>
</html>"""


def _render_admin_live() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>直播监控后台</title>
  <style>
    :root { color-scheme: light; --ink: #16211f; --muted: #5b6764; --line: #d6dfdb; --paper: #f7f9f6; --panel: #fff; --accent: #0c6b58; --accent-soft: #e0f1ea; --warn: #a16207; --danger: #b42318; }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--paper); color: var(--ink); }
    main { max-width: 1200px; margin: 0 auto; padding: 28px 18px 56px; }
    header { display: flex; justify-content: space-between; gap: 18px; align-items: flex-end; margin-bottom: 18px; }
    h1 { margin: 0; font-size: 32px; }
    a { color: var(--accent); font-weight: 900; text-decoration: none; }
    .summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin-bottom: 14px; }
    .card, .room { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 14px; }
    .card span, .metric span { display: block; color: var(--muted); font-size: 12px; font-weight: 900; text-transform: uppercase; letter-spacing: 0; }
    .card b { display: block; font-size: 30px; margin-top: 4px; }
    .rooms { display: grid; gap: 12px; }
    .room-head { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; margin-bottom: 10px; }
    .room h2 { margin: 0; font-size: 20px; }
    .pill { display: inline-flex; align-items: center; border-radius: 999px; padding: 6px 9px; font-weight: 900; background: var(--accent-soft); color: var(--accent); white-space: nowrap; }
    .pill.warn { background: #fef3c7; color: var(--warn); }
    .pill.bad { background: #fee2e2; color: var(--danger); }
    .metrics { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 8px; }
    .metric { border: 1px solid var(--line); border-radius: 8px; padding: 10px; background: #fbfdfb; min-width: 0; }
    .metric b { display: block; font-size: 18px; overflow-wrap: anywhere; }
    .actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
    .button { display: inline-flex; border-radius: 8px; background: var(--accent); color: #fff; padding: 8px 10px; }
    .empty { background: var(--panel); border: 1px dashed var(--line); border-radius: 10px; padding: 26px; color: var(--muted); text-align: center; font-weight: 800; }
    .small { color: var(--muted); font-size: 13px; }
    @media (max-width: 900px) { .summary, .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); } header { display: block; } }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>直播监控后台</h1>
        <div class="small">查看所有主播插件连接状态和实时指标</div>
      </div>
      <div><a href="/live">主播控制台</a> · <a href="/install">插件安装</a> · <a href="/">返回首页</a></div>
    </header>
    <section class="summary">
      <div class="card"><span>活跃直播间</span><b id="active-count">--</b></div>
      <div class="card"><span>有效数据</span><b id="valid-count">--</b></div>
      <div class="card"><span>总 GMV</span><b id="total-gmv">--</b></div>
      <div class="card"><span>最近更新</span><b id="latest-update">--</b></div>
    </section>
    <section class="rooms" id="rooms"><div class="empty">等待插件数据...</div></section>
  </main>
  <script>
    function fmtNumber(value) {
      const numeric = Number(value || 0);
      return Number.isFinite(numeric) && numeric ? Math.round(numeric).toLocaleString("zh-CN") : "--";
    }
    function fmtMoney(value) {
      const numeric = Number(value || 0);
      return Number.isFinite(numeric) && numeric ? "¥" + Math.round(numeric).toLocaleString("zh-CN") : "--";
    }
    function fmtPercent(value) {
      const numeric = Number(value || 0);
      return Number.isFinite(numeric) && numeric ? (numeric * 100).toFixed(1) + "%" : "--";
    }
    function escapeHtml(value) {
      return String(value || "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
    }
    function ageText(age) {
      if (age === null || age === undefined) return "--";
      if (age < 60) return Math.round(age) + "s";
      return Math.round(age / 60) + "m";
    }
    function statusPill(session) {
      const age = Number(session.age_seconds || 9999);
      if (!session.valid_live_metrics) return '<span class="pill bad">无有效指标</span>';
      if (age > 45) return '<span class="pill warn">连接变慢 · ' + ageText(age) + '</span>';
      return '<span class="pill">在线 · ' + ageText(age) + '</span>';
    }
    async function refresh() {
      const response = await fetch("/api/live/sessions");
      const data = await response.json();
      const sessions = Array.isArray(data.sessions) ? data.sessions : [];
      const fresh = sessions.filter((item) => item.age_seconds === null || item.age_seconds <= 120);
      document.getElementById("active-count").textContent = fmtNumber(fresh.length);
      document.getElementById("valid-count").textContent = fmtNumber(fresh.filter((item) => item.valid_live_metrics).length);
      document.getElementById("total-gmv").textContent = fmtMoney(fresh.reduce((sum, item) => sum + Number(item.pay_amt || 0), 0));
      document.getElementById("latest-update").textContent = fresh.length ? ageText(fresh[0].age_seconds) + " ago" : "--";
      const node = document.getElementById("rooms");
      if (!sessions.length) {
        node.innerHTML = '<div class="empty">暂无直播间数据。让主播打开淘宝直播中控页并启用 Chrome 插件。</div>';
        return;
      }
      node.innerHTML = sessions.slice(0, 20).map((session) => {
        const hostId = session.host_id || "default";
        const liveUrl = "/live?host_id=" + encodeURIComponent(hostId);
        const detailUrl = "/admin/live/" + encodeURIComponent(hostId);
        const metrics = [
          ["总观看", fmtNumber(session.total_viewers)],
          ["在线", fmtNumber(session.online_uv)],
          ["GMV", fmtMoney(session.pay_amt)],
          ["热度", fmtNumber(session.heat_score)],
          ["CTR", fmtPercent(session.ipv_uv_rate)],
          ["CVR", fmtPercent(session.pay_byr_rate)],
          ["当前商品", session.current_product || "--"],
          ["最新动作", session.current_action || "--"],
          ["快照数", fmtNumber(session.snapshot_count)],
          ["商品级数据", session.product_level_connected ? "已连接" : "未连接"],
          ["插件版本", (session.extension_version || "--") + (session.extension_update_available ? " · 需更新" : "")],
          ["来源", session.source || "--"],
          ["Live ID", session.live_id || "--"]
        ].map((item) => '<div class="metric"><span>' + item[0] + '</span><b>' + escapeHtml(item[1]) + '</b></div>').join("");
        return '<article class="room">'
          + '<div class="room-head"><div><h2>' + escapeHtml(session.display_name || session.host_id || "default") + '</h2><div class="small">Host ID: ' + escapeHtml(session.host_id || "default") + ' · Last updated: ' + ageText(session.age_seconds) + ' ago</div></div>' + statusPill(session) + '</div>'
          + '<div class="metrics">' + metrics + '</div>'
          + '<div class="actions"><a class="button" href="' + liveUrl + '">打开这个直播间</a><a class="button" href="' + detailUrl + '">查看趋势/导出</a></div>'
          + '</article>';
      }).join("");
    }
    refresh();
    window.setInterval(refresh, 5000);
  </script>
</body>
</html>"""


def _render_boss_dashboard() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>老板总控看板</title>
  <style>
    :root { color-scheme: light; --ink: #111827; --muted: #66736f; --line: #d8e1dd; --paper: #f5f8f6; --panel: #fff; --accent: #0c6b58; --accent-soft: #e0f1ea; --warn: #a16207; --danger: #b42318; }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--paper); color: var(--ink); }
    main { max-width: 1280px; margin: 0 auto; padding: 28px 18px 56px; }
    header { display: flex; justify-content: space-between; align-items: flex-end; gap: 18px; margin-bottom: 18px; }
    h1 { margin: 0; font-size: 34px; }
    a { color: var(--accent); text-decoration: none; font-weight: 900; }
    .small { color: var(--muted); font-size: 13px; }
    .kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin-bottom: 14px; }
    .card, .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 14px; }
    .card span { display: block; color: var(--muted); font-size: 12px; font-weight: 900; text-transform: uppercase; }
    .card b { display: block; font-size: 34px; margin-top: 4px; }
    .layout { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
    .risk { border: 1px solid var(--line); border-left: 6px solid var(--warn); border-radius: 8px; padding: 12px; background: #fff; margin-top: 8px; }
    .risk.high { border-left-color: var(--danger); }
    .risk b { display: block; font-size: 18px; }
    table { width: 100%; border-collapse: collapse; min-width: 780px; }
    th, td { border-bottom: 1px solid var(--line); padding: 9px; text-align: left; font-size: 13px; }
    th { color: var(--muted); font-size: 12px; }
    .table-wrap { overflow-x: auto; }
    .score { font-weight: 950; color: var(--accent); }
    .button { display: inline-flex; border-radius: 8px; background: var(--accent); color: #fff; padding: 7px 9px; }
    .workspace-bar { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; gap: 8px; align-items: center; margin-bottom: 14px; }
    .intervention-buttons { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
    .intervention-buttons button { padding: 7px 8px; font-size: 12px; background: #e7efeb; color: var(--accent); }
    input, select { border: 1px solid var(--line); border-radius: 8px; padding: 10px; font: inherit; background: #fff; min-width: 0; }
    button { border: 0; border-radius: 8px; background: var(--accent); color: #fff; padding: 10px 12px; font-weight: 900; cursor: pointer; }
    @media (max-width: 900px) { .kpis, .layout { grid-template-columns: 1fr; } header { display: block; } }
    @media (max-width: 720px) { .workspace-bar { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <main>
    <header>
      <div><h1>老板总控看板</h1><div class="small">实时看钱、看风险、看主播执行力</div></div>
      <div><a href="/admin/live">直播监控后台</a> · <a href="/live">主播控制台</a> · <a href="/">返回首页</a></div>
    </header>
    <section class="workspace-bar">
      <input id="workspace-input" placeholder="输入老板/门店绑定码；留空查看全部">
      <select id="workspace-select"><option value="">全部工作区</option></select>
      <button id="apply-workspace" type="button">切换工作区</button>
    </section>
    <section class="kpis">
      <div class="card"><span>在线直播间</span><b id="active-count">--</b></div>
      <div class="card"><span>有效数据</span><b id="valid-count">--</b></div>
      <div class="card"><span>当前 GMV</span><b id="total-gmv">--</b></div>
      <div class="card"><span>风险直播间</span><b id="risk-count">--</b></div>
    </section>
    <section class="layout">
      <div class="panel">
        <h2>老板需要关注</h2>
        <div id="risk-list"><div class="small">等待数据...</div></div>
      </div>
      <div class="panel">
        <h2>今日最佳直播间</h2>
        <div id="top-room" class="small">等待数据...</div>
      </div>
    </section>
    <section class="panel" style="margin-top:14px;">
      <h2>主播执行评分</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>直播间</th><th>评分</th><th>执行</th><th>GMV</th><th>在线</th><th>CTR</th><th>CVR</th><th>最新动作</th><th>操作</th></tr></thead>
          <tbody id="room-rows"><tr><td colspan="9">等待数据...</td></tr></tbody>
        </table>
      </div>
    </section>
  </main>
  <script>
    const params = new URLSearchParams(location.search);
    let workspaceId = params.get("workspace_id") || "";
    function fmtNumber(value) { const numeric = Number(value || 0); return Number.isFinite(numeric) && numeric ? Math.round(numeric).toLocaleString("zh-CN") : "--"; }
    function fmtMoney(value) { const numeric = Number(value || 0); return Number.isFinite(numeric) && numeric ? "¥" + Math.round(numeric).toLocaleString("zh-CN") : "--"; }
    function fmtPercent(value) { const numeric = Number(value || 0); return Number.isFinite(numeric) && numeric ? (numeric * 100).toFixed(1) + "%" : "--"; }
    function escapeHtml(value) { return String(value || "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char])); }
    async function refresh() {
      const query = workspaceId ? "?workspace_id=" + encodeURIComponent(workspaceId) : "";
      const response = await fetch("/api/boss/dashboard" + query);
      const data = await response.json();
      renderWorkspaceOptions(data.workspace_options || []);
      document.getElementById("active-count").textContent = fmtNumber(data.active_count);
      document.getElementById("valid-count").textContent = fmtNumber(data.valid_count);
      document.getElementById("total-gmv").textContent = fmtMoney(data.total_gmv);
      document.getElementById("risk-count").textContent = fmtNumber(data.risk_count);
      renderRisks(data.risk_rooms || []);
      renderTopRoom(data.top_room);
      renderRooms(data.rooms || []);
    }
    function renderWorkspaceOptions(options) {
      const input = document.getElementById("workspace-input");
      const select = document.getElementById("workspace-select");
      if (document.activeElement !== input) input.value = workspaceId;
      const unique = Array.from(new Set(options.filter(Boolean)));
      const all = ["", ...unique.filter((item) => item !== "default")];
      select.innerHTML = all.map((item) => '<option value="' + escapeHtml(item) + '"' + (item === workspaceId ? " selected" : "") + '>' + (item ? escapeHtml(item) : "全部工作区") + '</option>').join("");
    }
    function renderRisks(risks) {
      const node = document.getElementById("risk-list");
      if (!risks.length) { node.innerHTML = '<div class="small">当前没有高风险直播间。</div>'; return; }
      node.innerHTML = risks.map((risk) => '<div class="risk ' + (risk.level === "high" ? "high" : "") + '"><b>' + escapeHtml(risk.display_name) + '</b><div>' + escapeHtml(risk.reason) + '</div><div class="small">建议：' + escapeHtml(risk.action) + '</div><div class="small">预计机会损失：' + fmtMoney(risk.estimated_loss) + '</div></div>').join("");
    }
    function renderTopRoom(room) {
      const node = document.getElementById("top-room");
      if (!room) { node.textContent = "等待数据..."; return; }
      node.innerHTML = '<h3>' + escapeHtml(room.display_name || room.host_id) + '</h3><p>当前 GMV：<b>' + fmtMoney(room.pay_amt) + '</b></p><p>在线：' + fmtNumber(room.online_uv) + ' · CTR ' + fmtPercent(room.ipv_uv_rate) + ' · CVR ' + fmtPercent(room.pay_byr_rate) + '</p><p class="small">近 5 分钟执行：' + fmtNumber(room.host_feedback_count_5m) + ' 次</p>';
    }
    function renderRooms(rooms) {
      const node = document.getElementById("room-rows");
      if (!rooms.length) { node.innerHTML = '<tr><td colspan="9">等待数据...</td></tr>'; return; }
      node.innerHTML = rooms.map((room) => {
        const url = "/admin/live/" + encodeURIComponent(room.host_id);
        const feedback = fmtNumber(room.host_feedback_count_5m) + '次 / 5m' + (room.last_host_feedback_action ? '<div class="small">' + escapeHtml(room.last_host_feedback_action) + '</div>' : '');
        const pending = room.pending_boss_intervention ? '<div class="small" style="color:#9f2f22;font-weight:950;">待主播确认：' + escapeHtml(room.boss_intervention_message || "") + '</div>' : '<div class="small">老板指令：已同步</div>';
        const buttons = '<div class="intervention-buttons">'
          + '<button data-host-id="' + escapeHtml(room.host_id) + '" data-workspace-id="' + escapeHtml(room.workspace_id || "") + '" data-message="开始讲尺码，直接回答身高体重。">讲尺码</button>'
          + '<button data-host-id="' + escapeHtml(room.host_id) + '" data-workspace-id="' + escapeHtml(room.workspace_id || "") + '" data-message="解释价格价值，别急着换款。">讲价格</button>'
          + '<button data-host-id="' + escapeHtml(room.host_id) + '" data-workspace-id="' + escapeHtml(room.workspace_id || "") + '" data-message="当前款收口，准备切下一件。">切品</button>'
          + '</div>';
        return '<tr><td>' + escapeHtml(room.display_name) + '<div class="small">' + escapeHtml(room.workspace_id || "default") + '</div></td><td class="score">' + fmtNumber(room.execution_score) + '</td><td>' + feedback + pending + '</td><td>' + fmtMoney(room.pay_amt) + '</td><td>' + fmtNumber(room.online_uv) + '</td><td>' + fmtPercent(room.ctr) + '</td><td>' + fmtPercent(room.cvr) + '</td><td>' + escapeHtml(room.current_action || "--") + '</td><td><a class="button" href="' + url + '">详情</a>' + buttons + '</td></tr>';
      }).join("");
      node.querySelectorAll(".intervention-buttons button").forEach((button) => {
        button.addEventListener("click", () => sendIntervention(button.dataset.hostId, button.dataset.workspaceId, button.dataset.message));
      });
    }
    async function sendIntervention(hostId, workspaceIdValue, message) {
      await fetch("/api/live/boss-intervention", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ host_id: hostId, workspace_id: workspaceIdValue, message, created_by: "boss_dashboard" })
      }).catch(() => {});
      refresh();
    }
    document.getElementById("apply-workspace").addEventListener("click", () => {
      workspaceId = document.getElementById("workspace-input").value.trim();
      history.replaceState(null, "", workspaceId ? "/boss?workspace_id=" + encodeURIComponent(workspaceId) : "/boss");
      refresh();
    });
    document.getElementById("workspace-select").addEventListener("change", (event) => {
      workspaceId = event.target.value;
      document.getElementById("workspace-input").value = workspaceId;
      history.replaceState(null, "", workspaceId ? "/boss?workspace_id=" + encodeURIComponent(workspaceId) : "/boss");
      refresh();
    });
    refresh();
    window.setInterval(refresh, 5000);
  </script>
</body>
</html>"""


def _render_admin_live_detail(host_id: str) -> str:
    safe_host_id = html.escape(host_id)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>直播间趋势 - {safe_host_id}</title>
  <style>
    :root {{ color-scheme: light; --ink: #16211f; --muted: #5b6764; --line: #d6dfdb; --paper: #f7f9f6; --panel: #fff; --accent: #0c6b58; --accent-soft: #e0f1ea; --warn: #a16207; --danger: #b42318; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--paper); color: var(--ink); }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 28px 18px 56px; }}
    header {{ display: flex; justify-content: space-between; gap: 18px; align-items: flex-end; margin-bottom: 18px; }}
    h1 {{ margin: 0; font-size: 30px; }}
    a {{ color: var(--accent); font-weight: 900; text-decoration: none; }}
    .grid {{ display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 10px; margin-bottom: 14px; }}
    .card, .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 14px; }}
    .card span {{ display: block; color: var(--muted); font-size: 12px; font-weight: 900; text-transform: uppercase; letter-spacing: 0; }}
    .card b {{ display: block; font-size: 24px; margin-top: 4px; overflow-wrap: anywhere; }}
    .panel {{ margin-top: 14px; }}
    .chart {{ display: grid; grid-template-columns: repeat(60, minmax(3px, 1fr)); gap: 3px; align-items: end; min-height: 170px; padding: 12px; border: 1px solid var(--line); border-radius: 8px; background: #fbfdfb; }}
    .bar {{ min-height: 2px; background: var(--accent); border-radius: 4px 4px 0 0; }}
    .bar.pay {{ background: #b7791f; }}
    .bar.heat {{ background: #2563eb; }}
    .chart-tabs {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }}
    button, .button {{ border: 0; border-radius: 8px; background: var(--accent); color: #fff; padding: 8px 10px; font-weight: 900; cursor: pointer; display: inline-flex; }}
    button.secondary {{ background: #e7efeb; color: var(--accent); }}
    button.active {{ background: var(--accent); color: #fff; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; min-width: 860px; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 9px; text-align: left; font-size: 13px; }}
    th {{ color: var(--muted); font-size: 12px; }}
    .table-wrap {{ overflow-x: auto; border: 1px solid var(--line); border-radius: 8px; }}
    .timeline {{ display: grid; gap: 8px; }}
    .timeline-item {{ border: 1px solid var(--line); border-left: 5px solid var(--accent); border-radius: 8px; padding: 10px; background: #fbfdfb; }}
    .summary-list {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }}
    .summary-box {{ border: 1px solid var(--line); border-radius: 8px; padding: 12px; background: #fbfdfb; }}
    .summary-box h3 {{ margin: 0 0 8px; font-size: 14px; color: var(--accent); }}
    .summary-box ul {{ margin: 0; padding-left: 18px; }}
    .small {{ color: var(--muted); font-size: 13px; }}
    @media (max-width: 900px) {{ .grid, .summary-list {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} header {{ display: block; }} }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>直播间趋势</h1>
        <div class="small">Host ID: <b id="host-id">{safe_host_id}</b></div>
      </div>
      <div><a href="/live?host_id={safe_host_id}">打开主播控制台</a> · <a href="/admin/live">返回监控后台</a> · <a href="/admin/live/{safe_host_id}/export.csv">导出 CSV</a></div>
    </header>
    <section class="grid">
      <div class="card"><span>在线</span><b id="online-uv">--</b></div>
      <div class="card"><span>总观看</span><b id="total-viewers">--</b></div>
      <div class="card"><span>GMV</span><b id="pay-amt">--</b></div>
      <div class="card"><span>热度</span><b id="heat-score">--</b></div>
      <div class="card"><span>最新动作</span><b id="current-action">--</b></div>
    </section>
    <section class="panel">
      <div class="chart-tabs">
        <button class="active" data-chart="online_uv">在线</button>
        <button class="secondary" data-chart="pay_amt">GMV</button>
        <button class="secondary" data-chart="heat_score">热度</button>
        <button class="secondary" data-chart="ipv_uv_rate">CTR</button>
        <button class="secondary" data-chart="pay_byr_rate">CVR</button>
      </div>
      <div class="chart" id="chart"></div>
      <div class="small" id="chart-label" style="margin-top:8px;">最近快照趋势</div>
    </section>
    <section class="panel">
      <h2>自动复盘摘要</h2>
      <div id="post-live-headline" style="font-size:22px;font-weight:950;color:var(--accent);">等待数据...</div>
      <div class="summary-list" style="margin-top:12px;">
        <div class="summary-box"><h3>亮点</h3><ul id="summary-highlights"><li>等待数据...</li></ul></div>
        <div class="summary-box"><h3>风险</h3><ul id="summary-risks"><li>等待数据...</li></ul></div>
        <div class="summary-box"><h3>下场建议</h3><ul id="summary-suggestions"><li>等待数据...</li></ul></div>
      </div>
      <div class="small" style="margin-top:10px;">最佳时刻：<b id="summary-best">--</b> · 观察点：<b id="summary-weak">--</b></div>
    </section>
    <section class="panel">
      <h2>AI 动作时间线</h2>
      <div class="timeline" id="timeline"><div class="timeline-item">等待动作...</div></div>
    </section>
    <section class="panel">
      <h2>最近快照</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>时间</th><th>商品</th><th>在线</th><th>总观看</th><th>GMV</th><th>热度</th><th>CTR</th><th>CVR</th><th>停留</th><th>评论</th></tr></thead>
          <tbody id="snapshot-rows"><tr><td colspan="10">等待数据...</td></tr></tbody>
        </table>
      </div>
    </section>
  </main>
  <script>
    const hostId = {json.dumps(host_id)};
    let chartField = "online_uv";
    function fmtNumber(value) {{ const numeric = Number(value || 0); return Number.isFinite(numeric) && numeric ? Math.round(numeric).toLocaleString("zh-CN") : "--"; }}
    function fmtMoney(value) {{ const numeric = Number(value || 0); return Number.isFinite(numeric) && numeric ? "¥" + Math.round(numeric).toLocaleString("zh-CN") : "--"; }}
    function fmtPercent(value) {{ const numeric = Number(value || 0); return Number.isFinite(numeric) && numeric ? (numeric * 100).toFixed(1) + "%" : "--"; }}
    function escapeHtml(value) {{ return String(value || "").replace(/[&<>"']/g, (char) => ({{ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }}[char])); }}
    function timeText(ts) {{ return ts ? new Date(ts * 1000).toLocaleTimeString("zh-CN", {{ hour12: false }}) : "--"; }}
    async function refresh() {{
      const response = await fetch("/api/live/history?host_id=" + encodeURIComponent(hostId));
      const data = await response.json();
      const snapshots = Array.isArray(data.snapshots) ? data.snapshots : [];
      const summary = data.summary || {{}};
      const latest = snapshots[snapshots.length - 1] || {{}};
      document.querySelector("h1").textContent = summary.display_name || "直播间趋势";
      document.getElementById("online-uv").textContent = fmtNumber(latest.online_uv || summary.online_uv);
      document.getElementById("total-viewers").textContent = fmtNumber(latest.total_viewers || summary.total_viewers);
      document.getElementById("pay-amt").textContent = fmtMoney(latest.pay_amt || summary.pay_amt);
      document.getElementById("heat-score").textContent = fmtNumber(latest.heat_score || summary.heat_score);
      document.getElementById("current-action").textContent = summary.current_action || "--";
      renderChart(snapshots);
      renderSummary(data.post_live_summary || {{}});
      renderTimeline(data.actions || []);
      renderRows(snapshots.slice(-40).reverse());
    }}
    function renderChart(snapshots) {{
      const node = document.getElementById("chart");
      const values = snapshots.slice(-60).map((item) => Number(item[chartField] || 0));
      const maxValue = Math.max(...values, 1);
      node.innerHTML = values.map((value) => {{
        const height = Math.max(2, Math.round((value / maxValue) * 150));
        const cls = chartField === "pay_amt" ? "bar pay" : chartField === "heat_score" ? "bar heat" : "bar";
        return '<div class="' + cls + '" style="height:' + height + 'px" title="' + value + '"></div>';
      }}).join("") || '<div class="small">暂无趋势数据</div>';
      document.getElementById("chart-label").textContent = "当前指标: " + chartField + " · 快照数: " + snapshots.length;
    }}
    function renderTimeline(actions) {{
      const node = document.getElementById("timeline");
      if (!actions.length) {{ node.innerHTML = '<div class="timeline-item">等待动作...</div>'; return; }}
      node.innerHTML = actions.slice(0, 10).map((item) => '<div class="timeline-item"><b>' + timeText(item.timestamp) + ' · ' + escapeHtml(item.decision || "--") + '</b><div class="small">' + escapeHtml((item.reason || []).join(" / ")) + '</div><div>' + escapeHtml(item.next_action || "--") + '</div></div>').join("");
    }}
    function renderSummary(summary) {{
      document.getElementById("post-live-headline").textContent = summary.headline || "暂无可复盘数据";
      document.getElementById("summary-best").textContent = summary.best_moment || "--";
      document.getElementById("summary-weak").textContent = summary.weak_moment || "--";
      renderList("summary-highlights", summary.highlights || []);
      renderList("summary-risks", summary.risks || []);
      renderList("summary-suggestions", summary.next_suggestions || []);
    }}
    function renderList(id, items) {{
      const node = document.getElementById(id);
      node.innerHTML = (items.length ? items : ["等待数据..."]).map((item) => '<li>' + escapeHtml(item) + '</li>').join("");
    }}
    function renderRows(rows) {{
      const node = document.getElementById("snapshot-rows");
      if (!rows.length) {{ node.innerHTML = '<tr><td colspan="10">等待数据...</td></tr>'; return; }}
      node.innerHTML = rows.map((row) => '<tr><td>' + timeText(row.timestamp) + '</td><td>' + escapeHtml(row.current_product || "--") + '</td><td>' + fmtNumber(row.online_uv) + '</td><td>' + fmtNumber(row.total_viewers) + '</td><td>' + fmtMoney(row.pay_amt) + '</td><td>' + fmtNumber(row.heat_score) + '</td><td>' + fmtPercent(row.ipv_uv_rate) + '</td><td>' + fmtPercent(row.pay_byr_rate) + '</td><td>' + (row.stay_time_pu ? Math.round(row.stay_time_pu) + "s" : "--") + '</td><td>' + fmtNumber(row.comment_uv) + '</td></tr>').join("");
    }}
    document.querySelectorAll("[data-chart]").forEach((button) => {{
      button.addEventListener("click", () => {{
        chartField = button.getAttribute("data-chart");
        document.querySelectorAll("[data-chart]").forEach((item) => item.className = "secondary");
        button.className = "active";
        refresh();
      }});
    }});
    refresh();
    window.setInterval(refresh, 5000);
  </script>
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
      <p><a href="/live">打开主播控制台</a> · <a href="/admin/live">直播监控后台</a> · <a href="/install">插件安装教程</a> · <a href="/reports/{html.escape(report_id)}">查看保存版本</a> · <a href="/reports/{html.escape(report_id)}/export">下载 HTML</a> · <a href="/reports">历史报告</a> · <a href="/download/chrome-extension">下载 Chrome 插件包</a></p>
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

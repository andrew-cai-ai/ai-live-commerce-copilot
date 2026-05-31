from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class GateCheck:
    name: str
    status: str
    detail: str


@dataclass(frozen=True)
class GateResult:
    decision: str
    score: int
    reason: str
    checks: list[GateCheck]
    next_action: str


def capability_surface() -> dict[str, list[dict[str, str]]]:
    """ECC agent-sort surface for the live-commerce assistant.

    DAILY capabilities stay in the normal live workflow. LIBRARY capabilities are
    useful, but should be invoked only when a product/session deserves deeper work.
    """
    return {
        "daily": [
            {
                "key": "excel_import_gate",
                "name": "Excel Import Gate",
                "why": "Block weak inventory imports before they become live-room recommendations.",
            },
            {
                "key": "product_pool_matching",
                "name": "Product Pool Matching",
                "why": "Map Taobao live titles back to Ashley's Excel product pool.",
            },
            {
                "key": "live_connector_gate",
                "name": "Chrome Connector Gate",
                "why": "Tell the host whether page capture, parsing, and ingest are actually working.",
            },
            {
                "key": "director_decision_loop",
                "name": "Director Decision Loop",
                "why": "Turn live metrics into one host-readable next action.",
            },
            {
                "key": "training_quality_gate",
                "name": "Training Quality Gate",
                "why": "Use only clean host/action/effect samples for model training.",
            },
        ],
        "library": [
            {
                "key": "deep_market_research",
                "name": "Deep Market Research",
                "why": "Run only for products that pass the daily selection gate.",
            },
            {
                "key": "competitor_price_audit",
                "name": "Competitor Price Audit",
                "why": "Validate Taobao/Dewu/Xiaohongshu pricing before a major live push.",
            },
            {
                "key": "host_training_replay",
                "name": "Host Training Replay",
                "why": "Review a session after the stream, not during high-pressure live selling.",
            },
            {
                "key": "chrome_payload_forensics",
                "name": "Chrome Payload Forensics",
                "why": "Debug Taobao API changes when the connector sees unknown payloads.",
            },
        ],
    }


def inventory_quality_gate(rows: list[Any]) -> dict[str, Any]:
    rows = [row for row in rows if _text(_pick(row, "product_name"))]
    if not rows:
        return asdict(GateResult(
            decision="Blocked",
            score=0,
            reason="No valid products were imported.",
            checks=[GateCheck("Product Coverage", "Fail", "0 valid product rows.")],
            next_action="Fix the Excel file before generating a live report.",
        ))

    total = len(rows)
    sku_ratio = _ratio(rows, lambda row: bool(_text(_pick(row, "sku"))))
    target_ratio = _ratio(rows, lambda row: _pick(row, "target_price") is not None)
    cost_ratio = _ratio(rows, lambda row: _pick(row, "cost_price") is not None)
    notes_ratio = _ratio(rows, lambda row: bool(_text(_pick(row, "notes"))))
    inventory_ratio = _ratio(rows, lambda row: _pick(row, "inventory") is not None)
    stock_note_ratio = _ratio(rows, lambda row: _has_stock_evidence(_text(_pick(row, "notes"))))

    checks = [
        _coverage_check("Product Coverage", total, pass_at=5, warn_at=1, unit="rows"),
        _ratio_check("SKU Coverage", sku_ratio, pass_at=0.8, warn_at=0.5),
        _ratio_check("Target Price Coverage", target_ratio, pass_at=0.8, warn_at=0.5),
        _ratio_check("Cost Confidence", cost_ratio, pass_at=0.7, warn_at=0.4),
        _ratio_check("Livestream Notes", notes_ratio, pass_at=0.6, warn_at=0.3),
    ]
    if inventory_ratio >= 0.6:
        checks.append(_check("Inventory Evidence", "Pass", f"{_pct(inventory_ratio)} rows have numeric inventory."))
    elif stock_note_ratio >= 0.6:
        checks.append(_check("Inventory Evidence", "Warn", f"{_pct(stock_note_ratio)} rows use stock/update notes instead of numeric inventory."))
    else:
        checks.append(_check("Inventory Evidence", "Warn", "Inventory is mostly unknown; keep live recommendations conservative."))

    score = 0
    score += 20 if total >= 5 else 12
    score += _weighted_score(sku_ratio, 20)
    score += _weighted_score(target_ratio, 20)
    score += _weighted_score(cost_ratio, 18)
    score += _weighted_score(notes_ratio, 14)
    score += 8 if inventory_ratio >= 0.6 else 6 if stock_note_ratio >= 0.6 else 2
    score = min(100, round(score))

    has_fail = any(check.status == "Fail" for check in checks)
    if score >= 80 and not has_fail:
        decision = "Promote"
        reason = "Inventory is strong enough to feed the live product pool."
        next_action = "Generate the product report and bind the Chrome extension workspace before going live."
    elif score >= 55:
        decision = "Watchlist"
        reason = "Inventory is usable, but missing fields should be fixed before an important stream."
        next_action = "Preview the rows, fix missing SKU/price/cost fields, then regenerate."
    else:
        decision = "Blocked"
        reason = "Inventory evidence is too weak for reliable live-room recommendations."
        next_action = "Fix product names, target prices, and SKU/cost fields before generating."

    return asdict(GateResult(decision=decision, score=score, reason=reason, checks=checks, next_action=next_action))


def extension_link_gate(status: dict[str, Any]) -> dict[str, Any]:
    checks = [
        _bool_check("Taobao Page", bool(status.get("activeTabMatches")), "Current tab matches Taobao/Tmall live surfaces."),
        _bool_check(
            "Script Injection",
            bool(status.get("contentScriptInjected")) and bool(status.get("pageHookInjected") or status.get("pageHookScriptLoaded")),
            "Content script and page hook are loaded.",
        ),
        _bool_check(
            "Live Capture",
            bool(status.get("capturedTargetApi")) or bool(status.get("domFallbackCaptured")),
            "Target API or DOM fallback produced live metrics.",
        ),
        _bool_check("Payload Parse", bool(status.get("lastParseSuccess")), "Payload parsed successfully."),
        _bool_check("Backend Ingest", bool(status.get("lastSendSuccess")), "Payload reached FastAPI ingest endpoint."),
    ]
    workspace = _text(status.get("workspaceId") or status.get("workspace_id"))
    if workspace:
        checks.append(_check("Workspace Binding", "Pass", f"Workspace: {workspace}."))
    else:
        checks.append(_check("Workspace Binding", "Warn", "No workspace binding code; data goes to default."))

    score = sum(18 if check.status == "Pass" else 8 if check.status == "Warn" else 0 for check in checks)
    score = min(100, score)
    has_fail = any(check.status == "Fail" for check in checks)
    if score >= 90 and not has_fail:
        decision = "Ready"
        reason = "The live connector is ready for a real run."
        next_action = "Start or continue the live validation run."
    elif score >= 55:
        decision = "Partial"
        reason = "The connector is partially working, but one or more gates still need attention."
        next_action = "Fix the first failed connector step before trusting fine-grained director decisions."
    else:
        decision = "Blocked"
        reason = "The live connector is not ready."
        next_action = "Open Taobao live console, inject the script, capture metrics, then verify ingest."
    return asdict(GateResult(decision=decision, score=score, reason=reason, checks=checks, next_action=next_action))


def _pick(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _ratio(rows: list[Any], predicate: Any) -> float:
    return sum(1 for row in rows if predicate(row)) / max(len(rows), 1)


def _has_stock_evidence(notes: str) -> bool:
    text = notes.lower()
    return any(term in text for term in ("库存", "stock", "erp", "官网", "奥莱", "断货", "更新库存"))


def _check(name: str, status: str, detail: str) -> GateCheck:
    return GateCheck(name=name, status=status, detail=detail)


def _bool_check(name: str, ok: bool, detail: str) -> GateCheck:
    return _check(name, "Pass" if ok else "Fail", detail if ok else f"Missing: {detail}")


def _coverage_check(name: str, count: int, pass_at: int, warn_at: int, unit: str) -> GateCheck:
    if count >= pass_at:
        return _check(name, "Pass", f"{count} {unit}.")
    if count >= warn_at:
        return _check(name, "Warn", f"Only {count} {unit}.")
    return _check(name, "Fail", f"{count} {unit}.")


def _ratio_check(name: str, ratio: float, pass_at: float, warn_at: float) -> GateCheck:
    if ratio >= pass_at:
        return _check(name, "Pass", f"{_pct(ratio)} coverage.")
    if ratio >= warn_at:
        return _check(name, "Warn", f"{_pct(ratio)} coverage.")
    return _check(name, "Fail", f"{_pct(ratio)} coverage.")


def _weighted_score(ratio: float, weight: int) -> float:
    return min(1.0, max(0.0, ratio)) * weight


def _pct(ratio: float) -> str:
    return f"{round(ratio * 100)}%"

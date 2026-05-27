from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

REPORT_DIR = Path("data/reports")


@dataclass(frozen=True)
class ReportRecord:
    report_id: str
    title: str
    created_at: float
    product_count: int
    html_path: Path


def save_report(html: str, product_names: list[str]) -> ReportRecord:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    created_at = time.time()
    report_id = time.strftime("%Y%m%d-%H%M%S", time.localtime(created_at))
    title = _title_from_products(product_names)
    html_path = REPORT_DIR / f"{report_id}.html"
    meta_path = REPORT_DIR / f"{report_id}.json"
    html_path.write_text(html, encoding="utf-8")
    meta_path.write_text(
        json.dumps(
            {
                "report_id": report_id,
                "title": title,
                "created_at": created_at,
                "product_count": len(product_names),
                "html_path": str(html_path),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return ReportRecord(report_id, title, created_at, len(product_names), html_path)


def list_reports(limit: int = 30) -> list[ReportRecord]:
    if not REPORT_DIR.exists():
        return []
    records = []
    for meta_path in REPORT_DIR.glob("*.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        report_id = str(meta.get("report_id") or meta_path.stem)
        if not _valid_report_id(report_id):
            continue
        html_path = REPORT_DIR / f"{report_id}.html"
        if not html_path.exists():
            continue
        records.append(
            ReportRecord(
                report_id=report_id,
                title=str(meta.get("title") or report_id),
                created_at=float(meta.get("created_at") or 0),
                product_count=int(meta.get("product_count") or 0),
                html_path=html_path,
            )
        )
    return sorted(records, key=lambda record: record.created_at, reverse=True)[:limit]


def get_report_path(report_id: str) -> Path | None:
    if not _valid_report_id(report_id):
        return None
    path = REPORT_DIR / f"{report_id}.html"
    return path if path.exists() else None


def _title_from_products(product_names: list[str]) -> str:
    if not product_names:
        return "空报告"
    first = product_names[0]
    if len(product_names) == 1:
        return first
    return f"{first} 等 {len(product_names)} 件商品"


def _valid_report_id(report_id: str) -> bool:
    return bool(re.fullmatch(r"\d{8}-\d{6}", report_id))

"""Input adapter: kickoff-kit counterparty file -> Counterparty rows. Owner: Arth (P1).

Accepts CSV / JSON / XLSX (xlsx needs openpyxl). Bad rows are reported, never fatal.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from typing import Any

from crd import db
from crd.models import Counterparty, Role

ALIASES = {
    "id": "cp_id", "cpid": "cp_id", "counterparty_id": "cp_id", "code": "cp_id", "vendor_id": "cp_id",
    "customer_id": "cp_id", "supplier_id": "cp_id",
    "company": "name", "company_name": "name", "counterparty": "name", "vendor": "name", "customer": "name",
    "supplier": "name", "party": "name", "party_name": "name", "trade_name": "name", "brand": "name",
    "registered_name": "legal_name", "legal": "legal_name",
    "website": "domain", "url": "domain", "web": "domain", "site": "domain",
    "gst": "gstin", "gst_no": "gstin", "gst_number": "gstin", "gstin_no": "gstin",
    "cin_no": "cin", "company_cin": "cin",
    "type": "role", "category": "role", "relationship": "role", "counterparty_type": "role",
    "outstanding": "exposure_inr", "receivable": "exposure_inr", "receivables": "exposure_inr",
    "exposure": "exposure_inr", "monthly_spend": "exposure_inr", "spend": "exposure_inr", "amount": "exposure_inr",
    "terms": "credit_terms_days", "credit_terms": "credit_terms_days", "payment_terms": "credit_terms_days",
    "overdue": "overdue_inr", "dso": "dso_days",
    "owner": "account_owner", "account_manager": "account_owner",
    "location": "city", "hq": "city",
    "backups": "alt_suppliers", "alternates": "alt_suppliers",
    "tier": "importance", "account_tier": "importance", "strategic": "importance",
    "dependence": "depends_on_us", "dependency": "depends_on_us", "reliance": "depends_on_us",
}

ROLE_MAP = [
    (("transport", "truck", "fleet", "carrier", "freight", "courier", "logistics partner", "3pl partner"), Role.carrier),
    (("warehouse", "lessor", "landlord"), Role.warehouse_lessor),
    (("customer", "client", "shipper", "buyer"), Role.customer),
]


def norm_header(h: str) -> str:
    h = re.sub(r"[^a-z0-9]+", "_", h.strip().lower()).strip("_")
    return ALIASES.get(h, h)


def norm_domain(v: str | None) -> str | None:
    if not v:
        return None
    v = v.strip().lower()
    v = re.sub(r"^https?://", "", v)
    v = re.sub(r"^www\.", "", v)
    v = v.split("/")[0].split("?")[0].strip()
    return v or None


def parse_inr(v: Any) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).lower().replace("₹", "").replace("rs.", "").replace("rs", "").replace("inr", "")
    s = s.replace(",", "").strip()
    mult = 1.0
    if re.search(r"(cr|crore)s?$", s):
        mult, s = 1e7, re.sub(r"(cr|crore)s?$", "", s)
    elif re.search(r"(l|lakh|lac|lakhs|lacs)$", s):
        mult, s = 1e5, re.sub(r"(l|lakh|lac|lakhs|lacs)$", "", s)
    elif s.endswith("k"):
        mult, s = 1e3, s[:-1]
    try:
        return float(s.strip()) * mult
    except ValueError:
        raise ValueError(f"cannot parse amount '{v}'")


def norm_role(v: Any) -> Role:
    s = str(v or "").strip().lower()
    for r in Role:
        if s == r.value:
            return r
    for keys, role in ROLE_MAP:
        if any(k in s for k in keys):
            return role
    return Role.vendor


def _read_rows(path: str) -> list[dict]:
    p = path.lower()
    if p.endswith(".json"):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data = data.get("counterparties") or data.get("rows") or data.get("data") or list(data.values())[0]
        return list(data)
    if p.endswith((".xlsx", ".xls")):
        import openpyxl  # optional dependency
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        hdr = [str(h or "") for h in rows[0]]
        return [dict(zip(hdr, r)) for r in rows[1:] if any(c is not None for c in r)]
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def normalise_row(raw: dict, i: int) -> Counterparty:
    row: dict[str, Any] = {}
    for k, v in raw.items():
        if k is None:
            continue
        nk = norm_header(str(k))
        if isinstance(v, str):
            v = v.strip()
        if v in ("", None):
            continue
        row.setdefault(nk, v)
    if "name" not in row and "legal_name" in row:
        row["name"] = row["legal_name"]
    if "name" not in row:
        raise ValueError("missing name")
    row["cp_id"] = str(row.get("cp_id") or f"ROW-{i:03d}")
    row["role"] = norm_role(row.get("role"))
    row["domain"] = norm_domain(row.get("domain"))
    for k in ("exposure_inr", "overdue_inr"):
        if k in row:
            row[k] = parse_inr(row[k]) or 0.0
    if "credit_terms_days" in row:
        m = re.search(r"\d+", str(row["credit_terms_days"]))
        row["credit_terms_days"] = int(m.group()) if m else None
    if "dso_days" in row:
        row["dso_days"] = float(re.sub(r"[^\d.]", "", str(row["dso_days"])) or 0)
    if "gstin" in row:
        row["gstin"] = str(row["gstin"]).upper().replace(" ", "")
    if "cin" in row:
        row["cin"] = str(row["cin"]).upper().replace(" ", "")
    if "criticality" in row:
        c = str(row["criticality"]).lower().replace(" ", "_").replace("-", "_")
        row["criticality"] = c if c in ("sole_source", "primary", "backup") else "na"
    if "importance" in row:
        v = str(row["importance"]).strip().lower()
        row["importance"] = {"key": "strategic", "critical": "strategic", "high": "strategic",
                             "medium": "core", "normal": "standard", "low": "standard"}.get(v, v)
        if row["importance"] not in ("strategic", "core", "standard"):
            row.pop("importance")
    if "depends_on_us" in row:
        v = str(row["depends_on_us"]).strip().lower()
        row["depends_on_us"] = {"yes": "high", "no": "low", "n/a": "na", "none": "na"}.get(v, v)
        if row["depends_on_us"] not in ("high", "medium", "low", "na"):
            row.pop("depends_on_us")
    if isinstance(row.get("alt_suppliers"), str):
        row["alt_suppliers"] = [s.strip() for s in re.split(r"[;|,]", row["alt_suppliers"]) if s.strip()]
    known = set(Counterparty.model_fields)
    clean = {k: v for k, v in row.items() if k in known}
    clean["record_hash"] = hashlib.sha256(json.dumps(clean, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return Counterparty.model_validate(clean)


def load_counterparties(path: str, persist: bool = True) -> tuple[list[Counterparty], list[str]]:
    rows, errors = [], []
    for i, raw in enumerate(_read_rows(path), start=1):
        try:
            cp = normalise_row(raw, i)
            rows.append(cp)
            if persist:
                db.upsert_counterparty(cp)
        except Exception as e:  # one bad row never stops the run
            errors.append(f"row {i}: {e}")
    return rows, errors

"""
Finance MCP — Web App (Starlette).

Bundles the static catalogue UI (docs/app/tools.html) and a JSON API that
drives every one of the 12 PE × AI tools end-to-end on user-submitted form
data, returning the freshly-rendered report's URL so the browser can
navigate straight to it.

Run:
    python -m finance_mcp.web [port]
    # or
    finance-mcp-web [port]

Default port: 8765.
    /                  — landing page (docs/index.html)
    /app/              — DX upload UI (docs/app/index.html)
    /app/tools.html    — full 12-tool catalogue
    /api/diagnose      — Tool I (DX)        · multipart CSV upload
    /api/explain       — Tool II (Explainer) · OpportunityMap path → memo
    /api/benchmark-corpus  — Tool III (BX)  · N OpportunityMap JSONs → corpus
    /api/eval          — Tool IV (Eval)     · runs the corpus eval
    /api/cim           — Tool V (CIM)       · ticker + form → red-flags
    /api/exit-proof-pack   — Tool VI         · seller-side proof pack
    /api/ddq           — Tool VII (DDQ)     · ILPA-shaped DDQ packet
    /api/normalize     — Tool VIII          · cross-portco normalization
    /api/plan-drift    — Tool IX            · 100-day plan drift report
    /api/benchmark-vendors — Tool X         · USAspending procurement
    /api/ai-act-audit  — Tool XI            · EU AI Act compliance pack
    /api/audit-agents  — Tool XII           · agent-sprawl auditor
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable

from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
)
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from finance_mcp.dx.session import clear_sessions
from finance_mcp.dx_orchestrator import run_diagnostic


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DOCS_DIR = _REPO_ROOT / "docs"
_APP_DIR = _DOCS_DIR / "app"
_LANDING_HTML = _DOCS_DIR / "index.html"
_OUTPUT_DIR = _REPO_ROOT / "finance_output"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _form_str(form: Any, key: str, default: str = "") -> str:
    """Pull a single string value from a Starlette FormData, trimmed."""
    return str(form.get(key, default) or default).strip()


def _form_list_str(form: Any, key: str) -> list[str]:
    """Pull a comma-or-newline-separated list from a single form field."""
    raw = str(form.get(key, "") or "").strip()
    if not raw:
        return []
    parts: list[str] = []
    for chunk in raw.replace("\r", "\n").replace(",", "\n").split("\n"):
        s = chunk.strip()
        if s:
            parts.append(s)
    return parts


def _safe_filename(s: str, prefix: str = "out") -> str:
    """Filesystem-safe slug for output basenames."""
    cleaned = "".join(c if c.isalnum() or c in "-_" else "_" for c in s)[:48]
    return cleaned or prefix


def _report_url(report_path: str) -> str:
    """Map a finance_output/<file> path to the URL the browser can fetch."""
    p = Path(report_path)
    return f"/finance_output/{p.name}"


def _resolve_in_repo(path_str: str) -> Path:
    """Resolve a relative path against the repo root (so 'finance_output/x.json'
    works from a request even when the working directory differs)."""
    p = Path(path_str)
    return p if p.is_absolute() else (_REPO_ROOT / p)


async def _run_tool(fn: Callable[[], dict]) -> dict:
    """Invoke a synchronous tool function in a thread executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, fn)


def _result_path(result: dict) -> str | None:
    """Tool functions return either {report_path: ...} or {path: ...}."""
    return (
        result.get("report_path")
        or result.get("path")
        or result.get("html_path")
    )


def _ok_response(result: dict, **extra: Any) -> JSONResponse:
    rp = _result_path(result)
    if not rp:
        return JSONResponse(
            {"error": "tool returned no report_path", "result": result},
            status_code=500,
        )
    payload: dict[str, Any] = {
        "ok": True,
        "report_url": _report_url(rp),
        "report_path": rp,
        "result": {k: v for k, v in result.items() if k not in {"report_path", "path"}},
    }
    payload.update(extra)
    return JSONResponse(payload)


def _fail_response(stage: str, exc: Exception) -> JSONResponse:
    return JSONResponse(
        {"error": f"{stage} failed: {exc.__class__.__name__}: {exc}"},
        status_code=422,
    )


# ---------------------------------------------------------------------------
# / — landing page · /healthz
# ---------------------------------------------------------------------------

async def index(request: Request) -> FileResponse:
    """Serve the marketing landing page at /."""
    return FileResponse(str(_LANDING_HTML), media_type="text/html")


async def healthz(request: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


# ---------------------------------------------------------------------------
# Tool I — /api/diagnose (DX upload, multipart)
# ---------------------------------------------------------------------------

async def api_diagnose(request: Request) -> JSONResponse:
    """Accept multipart CSV upload, run the DX pipeline, return report URL."""
    try:
        form = await request.form()
    except Exception as exc:
        return JSONResponse({"error": f"invalid form: {exc}"}, status_code=400)

    files = form.getlist("files")
    portco_id = _form_str(form, "portco_id", "uploaded") or "uploaded"
    safe_portco = _safe_filename(portco_id, "uploaded")

    if not files:
        return JSONResponse(
            {"error": "no files uploaded (field name: files)"}, status_code=400
        )

    run_id = uuid.uuid4().hex[:10]
    tmp_dir = Path(tempfile.mkdtemp(prefix=f"dx_upload_{run_id}_"))
    data_paths: list[str] = []
    try:
        for f in files:
            raw_name = getattr(f, "filename", "") or ""
            if not raw_name:
                continue
            base = os.path.basename(raw_name)
            if not base.lower().endswith(".csv"):
                return JSONResponse(
                    {"error": f"only .csv files are accepted; got {base!r}"},
                    status_code=400,
                )
            dest = tmp_dir / base
            content = await f.read()
            dest.write_bytes(content)
            data_paths.append(str(dest))

        if not data_paths:
            return JSONResponse(
                {"error": "no valid CSV files in upload"}, status_code=400
            )

        events: list[dict[str, Any]] = []

        def _progress(stage: str, payload: dict) -> None:
            events.append({"stage": stage, **payload})

        clear_sessions()

        loop = asyncio.get_running_loop()
        output_filename = f"dx_report_{safe_portco}_{run_id}.html"
        try:
            result = await loop.run_in_executor(
                None,
                lambda: run_diagnostic(
                    data_paths=data_paths,
                    portco_id=safe_portco,
                    top_k_opportunities=5,
                    output_filename=output_filename,
                    progress=_progress,
                ),
            )
        except Exception as exc:
            return JSONResponse(
                {
                    "error": f"diagnostic failed: {exc}",
                    "stage_events": events,
                },
                status_code=422,
            )

        report_name = Path(result.report_path).name
        return JSONResponse({
            "ok": True,
            "run_id": run_id,
            "portco_id": safe_portco,
            "template_id": result.template_id,
            "opportunities_rendered": result.opportunities_rendered,
            "total_impact_usd_annual": result.total_impact_usd_annual,
            "report_url": f"/reports/{report_name}",
            "stage_events": events,
        })

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Tool II — /api/explain
# ---------------------------------------------------------------------------

async def api_explain(request: Request) -> JSONResponse:
    """Render a board-defendable memo from a portco's own CSVs.

    Accepts the same CSV pair as DX (loans + performance). Internally runs
    the DX pipeline to produce the OpportunityMap, then renders the memo
    on top. The OpportunityMap is an implementation detail; the user-facing
    contract is "drop CSVs, get memo."

    Backwards-compatible: if `opportunity_map_path` is provided in the
    form (no files uploaded), uses that as the source instead.
    """
    from finance_mcp.explainer import explain_decision

    form = await request.form()
    audience = _form_str(form, "audience", "board") or "board"
    portco_id = _form_str(form, "portco_id", "uploaded") or "uploaded"
    safe_portco = _safe_filename(portco_id, "uploaded")
    files = form.getlist("files")

    om_path: Path | None = None
    tmp_dir: Path | None = None
    try:
        if files and hasattr(files[0], "filename") and files[0].filename:
            # CSV upload path — run DX first.
            tmp_dir = Path(tempfile.mkdtemp(prefix="explain_dx_"))
            data_paths: list[str] = []
            for f in files:
                raw_name = getattr(f, "filename", "") or ""
                if not raw_name:
                    continue
                base = os.path.basename(raw_name)
                if not base.lower().endswith(".csv"):
                    return JSONResponse(
                        {"error": f"only .csv files are accepted; got {base!r}"},
                        status_code=400,
                    )
                dest = tmp_dir / base
                dest.write_bytes(await f.read())
                data_paths.append(str(dest))
            if not data_paths:
                return JSONResponse(
                    {"error": "no valid CSV files in upload"}, status_code=400
                )

            run_id = uuid.uuid4().hex[:10]
            dx_filename = f"dx_report_{safe_portco}_{run_id}.html"
            clear_sessions()
            dx_result = await _run_tool(
                lambda: run_diagnostic(
                    data_paths=data_paths,
                    portco_id=safe_portco,
                    top_k_opportunities=5,
                    output_filename=dx_filename,
                )
            )
            # The DX orchestrator writes both .html and .json sidecars.
            om_path = Path(dx_result.report_path).with_suffix(".json")
        else:
            # Path-based input — use provided JSON or default example.
            raw_path = _form_str(
                form,
                "opportunity_map_path",
                "finance_output/dx_report_MortgageCo.json",
            )
            om_path = _resolve_in_repo(raw_path)

        if not om_path or not om_path.is_file():
            return JSONResponse(
                {"error": f"OpportunityMap not found: {om_path}"},
                status_code=404,
            )

        try:
            result = await _run_tool(
                lambda: explain_decision(
                    opportunity_map_path=str(om_path),
                    audience=audience,
                )
            )
        except Exception as exc:
            return _fail_response("explain", exc)
        return _ok_response(result)
    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Tool III — /api/benchmark-corpus  (BX)
# ---------------------------------------------------------------------------

async def api_benchmark_corpus(request: Request) -> JSONResponse:
    """Roll up N portcos into a fund-level BX corpus.

    User-facing inputs (no JSON paths exposed):
      - `preset`     — pick a built-in fund preset (mixed_fund, hmda_states,
                       regional_lenders_demo). Resolves to the right set of
                       per-portco OpportunityMap JSONs.
      - `portco_ids` — comma-separated portco IDs that already have DX runs
                       in finance_output/. Each ID resolves to
                       `dx_report_<id>.json`.
      - `corpus_id`  — display label / output filename slug.

    Backwards-compatible: if `json_paths` is supplied (legacy), uses those.
    """
    from finance_mcp.bx.ingest_corpus import bx_ingest_corpus
    from finance_mcp.bx.report import bx_report

    form = await request.form()
    corpus_id = _form_str(form, "corpus_id", "") or "fund_demo"
    safe_corpus = _safe_filename(corpus_id, "fund_demo")

    # 1) Preset shortcut — the curated built-in corpora.
    PRESETS: dict[str, list[str]] = {
        "mixed_fund": [
            "dx_report_MortgageCo.json", "dx_report_DCMortgage.json",
            "dx_report_midwest_lender.json", "dx_report_southeast_lender.json",
            "dx_report_pacific_lender.json", "dx_report_northeast_lender.json",
            "dx_report_mountain_lender.json",
        ],
        "hmda_states": [
            "dx_report_HMDA_DC.json", "dx_report_HMDA_DE.json",
            "dx_report_HMDA_MA.json", "dx_report_HMDA_AZ.json",
            "dx_report_HMDA_GA.json",
        ],
        "regional_lenders_demo": [
            "dx_report_midwest_lender.json", "dx_report_southeast_lender.json",
            "dx_report_pacific_lender.json", "dx_report_northeast_lender.json",
            "dx_report_mountain_lender.json",
        ],
    }
    preset = _form_str(form, "preset", "")

    json_paths: list[str] = []

    # Preset wins if explicitly chosen.
    if preset and preset in PRESETS:
        for fname in PRESETS[preset]:
            p = _OUTPUT_DIR / fname
            if p.is_file():
                json_paths.append(str(p))
        if not safe_corpus or safe_corpus == "fund_demo":
            safe_corpus = preset

    # 2) Portco IDs — comma-separated.
    if not json_paths:
        ids_raw = _form_str(form, "portco_ids", "")
        if ids_raw:
            for tok in [t.strip() for t in ids_raw.replace("\n", ",").split(",")]:
                if not tok:
                    continue
                p = _OUTPUT_DIR / f"dx_report_{tok}.json"
                if p.is_file():
                    json_paths.append(str(p))

    # 3) Legacy file upload / direct paths.
    if not json_paths:
        files = form.getlist("json_paths")
        if files and hasattr(files[0], "filename") and files[0].filename:
            tmp_dir = Path(tempfile.mkdtemp(prefix="bx_upload_"))
            for f in files:
                raw_name = getattr(f, "filename", "") or ""
                if not raw_name:
                    continue
                base = os.path.basename(raw_name)
                if not base.lower().endswith(".json"):
                    return JSONResponse(
                        {"error": f"only .json files are accepted; got {base!r}"},
                        status_code=400,
                    )
                dest = tmp_dir / base
                dest.write_bytes(await f.read())
                json_paths.append(str(dest))
        else:
            for raw in _form_list_str(form, "json_paths"):
                json_paths.append(str(_resolve_in_repo(raw)))

    # 4) Fallback: pick the default mixed-fund preset so the form is
    #    one-click usable for the demo.
    if not json_paths:
        for fname in PRESETS["mixed_fund"]:
            p = _OUTPUT_DIR / fname
            if p.is_file():
                json_paths.append(str(p))
        if not safe_corpus or safe_corpus == "fund_demo":
            safe_corpus = "mixed_fund"

    if not json_paths:
        return JSONResponse(
            {"error": "no portco runs found — run DX on at least one portco first"},
            status_code=400,
        )

    try:
        await _run_tool(
            lambda: bx_ingest_corpus(
                json_paths=json_paths, corpus_id=safe_corpus
            )
        )
        result = await _run_tool(
            lambda: bx_report(corpus_id=safe_corpus)
        )
    except Exception as exc:
        return _fail_response("bx", exc)
    return _ok_response(result, n_portcos=len(json_paths))


# ---------------------------------------------------------------------------
# Tool IV — /api/eval
# ---------------------------------------------------------------------------

async def api_eval(request: Request) -> JSONResponse:
    """Run the corpus eval (no inputs — walks finance_output/).

    The script `scripts/run_eval_corpus.py` is the canonical entrypoint.
    We invoke it as a subprocess so it produces eval_corpus_summary.html
    (the file the catalogue links to) using the same code path that
    `python -m scripts.run_eval_corpus` runs.
    """
    import subprocess

    out_html = _OUTPUT_DIR / "eval_corpus_summary.html"

    def _do_eval() -> dict:
        completed = subprocess.run(
            [sys.executable, "-m", "scripts.run_eval_corpus"],
            cwd=str(_REPO_ROOT),
            timeout=120,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"run_eval_corpus exited {completed.returncode}: "
                f"{completed.stderr[-400:]}"
            )
        if not out_html.is_file():
            raise RuntimeError("eval_corpus_summary.html not produced")
        return {"report_path": str(out_html)}

    try:
        result = await _run_tool(_do_eval)
    except Exception as exc:
        return _fail_response("eval", exc)
    return _ok_response(result)


# ---------------------------------------------------------------------------
# Tool V — /api/cim
# ---------------------------------------------------------------------------

async def api_cim(request: Request) -> JSONResponse:
    from finance_mcp.cim import cim_analyze

    form = await request.form()
    ticker = _form_str(form, "ticker", "SHC") or "SHC"
    form_kind = _form_str(form, "form", "10-K") or "10-K"

    try:
        result = await _run_tool(
            lambda: cim_analyze(ticker=ticker, form=form_kind)
        )
    except Exception as exc:
        return _fail_response("cim", exc)
    return _ok_response(result)


# ---------------------------------------------------------------------------
# Tool VI — /api/exit-proof-pack
# ---------------------------------------------------------------------------

async def api_exit_proof_pack(request: Request) -> JSONResponse:
    """Build the seller-side proof pack from a portco's own CSVs.

    Accepts the same CSV pair as DX. Internally runs DX to produce the
    OpportunityMap, then builds the proof pack on top. The portco's deal
    team only ever sees CSVs in / proof pack out — no JSON in the middle.

    Backwards-compatible: if `opportunity_map_path` is provided in the
    form (no files uploaded), uses that as the source instead.
    """
    from finance_mcp.seller_pack import exit_proof_pack

    form = await request.form()
    portco_id = _form_str(form, "portco_id", "MortgageCo") or "MortgageCo"
    safe_portco = _safe_filename(portco_id, "MortgageCo")
    files = form.getlist("files")

    om_path: Path | None = None
    tmp_dir: Path | None = None
    try:
        if files and hasattr(files[0], "filename") and files[0].filename:
            tmp_dir = Path(tempfile.mkdtemp(prefix="seller_pack_dx_"))
            data_paths: list[str] = []
            for f in files:
                raw_name = getattr(f, "filename", "") or ""
                if not raw_name:
                    continue
                base = os.path.basename(raw_name)
                if not base.lower().endswith(".csv"):
                    return JSONResponse(
                        {"error": f"only .csv files are accepted; got {base!r}"},
                        status_code=400,
                    )
                dest = tmp_dir / base
                dest.write_bytes(await f.read())
                data_paths.append(str(dest))
            if not data_paths:
                return JSONResponse(
                    {"error": "no valid CSV files in upload"}, status_code=400
                )

            run_id = uuid.uuid4().hex[:10]
            dx_filename = f"dx_report_{safe_portco}_{run_id}.html"
            clear_sessions()
            dx_result = await _run_tool(
                lambda: run_diagnostic(
                    data_paths=data_paths,
                    portco_id=safe_portco,
                    top_k_opportunities=5,
                    output_filename=dx_filename,
                )
            )
            om_path = Path(dx_result.report_path).with_suffix(".json")
        else:
            raw_path = _form_str(
                form,
                "opportunity_map_path",
                "finance_output/dx_report_MortgageCo.json",
            )
            om_path = _resolve_in_repo(raw_path)

        if not om_path or not om_path.is_file():
            return JSONResponse(
                {"error": f"OpportunityMap not found: {om_path}"},
                status_code=404,
            )

        try:
            result = await _run_tool(
                lambda: exit_proof_pack(
                    portco_id=portco_id,
                    opportunity_map_path=str(om_path),
                )
            )
        except Exception as exc:
            return _fail_response("exit-proof-pack", exc)
        return _ok_response(result)
    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Tool VII — /api/ddq
# ---------------------------------------------------------------------------

async def api_ddq(request: Request) -> JSONResponse:
    from finance_mcp.ddq import ddq_respond

    form = await request.form()
    fund_name = _form_str(form, "fund_name", "Bolnet Capital Partners I") or "Bolnet Capital Partners I"

    try:
        result = await _run_tool(lambda: ddq_respond(fund_name=fund_name))
    except Exception as exc:
        return _fail_response("ddq", exc)
    return _ok_response(result)


# ---------------------------------------------------------------------------
# Tool VIII — /api/normalize
# ---------------------------------------------------------------------------

async def api_normalize(request: Request) -> JSONResponse:
    from finance_mcp.normalize import normalize_portco

    form = await request.form()

    csv_paths: list[str] = []
    portco_ids: list[str] = []

    files = form.getlist("portco_csv_paths")
    if files and hasattr(files[0], "filename"):
        tmp_dir = Path(tempfile.mkdtemp(prefix="normalize_upload_"))
        for f in files:
            raw_name = getattr(f, "filename", "") or ""
            if not raw_name:
                continue
            dest = tmp_dir / os.path.basename(raw_name)
            dest.write_bytes(await f.read())
            csv_paths.append(str(dest))
            stem = dest.stem
            portco_ids.append(stem)
    else:
        for raw in _form_list_str(form, "portco_csv_paths"):
            csv_paths.append(str(_resolve_in_repo(raw)))
        portco_ids = _form_list_str(form, "portco_ids")

    if not csv_paths:
        # Default to the demo trio so the UI is one-click for users.
        csv_paths = [
            str(_REPO_ROOT / "demo" / "regional_lenders" / "midwest_lender" / "loans.csv"),
            str(_REPO_ROOT / "demo" / "yasserh_mortgages" / "loans.csv"),
            str(_REPO_ROOT / "demo" / "hmda_states" / "ga" / "loans.csv"),
        ]
        portco_ids = ["midwest_lender", "MortgageCo", "HMDA_GA"]
    if len(portco_ids) != len(csv_paths):
        portco_ids = [Path(p).stem for p in csv_paths]

    try:
        result = await _run_tool(
            lambda: normalize_portco(
                portco_csv_paths=csv_paths, portco_ids=portco_ids
            )
        )
    except Exception as exc:
        return _fail_response("normalize", exc)
    return _ok_response(result)


# ---------------------------------------------------------------------------
# Tool IX — /api/plan-drift
# ---------------------------------------------------------------------------

async def api_plan_drift(request: Request) -> JSONResponse:
    from finance_mcp.plan_drift import track_plan_drift

    form = await request.form()
    portco_id = _form_str(form, "portco_id", "SoteraCo") or "SoteraCo"
    ticker = _form_str(form, "ticker", "SHC") or "SHC"

    try:
        result = await _run_tool(
            lambda: track_plan_drift(portco_id=portco_id, ticker=ticker)
        )
    except Exception as exc:
        return _fail_response("plan-drift", exc)
    return _ok_response(result)


# ---------------------------------------------------------------------------
# Tool X — /api/benchmark-vendors
# ---------------------------------------------------------------------------

async def api_benchmark_vendors(request: Request) -> JSONResponse:
    from finance_mcp.procurement import benchmark_vendors

    form = await request.form()
    psc_code = _form_str(form, "psc_code", "D310") or "D310"
    fiscal_year = _form_str(form, "fiscal_year", "2024") or "2024"
    max_records = _form_str(form, "max_records", "500") or "500"

    try:
        fy = int(fiscal_year)
        mr = int(max_records)
    except ValueError as exc:
        return JSONResponse({"error": f"bad numeric form value: {exc}"}, status_code=400)

    try:
        result = await _run_tool(
            lambda: benchmark_vendors(
                psc_code=psc_code, fiscal_year=fy, max_records=mr
            )
        )
    except Exception as exc:
        return _fail_response("benchmark-vendors", exc)
    return _ok_response(result)


# ---------------------------------------------------------------------------
# Tool XI — /api/ai-act-audit
# ---------------------------------------------------------------------------

async def api_ai_act_audit(request: Request) -> JSONResponse:
    from finance_mcp.eu_ai_act import ai_act_audit

    form = await request.form()
    portco_id = _form_str(form, "portco_id", "LendingCo-EU") or "LendingCo-EU"
    desc = _form_str(form, "ai_system_description",
                     "Consumer credit-decisioning ML model.") \
        or "Consumer credit-decisioning ML model."
    use_case = _form_str(form, "use_case_category", "credit_decisioning") \
        or "credit_decisioning"

    try:
        result = await _run_tool(
            lambda: ai_act_audit(
                portco_id=portco_id,
                ai_system_description=desc,
                use_case_category=use_case,
            )
        )
    except Exception as exc:
        return _fail_response("ai-act-audit", exc)
    return _ok_response(result)


# ---------------------------------------------------------------------------
# Tool XII — /api/audit-agents
# ---------------------------------------------------------------------------

async def api_audit_agents(request: Request) -> JSONResponse:
    from finance_mcp.agent_sprawl import audit_agents

    try:
        result = await _run_tool(lambda: audit_agents())
    except Exception as exc:
        return _fail_response("audit-agents", exc)
    return _ok_response(result)


# ---------------------------------------------------------------------------
# /reports/<file>          — DX-only legacy path (kept for app.js)
# /finance_output/<file>   — Generic served-from-disk reports
# ---------------------------------------------------------------------------

async def report_file(request: Request) -> FileResponse:
    """Serve a generated DX report from finance_output/ (legacy route)."""
    name = request.path_params["filename"]
    safe = os.path.basename(name)
    path = _OUTPUT_DIR / safe
    if not path.is_file() or not safe.startswith("dx_report_"):
        return JSONResponse({"error": "report not found"}, status_code=404)
    return FileResponse(str(path), media_type="text/html")


async def finance_output_file(request: Request) -> FileResponse:
    """Serve any HTML / JSON / asset from finance_output/."""
    name = request.path_params["filename"]
    safe = os.path.basename(name)
    path = _OUTPUT_DIR / safe
    if not path.is_file():
        return JSONResponse({"error": f"{safe} not found"}, status_code=404)
    media = "text/html" if safe.lower().endswith(".html") else "application/octet-stream"
    if safe.lower().endswith(".json"):
        media = "application/json"
    return FileResponse(str(path), media_type=media)


def build_app() -> Starlette:
    _APP_DIR.mkdir(parents=True, exist_ok=True)
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    app = Starlette(
        debug=False,
        routes=[
            Route("/", endpoint=index),
            Route("/healthz", endpoint=healthz),
            # Tool I — DX (multipart upload, special path).
            Route("/api/diagnose", endpoint=api_diagnose, methods=["POST"]),
            # Tools II–XII (form POST → JSON).
            Route("/api/explain", endpoint=api_explain, methods=["POST"]),
            Route("/api/benchmark-corpus", endpoint=api_benchmark_corpus, methods=["POST"]),
            Route("/api/eval", endpoint=api_eval, methods=["POST"]),
            Route("/api/cim", endpoint=api_cim, methods=["POST"]),
            Route("/api/exit-proof-pack", endpoint=api_exit_proof_pack, methods=["POST"]),
            Route("/api/ddq", endpoint=api_ddq, methods=["POST"]),
            Route("/api/normalize", endpoint=api_normalize, methods=["POST"]),
            Route("/api/plan-drift", endpoint=api_plan_drift, methods=["POST"]),
            Route("/api/benchmark-vendors", endpoint=api_benchmark_vendors, methods=["POST"]),
            Route("/api/ai-act-audit", endpoint=api_ai_act_audit, methods=["POST"]),
            Route("/api/audit-agents", endpoint=api_audit_agents, methods=["POST"]),
            # Reports.
            Route("/reports/{filename}", endpoint=report_file, methods=["GET"]),
            Route("/finance_output/{filename}", endpoint=finance_output_file, methods=["GET"]),
            # App and docs static.
            Mount(
                "/app",
                app=StaticFiles(directory=str(_APP_DIR), html=True),
                name="app",
            ),
            Mount(
                "/docs",
                app=StaticFiles(directory=str(_DOCS_DIR), html=True),
                name="docs",
            ),
            # Mirror docs/demos/ at /demos/ so the relative path used on
            # GitHub Pages also works when running the local Starlette app.
            Mount(
                "/demos",
                app=StaticFiles(directory=str(_DOCS_DIR / "demos"), html=False),
                name="demos",
            ),
        ],
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    return app


def start(port: int = 8765) -> None:
    import uvicorn

    host = os.environ.get("FINANCE_WEB_HOST", "127.0.0.1")
    print(f"[finance-web] Serving on http://{host}:{port}/", file=sys.stderr)
    uvicorn.run(build_app(), host=host, port=port, log_level="info")


if __name__ == "__main__":
    _port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    start(_port)

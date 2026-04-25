"""
Finance MCP — Web App (Starlette).

Bundles a static upload UI and a JSON endpoint that drives the full
Decision-Optimization Diagnostic pipeline on uploaded CSVs, returning
the report as a URL the browser can render in an iframe.

Run:
    python -m finance_mcp.web [port]
    # or
    finance-mcp-web [port]

Default port: 8765. Open http://localhost:8765/ to use the UI.
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
from typing import Any

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


async def index(request: Request) -> FileResponse:
    """Serve the marketing landing page at /."""
    return FileResponse(str(_LANDING_HTML), media_type="text/html")


async def healthz(request: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


async def api_diagnose(request: Request) -> JSONResponse:
    """Accept multipart CSV upload, run the DX pipeline, return report URL."""
    try:
        form = await request.form()
    except Exception as exc:
        return JSONResponse({"error": f"invalid form: {exc}"}, status_code=400)

    files = form.getlist("files")
    portco_id = str(form.get("portco_id", "uploaded")).strip() or "uploaded"
    # Sanitize portco_id so it maps cleanly to a filesystem-safe filename.
    safe_portco = "".join(
        c if c.isalnum() or c in "-_" else "_" for c in portco_id
    )[:40] or "uploaded"

    if not files:
        return JSONResponse(
            {"error": "no files uploaded (field name: files)"}, status_code=400
        )

    run_id = uuid.uuid4().hex[:10]
    tmp_dir = Path(tempfile.mkdtemp(prefix=f"dx_upload_{run_id}_"))
    data_paths: list[str] = []
    try:
        # Save each upload. We keep the original filename so the DX template
        # matcher can recognise 'leads.csv' etc.
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

        # Run pipeline. We collect progress events so the client can show them.
        # (Phase 0: one-shot, no streaming. Phase 1 will switch to SSE.)
        events: list[dict[str, Any]] = []

        def _progress(stage: str, payload: dict) -> None:
            events.append({"stage": stage, **payload})

        # Each upload gets its own session — start fresh. Running diagnostics
        # in parallel requires more plumbing (not in scope for Phase 0).
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
        # Keep the report files, drop the raw upload tmpdir.
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def report_file(request: Request) -> FileResponse:
    """Serve a generated DX report from finance_output/."""
    name = request.path_params["filename"]
    safe = os.path.basename(name)
    path = _OUTPUT_DIR / safe
    if not path.is_file() or not safe.startswith("dx_report_"):
        return JSONResponse({"error": "report not found"}, status_code=404)
    return FileResponse(str(path), media_type="text/html")


def build_app() -> Starlette:
    _APP_DIR.mkdir(parents=True, exist_ok=True)
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    app = Starlette(
        debug=False,
        routes=[
            Route("/", endpoint=index),
            Route("/healthz", endpoint=healthz),
            Route("/api/diagnose", endpoint=api_diagnose, methods=["POST"]),
            Route(
                "/reports/{filename}",
                endpoint=report_file,
                methods=["GET"],
            ),
            Mount(
                "/app",
                app=StaticFiles(directory=str(_APP_DIR), html=True),
                name="app",
            ),
        ],
    )
    # Allow the GitHub Pages landing page (or local files) to call the API
    # during local dev; tighten if this is ever exposed publicly.
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

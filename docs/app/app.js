// Claude Finance — /diagnose-decisions upload UI
// Phase 0: single-shot POST /api/diagnose, then render the returned report.

const form = document.getElementById("uploadForm");
const dropZone = document.getElementById("dropZone");
const fileInput = document.getElementById("fileInput");
const browseBtn = document.getElementById("browseBtn");
const fileList = document.getElementById("fileList");
const portcoInput = document.getElementById("portcoId");
const runBtn = document.getElementById("runBtn");
const resetBtn = document.getElementById("resetBtn");
const pipelinePanel = document.getElementById("pipeline");
const resultPanel = document.getElementById("result");
const reportFrame = document.getElementById("reportFrame");
const openReport = document.getElementById("openReport");
const resultSummary = document.getElementById("resultSummary");
const log = document.getElementById("log");

let picked = [];

function formatBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1_048_576) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1_048_576).toFixed(1)} MB`;
}

function renderFileList() {
  if (!picked.length) {
    fileList.hidden = true;
    fileList.innerHTML = "";
    runBtn.disabled = true;
    return;
  }
  fileList.hidden = false;
  fileList.innerHTML = picked
    .map((f) => `<li><span>${f.name}</span><span class="sz">${formatBytes(f.size)}</span></li>`)
    .join("");
  runBtn.disabled = false;
}

function setFiles(fileListLike) {
  picked = Array.from(fileListLike).filter((f) => f.name.toLowerCase().endsWith(".csv"));
  renderFileList();
  // Auto-suggest portco_id from first filename root if empty.
  if (!portcoInput.value && picked[0]) {
    const stem = picked[0].name.replace(/\.csv$/i, "").split(/[_.-]/)[0];
    portcoInput.value = stem;
  }
}

// Drop zone events
dropZone.addEventListener("click", () => fileInput.click());
browseBtn.addEventListener("click", (e) => { e.stopPropagation(); fileInput.click(); });
fileInput.addEventListener("change", (e) => setFiles(e.target.files));

["dragenter", "dragover"].forEach((evt) => {
  dropZone.addEventListener(evt, (e) => {
    e.preventDefault(); e.stopPropagation();
    dropZone.classList.add("hover");
  });
});
["dragleave", "drop"].forEach((evt) => {
  dropZone.addEventListener(evt, (e) => {
    e.preventDefault(); e.stopPropagation();
    dropZone.classList.remove("hover");
  });
});
dropZone.addEventListener("drop", (e) => {
  if (e.dataTransfer && e.dataTransfer.files) setFiles(e.dataTransfer.files);
});

// Demo chips — fetch canned demo CSVs from the server-served demo dataset
// endpoint. Phase 0: wire up as a user convenience; the user can still bring
// their own.
async function loadDemo(kind) {
  // The server doesn't expose demo files as endpoints, so we just prefill the
  // portco_id and show a hint. Full "try demo" requires a dedicated endpoint.
  const hints = {
    lending: "Generate the lending demo (real Lending Club loans, first run downloads ~1.6 GB once):\npython -m demo.lending_club.slice --out demo/lending_club\n\nThen drop demo/lending_club/loans.csv + performance.csv here.",
    saas: "Generate saas_pricing demo files locally with:\npython -c \"from demo.saas_pricing import generate as g; g.generate(out_dir='./demo_out', n_deals=8000, months=36, seed=42)\"",
    insurance: "Generate etelequote demo files locally with:\npython -c \"from demo.etelequote import generate as g; g.generate(out_dir='./demo_out', n_leads=4800, months=24, seed=42)\"",
  };
  log.textContent = hints[kind] || "";
  pipelinePanel.hidden = false;
  const ids = { lending: "LendingCo", saas: "saas_demo", insurance: "etelequote_demo" };
  portcoInput.value = ids[kind] || "";
}
document.querySelectorAll(".chip[data-demo]").forEach((el) => {
  el.addEventListener("click", () => loadDemo(el.dataset.demo));
});

// Stage rendering helpers
function resetStages() {
  pipelinePanel.querySelectorAll("li[data-stage]").forEach((li) => {
    li.classList.remove("active", "done", "error");
    li.querySelector(".stage-status").textContent = "pending";
  });
  log.textContent = "";
}
function markStage(stage, state) {
  const li = pipelinePanel.querySelector(`li[data-stage="${stage}"]`);
  if (!li) return;
  li.classList.remove("active", "done", "error");
  li.classList.add(state);
  const labels = { active: "running", done: "done", error: "error" };
  li.querySelector(".stage-status").textContent = labels[state] || state;
}

// Submit
form.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!picked.length) return;

  runBtn.disabled = true;
  runBtn.textContent = "Running…";
  resultPanel.hidden = true;
  pipelinePanel.hidden = false;
  resetStages();

  // Optimistically mark the first stage as active so the user sees motion
  // even before the server responds. Real stage events arrive in the response.
  markStage("ingest", "active");

  const fd = new FormData();
  fd.append("portco_id", portcoInput.value || "uploaded");
  picked.forEach((f) => fd.append("files", f, f.name));

  try {
    const res = await fetch("/api/diagnose", { method: "POST", body: fd });
    const data = await res.json();

    // Replay stage events to animate the pipeline, so the UI still feels live
    // even though Phase 0 is single-shot under the hood.
    const events = Array.isArray(data.stage_events) ? data.stage_events : [];
    for (let i = 0; i < events.length; i++) {
      const ev = events[i];
      markStage(ev.stage, "active");
      log.textContent += `[${ev.stage}] ${JSON.stringify(ev)}\n`;
      log.scrollTop = log.scrollHeight;
      await new Promise((r) => setTimeout(r, 180));
      markStage(ev.stage, "done");
    }

    if (!res.ok || !data.ok) {
      const stage = events.length ? events[events.length - 1].stage : "ingest";
      markStage(stage, "error");
      log.textContent += `\nERROR: ${data.error || "unknown"}\n`;
      runBtn.disabled = false;
      runBtn.textContent = "Run diagnostic";
      resetBtn.hidden = false;
      return;
    }

    // Show result
    resultSummary.innerHTML =
      `${data.opportunities_rendered} opportunit${data.opportunities_rendered === 1 ? "y" : "ies"} · ` +
      `<strong>$${Math.round(data.total_impact_usd_annual).toLocaleString()}/yr</strong> ` +
      `projected impact · <span style="color:var(--text-muted)">${data.template_id}</span>`;
    reportFrame.src = data.report_url;
    openReport.href = data.report_url;
    resultPanel.hidden = false;
    resetBtn.hidden = false;
    runBtn.textContent = "Re-run";
    runBtn.disabled = false;
    resultPanel.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    markStage("ingest", "error");
    log.textContent += `\nNETWORK ERROR: ${err.message}\n`;
    runBtn.disabled = false;
    runBtn.textContent = "Run diagnostic";
  }
});

resetBtn.addEventListener("click", () => {
  picked = [];
  fileInput.value = "";
  portcoInput.value = "";
  renderFileList();
  pipelinePanel.hidden = true;
  resultPanel.hidden = true;
  resetBtn.hidden = true;
  runBtn.textContent = "Run diagnostic";
  runBtn.disabled = true;
});

const $ = (selector) => document.querySelector(selector);
const form = $("#collection-form");
const keyInput = $("#openalex-key");
const sources = [...document.querySelectorAll('input[name="source"]')];
const types = [...document.querySelectorAll('input[name="paper-type"]')];
const currentYear = Math.min(new Date().getFullYear(), 2026);
let active = false;
let activeOutputPath = "";

function renderYears() {
  const host = $("#year-options");
  for (let year = 2000; year <= currentYear; year += 1) {
    const label = document.createElement("label");
    label.className = "year-chip checked";
    label.innerHTML = `<input type="checkbox" name="year" value="${year}" checked><span>${year}</span>`;
    host.append(label);
  }
}

function syncSourceCards() {
  sources.forEach((input) => input.closest(".source-card").classList.toggle("selected", input.checked));
  $("#key-block").classList.toggle("key-disabled", !$("input[name='source'][value='openalex']").checked);
}

function syncTypes() {
  types.forEach((input) => input.closest(".type-chip").classList.toggle("checked", input.checked));
}

function setRunning(running) {
  active = running;
  $("#start-button").disabled = running;
  $("#start-button").innerHTML = running ? "Collection running <span class=\"button-arrow\">…</span>" : "<span>Start collecting</span><span class=\"button-arrow\">→</span>";
  $("#stop-button").disabled = !running;
}

function renderStatus(data) {
  if (!data.has_job) {
    $("#status-badge").className = "status-badge idle";
    $("#status-label").textContent = "READY";
    setRunning(false);
    return;
  }
  const badge = $("#status-badge");
  const counts = data.counts || {};
  const saved = Number(counts.saved || 0);
  const target = Number(counts.target || $("#target").value || 0);
  const processed = Number(counts.processed || 0);
  const percent = target ? Math.min(100, Math.round(saved / target * 100)) : 0;
  $("#saved-count").textContent = saved.toLocaleString();
  $("#target-count").textContent = target.toLocaleString();
  $("#processed-count").textContent = processed.toLocaleString();
  $("#percent-count").textContent = `${percent}%`;
  $("#progress-fill").style.width = `${percent}%`;
  $("#status-message").textContent = data.message || "Ready when you are.";
  badge.className = `status-badge ${data.running ? "running" : data.phase === "complete" ? "complete" : data.phase === "error" ? "error" : data.phase === "stopped" ? "stopped" : "idle"}`;
  $("#status-label").textContent = data.running ? "RUNNING" : (data.phase || "ready").replaceAll("_", " ").toUpperCase();
  if (data.output) {
    $("#output-location").textContent = data.output;
    activeOutputPath = data.output_path || "";
    $("#open-folder").disabled = !activeOutputPath;
  }
  $("#progress-error").hidden = !data.error;
  $("#progress-error").textContent = data.error || "";
  setRunning(Boolean(data.running));
  if (data.report) $("#status-message").title = data.report;
}

async function refreshStatus() {
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    renderStatus(await response.json());
  } catch (_) {
    $("#status-message").textContent = "The local app connection was interrupted. Restart it from the terminal.";
  }
}

renderYears();
syncSourceCards();
syncTypes();
function updateTargetPreview() {
  const target = Number($("#target").value) || 0;
  $("#target-count").textContent = target.toLocaleString();
  if (!active) {
    const saved = Number($("#saved-count").textContent) || 0;
    const percent = target ? Math.min(100, Math.round(saved / target * 100)) : 0;
    $("#percent-count").textContent = `${percent}%`;
    $("#progress-fill").style.width = `${percent}%`;
  }
}

fetch("/api/defaults", { cache: "no-store" }).then((r) => r.json()).then((d) => {
  $("#workspace").value = d.workspace || "Paperflow";
  $("#output-location").textContent = d.output || "Paperflow\\Research_Papers";
  activeOutputPath = d.output_path || "";
  $("#open-folder").disabled = !d.output_exists;
}).catch(() => {
  $("#workspace").value = "Paperflow";
  $("#output-location").textContent = "Paperflow\\Research_Papers";
});

$("#workspace").addEventListener("change", async () => {
  const response = await fetch("/api/workspace-summary", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ workspace: $("#workspace").value.trim() }) });
  const result = await response.json();
  if (response.ok) {
    $("#output-location").textContent = result.output;
    activeOutputPath = result.output_path || "";
    $("#open-folder").disabled = !result.output_exists;
  }
});

$("#select-all-years").addEventListener("click", () => {
  document.querySelectorAll('input[name="year"]').forEach((input) => { input.checked = true; input.closest(".year-chip").classList.add("checked"); });
});
$("#clear-years").addEventListener("click", () => {
  document.querySelectorAll('input[name="year"]').forEach((input) => { input.checked = false; input.closest(".year-chip").classList.remove("checked"); });
});

sources.forEach((input) => input.addEventListener("change", syncSourceCards));
types.forEach((input) => input.addEventListener("change", syncTypes));
$("#reveal-key").addEventListener("click", () => {
  const show = keyInput.type === "password";
  keyInput.type = show ? "text" : "password";
  $("#reveal-key").textContent = show ? "HIDE" : "SHOW";
});
$("#target").addEventListener("input", updateTargetPreview);
document.querySelectorAll(".stepper").forEach((button) => button.addEventListener("click", () => {
  const input = $("#target");
  input.value = Math.max(1, Math.min(5000, Number(input.value || 0) + Number(button.dataset.step)));
  input.dispatchEvent(new Event("input"));
}));
document.querySelectorAll(".year-chip input").forEach((input) => input.addEventListener("change", () => input.closest(".year-chip").classList.toggle("checked", input.checked)));

$("#browse-folder").addEventListener("click", async () => {
  const button = $("#browse-folder");
  button.disabled = true;
  button.textContent = "Choose a folder…";
  try {
    const response = await fetch("/api/browse-folder", { method: "POST" });
    const result = await response.json();
    if (result.path) {
      $("#workspace").value = result.path;
      $("#workspace").dispatchEvent(new Event("change"));
    }
    else if (result.error) alert(result.error);
  } catch (_) { alert("The folder picker could not open. You can type the folder path instead."); }
  button.disabled = false;
  button.textContent = "Browse folders";
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (active) return;
  const selectedSources = sources.filter((x) => x.checked).map((x) => x.value);
  const payload = {
    queries: $("#queries").value,
    openalex_key: keyInput.value,
    sources: selectedSources,
    years: [...document.querySelectorAll('input[name="year"]:checked')].map((x) => Number(x.value)),
    paper_types: types.filter((x) => x.checked).map((x) => x.value),
    workspace: $("#workspace").value.trim(),
    target: Number($("#target").value),
  };
  const button = $("#start-button");
  button.disabled = true;
  button.innerHTML = "Starting…";
  try {
    const response = await fetch("/api/start", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Could not start the collection.");
    keyInput.value = "";
    keyInput.type = "password";
    $("#reveal-key").textContent = "SHOW";
    document.querySelector("#progress").scrollIntoView({ behavior: "smooth", block: "nearest" });
    await refreshStatus();
  } catch (error) {
    alert(error.message);
    button.disabled = false;
    button.innerHTML = "<span>Start collecting</span><span class=\"button-arrow\">→</span>";
  }
});

$("#stop-button").addEventListener("click", async () => {
  $("#stop-button").disabled = true;
  $("#status-message").textContent = "Stopping safely after the current paper…";
  await fetch("/api/stop", { method: "POST" });
  await refreshStatus();
});

$("#open-folder").addEventListener("click", async () => {
  const response = await fetch("/api/open-folder", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path: activeOutputPath }) });
  const result = await response.json();
  if (!response.ok) alert(result.error || "Could not open folder.");
});

refreshStatus();
window.setInterval(refreshStatus, 1200);

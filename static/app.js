const form = document.querySelector("#job-form");
const input = document.querySelector("#playlist-url");
const bitrate = document.querySelector("#bitrate");
const button = document.querySelector("#start-button");
const feedback = document.querySelector("#form-feedback");
const jobsList = document.querySelector("#jobs-list");
const emptyState = document.querySelector("#empty-state");
const workCount = document.querySelector("#work-count");
const historyFeedback = document.querySelector("#history-feedback");
const openFolderButton = document.querySelector("#open-folder-button");
const jobs = new Map();
const terminalStatuses = new Set(["completed", "completed_with_errors", "failed"]);
const statusLabels = {
  queued: "En cola",
  loading: "Verificando licencia",
  processing: "Procesando",
  completed: "Completado",
  completed_with_errors: "Completado con errores",
  failed: "Error",
};
const trackLabels = {
  queued: "En espera",
  downloading: "Descargando",
  converting: "Convirtiendo",
  processed: "Procesada",
  skipped: "Omitida · ya existe",
  failed: "Error",
};

function showFeedback(message) {
  feedback.textContent = message;
  feedback.hidden = !message;
}

function showHistoryFeedback(message, isError = false) {
  historyFeedback.textContent = message;
  historyFeedback.hidden = !message;
  historyFeedback.classList.toggle("error", isError);
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
}

function renderJob(job) {
  const tracks = job.tracks || [];
  const weights = {
    queued: 0,
    downloading: 0.25,
    converting: 0.7,
    processed: 1,
    skipped: 1,
    failed: 1,
  };
  const progress = tracks.reduce((total, track) => total + (weights[track.status] || 0), 0);
  const percent = tracks.length ? Math.round((progress / tracks.length) * 100) : 0;
  const running = !terminalStatuses.has(job.status);
  const hasFailures = job.status === "failed" || job.status === "completed_with_errors";
  const pillClass = hasFailures ? "error" : job.status === "completed" ? "" : "running";
  const details = tracks.map((track) => `
    <div class="track-row">
      <span class="track-name" title="${escapeHtml(track.name)}">${escapeHtml(track.title || track.name)}</span>
      <span class="track-state ${escapeHtml(track.status)}">${escapeHtml(trackLabels[track.status] || track.status)}</span>
      ${track.status === "failed" ? `<p class="track-error">${escapeHtml(track.message)}</p>` : ""}
    </div>`).join("");
  return `
    <article class="job-card">
      <div class="job-top">
        <div class="job-title-wrap">
          <div class="job-cover">♫</div>
          <div>
            <h3 class="job-title">${escapeHtml(job.title || job.identifier)}</h3>
            <p class="job-subtitle">${escapeHtml(job.bitrate)} KBPS · ${tracks.length ? `${tracks.length} PISTAS` : "VERIFICANDO"}</p>
          </div>
        </div>
        <span class="status-pill ${pillClass}">${escapeHtml(statusLabels[job.status] || job.status)}</span>
      </div>
      <div class="job-progress-meta"><span>${escapeHtml(job.message || "")}</span><span>${percent}%</span></div>
      <div class="progress-track" role="progressbar" aria-valuenow="${percent}" aria-valuemin="0" aria-valuemax="100">
        <div class="progress-value" style="width:${percent}%"></div>
      </div>
      ${job.folder ? `<p class="job-message">Carpeta: ${escapeHtml(job.folder)}</p>` : ""}
      ${details ? `<div class="track-list">${details}</div>` : ""}
      ${job.status === "failed" && !tracks.length ? `<p class="job-message">${escapeHtml(job.message)}</p>` : ""}
      ${running && !tracks.length ? `<p class="job-message">${escapeHtml(job.message)}</p>` : ""}
    </article>`;
}

function refreshJobs() {
  const entries = [...jobs.values()].reverse();
  jobsList.innerHTML = entries.map(renderJob).join("");
  emptyState.hidden = entries.length > 0;
  workCount.textContent = `${entries.length} ${entries.length === 1 ? "TRABAJO" : "TRABAJOS"}`;
}

async function pollJob(id) {
  try {
    const response = await fetch(`/api/jobs/${encodeURIComponent(id)}`, { cache: "no-store" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "No se pudo consultar el trabajo.");
    jobs.set(id, data);
    refreshJobs();
    if (!terminalStatuses.has(data.status)) window.setTimeout(() => pollJob(id), 1200);
  } catch (error) {
    const job = jobs.get(id);
    if (job) {
      jobs.set(id, { ...job, status: "failed", message: `No se pudo actualizar el estado: ${error.message}` });
      refreshJobs();
    }
  }
}

async function loadJobs() {
  try {
    const response = await fetch("/api/jobs", { cache: "no-store" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "No se pudo cargar el historial.");
    for (const job of result.jobs) jobs.set(job.id, job);
    openFolderButton.hidden = !result.canOpenFolder;
    refreshJobs();
    showHistoryFeedback(result.historyWarning || "", Boolean(result.historyWarning));
    for (const job of jobs.values()) {
      if (!terminalStatuses.has(job.status)) pollJob(job.id);
    }
  } catch (error) {
    showHistoryFeedback(`No se pudo cargar el historial: ${error.message}`, true);
  }
}

openFolderButton.addEventListener("click", async () => {
  openFolderButton.disabled = true;
  showHistoryFeedback("");
  try {
    const response = await fetch("/api/open-folder", { method: "POST" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "No se pudo abrir la carpeta.");
    showHistoryFeedback(`Carpeta de música abierta: ${result.folder}`);
  } catch (error) {
    showHistoryFeedback(error.message, true);
  } finally {
    openFolderButton.disabled = false;
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  showFeedback("");
  button.disabled = true;
  try {
    const response = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: input.value.trim(), bitrate: Number(bitrate.value) }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "No se pudo iniciar el trabajo.");
    const job = {
      id: result.id, identifier: input.value.trim(), bitrate: Number(bitrate.value),
      status: "queued", message: "Trabajo en cola.", title: input.value.trim(), tracks: [],
    };
    jobs.set(job.id, job);
    refreshJobs();
    input.value = "";
    document.querySelector("#trabajos").scrollIntoView({ behavior: "smooth", block: "start" });
    pollJob(job.id);
  } catch (error) {
    showFeedback(error.message);
  } finally {
    button.disabled = false;
  }
});

loadJobs();

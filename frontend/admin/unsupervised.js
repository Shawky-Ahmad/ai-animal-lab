/* AI Animal Lab — Unsupervised Learning Lab (instructor view).
   Sections: A image features (embedding heatmap + neighbors),
   B live K-means scatter, C explore clusters, D reveal answers.
   All data from same-origin /api/ml/* — no category ever enters sections A–C. */
"use strict";

(function () {
  const FETCH_TIMEOUT_MS = 10000;
  const STATUS_POLL_RUNNING_MS = 2000;
  const STATUS_POLL_IDLE_MS = 12000;
  const AUTO_STEP_MS = 1400;

  /* Fixed 8-color cluster palette + darker variants for centroid markers. */
  const CLUSTER_COLORS = ["#4f46e5", "#f59e0b", "#06b6d4", "#ef4444", "#10b981", "#a855f7", "#ec4899", "#0ea5e9"];
  const CLUSTER_DARK = ["#312e81", "#92400e", "#0e7490", "#991b1b", "#047857", "#6b21a8", "#9d174d", "#0369a1"];
  /* Fixed category colors for the reveal (assigned in sorted category order). */
  const CATEGORY_COLORS = ["#0ea5e9", "#f59e0b", "#10b981", "#ef4444", "#8b5cf6", "#14b8a6", "#f97316", "#ec4899", "#84cc16", "#6366f1"];
  const NEUTRAL_FILL = "#8a8bb5";
  const GRID = "#eef0f8";
  const MUTED = "#67668d";
  const FONT_STACK = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';
  const HEAT_COLS = 24;
  const HEAT_ROWS = 16;
  const MIXED_THRESHOLD = 0.8;

  const els = {
    setupBanner: document.getElementById("setup-banner"),
    mlDot: document.getElementById("ml-dot"),
    embedCount: document.getElementById("embed-count"),
    embedPhase: document.getElementById("embed-phase"),
    embedMeter: document.getElementById("embed-meter"),
    embedMeterFill: document.getElementById("embed-meter-fill"),
    embedBtn: document.getElementById("embed-btn"),
    pickerEmpty: document.getElementById("picker-empty"),
    pickerZone: document.getElementById("picker-zone"),
    pickerStrip: document.getElementById("picker-strip"),
    pickerSelect: document.getElementById("picker-select"),
    mainPhoto: document.getElementById("main-photo"),
    mainPhotoCap: document.getElementById("main-photo-cap"),
    heatCanvas: document.getElementById("heat-canvas"),
    nnGrid: document.getElementById("nn-grid"),
    kSelect: document.getElementById("k-select"),
    btnStart: document.getElementById("btn-start"),
    btnStep: document.getElementById("btn-step"),
    btnAuto: document.getElementById("btn-auto"),
    btnReset: document.getElementById("btn-reset"),
    runStatus: document.getElementById("run-status"),
    convergedBadge: document.getElementById("converged-badge"),
    scatterCanvas: document.getElementById("scatter"),
    scatterEmpty: document.getElementById("scatter-empty"),
    clusterChips: document.getElementById("cluster-chips"),
    clusterGridCount: document.getElementById("cluster-grid-count"),
    clusterGridEmpty: document.getElementById("cluster-grid-empty"),
    clusterGrid: document.getElementById("cluster-grid"),
    btnReveal: document.getElementById("btn-reveal"),
    revealEmpty: document.getElementById("reveal-empty"),
    revealOutput: document.getElementById("reveal-output"),
    catLegend: document.getElementById("cat-legend"),
    overallPurity: document.getElementById("overall-purity"),
    revealBars: document.getElementById("reveal-bars"),
  };

  const state = {
    data: { ids: [], filenames: [], pca: [], dim: 384 },
    points: [],            // [{x, y}] — built once per data load, never mutated
    selectedIdx: -1,       // index into data arrays
    featureToken: 0,
    lastVector: null,
    run: null,             // {run_id, k, iteration, assignments, centroids_2d, inertia, changed, converged}
    dsGlobal: [],          // datasetIndex -> [global point index]
    dataKey: "",           // ids join, to detect embedding changes
    autoTimer: null,
    autoBusy: false,
    jobRunning: false,
    mlAvailable: true,
    chart: null,
  };

  if (typeof Chart !== "undefined") {
    Chart.defaults.font.family = FONT_STACK;
    Chart.defaults.font.size = 12.5;
    Chart.defaults.color = MUTED;
    Chart.defaults.borderColor = GRID;
  }

  /* --------------------------------------------------------------------------
     Helpers
     -------------------------------------------------------------------------- */

  async function fetchJSON(url, options, extraOk) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), FETCH_TIMEOUT_MS);
    try {
      const res = await fetch(url, Object.assign({}, options, { signal: ctrl.signal }));
      let data = null;
      try { data = await res.json(); } catch (err) { /* non-JSON body */ }
      const ok = res.ok || (Array.isArray(extraOk) && extraOk.indexOf(res.status) !== -1);
      if (!ok) {
        const detail = data && data.detail ? String(data.detail) : "HTTP " + res.status;
        throw new Error(detail);
      }
      return data;
    } finally {
      clearTimeout(timer);
    }
  }

  function esc(value) {
    return String(value).replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[ch]));
  }

  function fileURL(filename) {
    return "/api/admin/files/" + encodeURIComponent(String(filename));
  }

  function fmtPct(value) {
    const n = Number(value);
    if (!isFinite(n)) return "—";
    return Math.round(n * 1000) / 10 + "%";
  }

  function fmtInertia(value) {
    const n = Number(value);
    if (!isFinite(n)) return "—";
    return n.toLocaleString(undefined, { maximumFractionDigits: 1 });
  }

  function setBusy(btn, busy) {
    btn.classList.toggle("is-busy", Boolean(busy));
  }

  /* --------------------------------------------------------------------------
     Status strip + embedding job (polled every 2 s while a job runs)
     -------------------------------------------------------------------------- */

  let statusTimer = null;

  const PHASE_TEXT = {
    idle: "Idle — embeddings are up to date.",
    loading: "Loading the neural network…",
    embedding: "Embedding",
    pca: "Computing the 2-D map…",
    saving: "Saving…",
    done: "Done — every photo now has its 384 numbers.",
    error: "Embedding failed",
  };

  function scheduleStatusPoll() {
    clearTimeout(statusTimer);
    statusTimer = setTimeout(pollStatus, state.jobRunning ? STATUS_POLL_RUNNING_MS : STATUS_POLL_IDLE_MS);
  }

  async function pollStatus() {
    try {
      const s = await fetchJSON("/api/ml/status");
      renderStatus(s && typeof s === "object" ? s : {});
    } catch (err) {
      els.mlDot.classList.add("err");
      els.embedPhase.textContent = "Cannot reach the ML service — retrying…";
    } finally {
      scheduleStatusPoll();
    }
  }

  function renderStatus(s) {
    const wasRunning = state.jobRunning;
    state.mlAvailable = Boolean(s.ml_available);
    const job = s.job && typeof s.job === "object" ? s.job : {};
    const emb = s.embeddings && typeof s.embeddings === "object" ? s.embeddings : {};
    const count = Number(emb.count) || 0;
    const total = Number(emb.images) || 0;
    const stale = Number(emb.stale) || 0;
    state.jobRunning = Boolean(job.running);
    const phase = String(job.phase || "idle");

    els.setupBanner.hidden = state.mlAvailable;

    els.embedCount.textContent = "Embeddings: " + count + " of " + total + " images" +
      (stale > 0 ? " · " + stale + " outdated" : "");

    let phaseText = PHASE_TEXT[phase] || phase;
    if (phase === "embedding") phaseText = "Embedding " + (Number(job.done) || 0) + " of " + (Number(job.total) || 0) + "…";
    if (phase === "error" && job.error) phaseText = "Embedding failed — " + String(job.error);
    els.embedPhase.textContent = phaseText;
    els.embedPhase.classList.toggle("phase-error", phase === "error");
    els.mlDot.classList.toggle("err", phase === "error");

    const showMeter = state.jobRunning && (Number(job.total) || 0) > 0;
    els.embedMeter.hidden = !showMeter;
    if (showMeter) {
      const pct = Math.max(0, Math.min(100, ((Number(job.done) || 0) * 100) / (Number(job.total) || 1)));
      els.embedMeterFill.style.width = pct + "%";
    }

    els.embedBtn.disabled = state.jobRunning || !state.mlAvailable;
    els.embedBtn.classList.toggle("is-busy", state.jobRunning);

    if (wasRunning && !state.jobRunning && (phase === "done" || phase === "idle")) {
      refreshData(); // the embedding set changed: reload images, picker and scatter
    }
    if (!wasRunning && state.jobRunning && !els.embedBtn.classList.contains("is-busy")) {
      els.embedBtn.classList.add("is-busy");
    }
  }

  async function startEmbedding() {
    els.embedBtn.disabled = true;
    setBusy(els.embedBtn, true);
    try {
      await fetchJSON("/api/ml/embed", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }, [409]);
      state.jobRunning = true;
      clearTimeout(statusTimer);
      pollStatus();
    } catch (err) {
      els.embedPhase.textContent = "Could not start embedding — " + err.message;
      els.embedBtn.disabled = false;
      setBusy(els.embedBtn, false);
    }
  }

  /* --------------------------------------------------------------------------
     Shared dataset load: /api/ml/data (no categories here, by contract)
     -------------------------------------------------------------------------- */

  async function refreshData() {
    let payload;
    try {
      payload = await fetchJSON("/api/ml/data");
    } catch (err) {
      els.pickerEmpty.hidden = false;
      els.pickerEmpty.textContent = "Could not load the image list — " + err.message;
      return;
    }
    const ids = Array.isArray(payload && payload.ids) ? payload.ids.map(Number) : [];
    const filenames = Array.isArray(payload && payload.filenames) ? payload.filenames : [];
    const pca = Array.isArray(payload && payload.pca) ? payload.pca : [];

    const prevSelectedId = state.selectedIdx >= 0 ? state.data.ids[state.selectedIdx] : null;
    state.data = { ids: ids, filenames: filenames, pca: pca, dim: Number(payload && payload.dim) || 384 };
    state.points = pca.map((p) => ({ x: Number(p[0]), y: Number(p[1]) }));
    const newKey = ids.join(",");

    if (state.run && newKey !== state.dataKey) {
      resetRun(); // embeddings changed — any previous clustering is stale
    }
    state.dataKey = newKey;

    const hasData = ids.length > 0;
    els.pickerEmpty.hidden = hasData;
    els.pickerZone.hidden = !hasData;
    els.btnStart.disabled = !hasData;
    if (!hasData) {
      els.scatterEmpty.textContent = "No images yet — compute embeddings first.";
      els.scatterEmpty.hidden = false;
    }

    renderPicker();
    renderScatterBase();
    updateRunUI(); // aligns the before-Start message with data availability

    // restore previous selection, or select the first image so the page is never blank
    let nextIdx = -1;
    if (prevSelectedId !== null) nextIdx = ids.indexOf(prevSelectedId);
    if (nextIdx < 0 && hasData) nextIdx = 0;
    if (nextIdx >= 0) selectImage(nextIdx);
    else clearFeatures();
  }

  /* --------------------------------------------------------------------------
     Section A — picker, heatmap, nearest neighbors
     -------------------------------------------------------------------------- */

  function renderPicker() {
    const ids = state.data.ids;
    const frag = document.createDocumentFragment();
    els.pickerStrip.textContent = "";
    els.pickerSelect.textContent = "";

    ids.forEach((id, i) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "pick-thumb";
      btn.dataset.idx = String(i);
      btn.title = "Image #" + id;
      btn.setAttribute("aria-label", "Image number " + id);
      const img = document.createElement("img");
      img.loading = "lazy";
      img.alt = "";
      img.src = fileURL(state.data.filenames[i]);
      btn.appendChild(img);
      frag.appendChild(btn);

      const opt = document.createElement("option");
      opt.value = String(i);
      opt.textContent = "Image #" + id;
      els.pickerSelect.appendChild(opt);
    });
    els.pickerStrip.appendChild(frag);
  }

  function selectImage(idx) {
    if (idx < 0 || idx >= state.data.ids.length) return;
    state.selectedIdx = idx;
    const id = state.data.ids[idx];

    els.pickerStrip.querySelectorAll(".pick-thumb").forEach((btn) => {
      btn.classList.toggle("selected", Number(btn.dataset.idx) === idx);
    });
    els.pickerSelect.value = String(idx);
    els.mainPhoto.src = fileURL(state.data.filenames[idx]);
    els.mainPhotoCap.textContent = "Image #" + id;

    loadFeatures(id);
  }

  function clearFeatures() {
    state.selectedIdx = -1;
    state.lastVector = null;
    els.mainPhoto.removeAttribute("src");
    els.mainPhotoCap.textContent = "—";
    els.nnGrid.textContent = "";
    drawHeatEmpty("Select an image to see its numbers.");
  }

  async function loadFeatures(imageId) {
    const token = ++state.featureToken;
    const embPromise = fetchJSON("/api/ml/embedding/" + imageId).catch((err) => ({ _error: err.message }));
    const nnPromise = fetchJSON("/api/ml/neighbors/" + imageId + "?k=5").catch((err) => ({ _error: err.message }));
    const [emb, nn] = await Promise.all([embPromise, nnPromise]);
    if (token !== state.featureToken) return; // selection moved on while fetching

    if (emb && !emb._error && Array.isArray(emb.vector)) {
      state.lastVector = emb.vector.map(Number);
      drawHeatmap(state.lastVector);
    } else {
      state.lastVector = null;
      drawHeatEmpty("No embedding for this photo yet — run the embedding job.");
    }

    els.nnGrid.textContent = "";
    if (nn && !nn._error && Array.isArray(nn.neighbors) && nn.neighbors.length > 0) {
      nn.neighbors.forEach((nb) => {
        const nid = Number(nb.image_id);
        const fig = document.createElement("figure");
        fig.className = "nn-item";
        const img = document.createElement("img");
        img.loading = "lazy";
        img.alt = "Image number " + nid;
        img.src = fileURL(nb.filename);
        const cap = document.createElement("figcaption");
        cap.innerHTML = '<span class="badge">Image #' + nid + "</span>" +
          '<span class="nn-sim">' + fmtPct(Number(nb.similarity)) + "</span>";
        fig.appendChild(img);
        fig.appendChild(cap);
        els.nnGrid.appendChild(fig);
      });
    } else {
      const p = document.createElement("p");
      p.className = "lab-empty slim";
      p.textContent = nn && nn._error ? "Neighbors unavailable — " + nn._error : "No neighbors yet.";
      els.nnGrid.appendChild(p);
    }
  }

  /* Teal → light → indigo color scale, t in [0, 1]. */
  function heatColor(t) {
    const stops = [
      [13, 148, 136],   // teal
      [224, 231, 255],  // near-white lavender
      [67, 56, 202],    // indigo
    ];
    const seg = t < 0.5 ? 0 : 1;
    const local = (t - seg * 0.5) / 0.5;
    const a = stops[seg];
    const b = stops[seg + 1];
    const r = Math.round(a[0] + (b[0] - a[0]) * local);
    const g = Math.round(a[1] + (b[1] - a[1]) * local);
    const bl = Math.round(a[2] + (b[2] - a[2]) * local);
    return "rgb(" + r + "," + g + "," + bl + ")";
  }

  function drawHeatEmpty(message) {
    const canvas = els.heatCanvas;
    const width = Math.max(280, canvas.parentElement.clientWidth || 560);
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(180 * dpr);
    canvas.style.width = width + "px";
    canvas.style.height = "180px";
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, 180);
    ctx.fillStyle = "#f7f8fe";
    ctx.fillRect(0, 0, width, 180);
    ctx.fillStyle = MUTED;
    ctx.font = "600 14px " + FONT_STACK;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(message, width / 2, 90);
  }

  function drawHeatmap(vector) {
    const canvas = els.heatCanvas;
    const padL = 40;
    const padB = 26;
    const padT = 8;
    const padR = 10;
    const width = Math.max(300, canvas.parentElement.clientWidth || 560);
    const cell = (width - padL - padR) / HEAT_COLS;
    const height = Math.round(cell * HEAT_ROWS + padT + padB);
    const dpr = window.devicePixelRatio || 1;

    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    canvas.style.width = width + "px";
    canvas.style.height = height + "px";
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);

    let min = Infinity;
    let max = -Infinity;
    for (let i = 0; i < vector.length; i++) {
      const v = Number(vector[i]);
      if (v < min) min = v;
      if (v > max) max = v;
    }
    const range = max - min || 1;

    for (let r = 0; r < HEAT_ROWS; r++) {
      for (let c = 0; c < HEAT_COLS; c++) {
        const i = r * HEAT_COLS + c;
        const v = Number(vector[i]) || 0;
        const t = (v - min) / range;
        ctx.fillStyle = heatColor(t);
        ctx.fillRect(padL + c * cell, padT + r * cell, cell - 1, cell - 1);
      }
    }

    // dimension index labels on the axes.
    // cell (row r, col c) holds dimension r*24 + c: columns are the 0..23
    // offsets (plus "383" for the very last cell), rows start at 0, 48, 96…
    ctx.fillStyle = MUTED;
    ctx.font = "600 12px " + FONT_STACK;
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    for (let c = 0; c < HEAT_COLS; c += 4) {
      ctx.fillText(String(c), padL + (c + 0.5) * cell, padT + HEAT_ROWS * cell + 6);
    }
    ctx.textAlign = "right";
    ctx.fillText("383", padL + HEAT_COLS * cell, padT + HEAT_ROWS * cell + 6);
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    for (let r = 0; r < HEAT_ROWS; r += 2) {
      ctx.fillText(String(r * HEAT_COLS), padL - 7, padT + (r + 0.5) * cell);
    }
  }

  /* --------------------------------------------------------------------------
     Section B — scatter + K-means controls
     -------------------------------------------------------------------------- */

  function ensureChart() {
    if (state.chart || typeof Chart === "undefined") return;
    const pts = state.points;
    let minX = Infinity; let maxX = -Infinity;
    let minY = Infinity; let maxY = -Infinity;
    pts.forEach((p) => {
      if (p.x < minX) minX = p.x;
      if (p.x > maxX) maxX = p.x;
      if (p.y < minY) minY = p.y;
      if (p.y > maxY) maxY = p.y;
    });
    if (!pts.length) { minX = 0; maxX = 1; minY = 0; maxY = 1; }
    const padX = (maxX - minX) * 0.07 || 0.5;
    const padY = (maxY - minY) * 0.07 || 0.5;

    state.chart = new Chart(els.scatterCanvas, {
      type: "scatter",
      data: { datasets: buildDatasets() },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { mode: "nearest", intersect: true },
        onClick(evt, elements) {
          if (!state.run) return;
          const el = elements.find((e) => e.datasetIndex >= 1 && e.datasetIndex <= state.run.k);
          if (!el) return;
          const gi = state.dsGlobal[el.datasetIndex] && state.dsGlobal[el.datasetIndex][el.index];
          if (gi === undefined) return;
          selectCluster(state.run.assignments[gi], true);
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            displayColors: false,
            callbacks: {
              title: () => "",
              label: (item) => {
                const list = state.dsGlobal[item.datasetIndex];
                const gi = list ? list[item.index] : undefined;
                if (gi === undefined) return item.dataset.label;
                const text = "Image #" + state.data.ids[gi];
                return state.run ? text + " · Cluster " + (state.run.assignments[gi] + 1) : text;
              },
            },
          },
        },
        scales: {
          x: {
            min: minX - padX, max: maxX + padX,
            grid: { color: GRID }, border: { display: false }, ticks: { display: false },
          },
          y: {
            min: minY - padY, max: maxY + padY,
            grid: { color: GRID }, border: { display: false }, ticks: { display: false },
          },
        },
      },
    });
  }

  /* Datasets are rebuilt from the SAME point objects every time, so positions
     never change — only membership and colors move. */
  function buildDatasets() {
    const pts = state.points;
    if (!state.run) {
      state.dsGlobal = [pts.map((_, i) => i)];
      return [{
        label: "Images",
        data: pts,
        backgroundColor: NEUTRAL_FILL,
        borderColor: "#ffffff",
        borderWidth: 1,
        pointRadius: 5.5,
        pointHoverRadius: 8,
      }];
    }
    const run = state.run;
    const k = run.k;
    const buckets = [];
    for (let c = 0; c < k; c++) buckets.push([]);
    run.assignments.forEach((c, gi) => {
      if (c >= 0 && c < k) buckets[c].push(gi);
    });

    const ds = [{
      label: "Images",
      data: [],
      backgroundColor: NEUTRAL_FILL,
      pointRadius: 0,
    }];
    state.dsGlobal = [[]];
    for (let c = 0; c < k; c++) {
      ds.push({
        label: "Cluster " + (c + 1),
        data: buckets[c].map((gi) => pts[gi]),
        backgroundColor: CLUSTER_COLORS[c % CLUSTER_COLORS.length],
        borderColor: "#ffffff",
        borderWidth: 1,
        pointRadius: 6.5,
        pointHoverRadius: 9,
      });
      state.dsGlobal.push(buckets[c]);
    }
    const dark = [];
    const fills = [];
    for (let c = 0; c < k; c++) {
      dark.push(CLUSTER_DARK[c % CLUSTER_DARK.length]);
      fills.push(CLUSTER_DARK[c % CLUSTER_DARK.length]);
    }
    ds.push({
      label: "Centroids",
      data: (run.centroids_2d || []).map((p) => ({ x: Number(p[0]), y: Number(p[1]) })),
      pointStyle: "crossRot",
      pointRadius: 11,
      pointHoverRadius: 13,
      borderWidth: 3.5,
      borderColor: dark,
      backgroundColor: fills,
    });
    state.dsGlobal.push([]);
    return ds;
  }

  function renderScatterBase() {
    if (!state.points.length) return;
    ensureChart();
    state.chart.data.datasets = buildDatasets();
    state.chart.update("none");
  }

  function updateRunUI() {
    const run = state.run;
    if (!run) {
      els.runStatus.textContent = "Not clustered yet.";
      els.convergedBadge.hidden = true;
      els.btnStep.disabled = true;
      els.btnAuto.disabled = true;
      els.btnReset.disabled = true;
      els.btnReveal.disabled = true;
      els.scatterEmpty.hidden = state.points.length === 0;
      if (state.points.length) els.scatterEmpty.textContent = "Pick a number of clusters, then Start.";
      return;
    }
    els.runStatus.textContent = "Iteration " + run.iteration + " · moved " + run.changed +
      " dots · inertia " + fmtInertia(run.inertia);
    els.convergedBadge.hidden = !run.converged;
    els.scatterEmpty.hidden = true;
    els.btnReset.disabled = false;
    els.btnReveal.disabled = false;
    els.btnStep.disabled = run.converged;
    els.btnAuto.disabled = run.converged;
  }

  function applyRun(resp) {
    state.run = resp && typeof resp === "object" ? resp : null;
    // Reject snapshots that do not belong to the currently loaded dataset:
    // an embed job finishing while a step request is in flight (or a run
    // started against a newer store than this page holds) must never paint
    // stale assignments over the new points.
    if (state.run && (!Array.isArray(state.run.assignments)
        || state.run.assignments.length !== state.data.ids.length)) {
      state.run = null;
    }
    if (!state.run) { updateRunUI(); return; }
    state.run.k = Number(state.run.k) || Number(els.kSelect.value) || 5;
    renderScatterBase();
    renderChips();
    els.clusterGrid.textContent = "";
    els.clusterGridEmpty.hidden = false;
    els.clusterGridEmpty.textContent = "Click a cluster chip above, or click a dot in the scatter.";
    els.clusterGridCount.textContent = "";
    els.revealOutput.hidden = true;
    els.revealEmpty.hidden = false;
    updateRunUI();
  }

  async function startClustering() {
    if (!state.points.length) return;
    stopAuto();
    setBusy(els.btnStart, true);
    els.btnStart.disabled = true;
    try {
      const resp = await fetchJSON("/api/ml/kmeans", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ k: Number(els.kSelect.value), seed: 42 }),
      });
      applyRun(resp);
    } catch (err) {
      els.runStatus.textContent = "Could not start clustering — " + err.message;
    } finally {
      setBusy(els.btnStart, false);
      els.btnStart.disabled = state.points.length === 0;
    }
  }

  async function stepOnce() {
    const run = state.run;
    if (!run || state.autoBusy) return null;
    state.autoBusy = true;
    els.btnStep.disabled = true;
    try {
      const resp = await fetchJSON("/api/ml/kmeans/step", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ run_id: run.run_id }),
      });
      applyRun(resp);
      return resp;
    } catch (err) {
      els.runStatus.textContent = "Step failed — " + err.message;
      return null;
    } finally {
      state.autoBusy = false;
      updateRunUI();
    }
  }

  function setAuto(on) {
    if (state.autoTimer) {
      clearInterval(state.autoTimer);
      state.autoTimer = null;
    }
    els.btnAuto.setAttribute("aria-pressed", on ? "true" : "false");
    els.btnAuto.textContent = on ? "Auto-playing…" : "Auto-play";
    els.btnAuto.classList.toggle("auto-on", on);
    if (on) {
      state.autoTimer = setInterval(async () => {
        const resp = await stepOnce();
        if (!resp || (state.run && state.run.converged)) setAuto(false);
      }, AUTO_STEP_MS);
    }
  }

  function stopAuto() {
    if (state.autoTimer) setAuto(false);
  }

  function resetRun() {
    stopAuto();
    state.run = null;
    state.dsGlobal = [];
    els.clusterChips.textContent = "";
    els.clusterGrid.textContent = "";
    els.clusterGridEmpty.hidden = false;
    els.clusterGridEmpty.textContent = "Run the clustering in Section B, then click a cluster to see its photos.";
    els.clusterGridCount.textContent = "";
    els.revealOutput.hidden = true;
    els.revealBars.textContent = "";
    els.catLegend.textContent = "";
    els.overallPurity.textContent = "—";
    els.btnReveal.textContent = "Reveal the true categories";
    renderScatterBase();
    updateRunUI();
  }

  /* --------------------------------------------------------------------------
     Section C — explore clusters
     -------------------------------------------------------------------------- */

  function renderChips() {
    els.clusterChips.textContent = "";
    if (!state.run) return;
    const k = state.run.k;
    const counts = new Array(k).fill(0);
    state.run.assignments.forEach((c) => {
      if (c >= 0 && c < k) counts[c] += 1;
    });
    for (let c = 0; c < k; c++) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "cluster-chip";
      btn.dataset.c = String(c);
      btn.innerHTML = '<span class="cc-dot" style="background:' + CLUSTER_COLORS[c % CLUSTER_COLORS.length] +
        '"></span>Cluster ' + (c + 1) + '<span class="cc-count">' + counts[c] + "</span>";
      els.clusterChips.appendChild(btn);
    }
  }

  function selectCluster(c, scroll) {
    const run = state.run;
    if (!run || c < 0 || c >= run.k) return;
    els.clusterChips.querySelectorAll(".cluster-chip").forEach((chip) => {
      chip.classList.toggle("selected", Number(chip.dataset.c) === c);
    });

    const members = [];
    run.assignments.forEach((assign, gi) => {
      if (assign === c) members.push(gi);
    });
    els.clusterGridEmpty.hidden = true;
    els.clusterGrid.textContent = "";
    els.clusterGridCount.textContent = members.length + (members.length === 1 ? " photo" : " photos") +
      " in Cluster " + (c + 1);

    const frag = document.createDocumentFragment();
    members.forEach((gi) => {
      const id = state.data.ids[gi];
      const fig = document.createElement("figure");
      fig.className = "tile";
      const img = document.createElement("img");
      img.loading = "lazy";
      img.alt = "Image number " + id;
      img.src = fileURL(state.data.filenames[gi]);
      const badge = document.createElement("span");
      badge.className = "cat-badge";
      badge.textContent = "Image #" + id;
      fig.appendChild(img);
      fig.appendChild(badge);
      frag.appendChild(fig);
    });
    els.clusterGrid.appendChild(frag);

    if (scroll) {
      const card = document.getElementById("card-c");
      if (card) card.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  /* --------------------------------------------------------------------------
     Section D — reveal answers (the only place categories appear)
     -------------------------------------------------------------------------- */

  function categoryColorMap(names) {
    const map = {};
    names.forEach((name, i) => {
      map[name] = CATEGORY_COLORS[i % CATEGORY_COLORS.length];
    });
    return map;
  }

  async function reveal() {
    const run = state.run;
    if (!run) return;
    setBusy(els.btnReveal, true);
    els.btnReveal.disabled = true;
    try {
      const data = await fetchJSON("/api/ml/reveal/" + encodeURIComponent(String(run.run_id)));
      renderReveal(data);
    } catch (err) {
      els.revealEmpty.hidden = false;
      els.revealEmpty.textContent = "Reveal failed — " + err.message;
    } finally {
      setBusy(els.btnReveal, false);
      els.btnReveal.disabled = false;
    }
  }

  function renderReveal(data) {
    const clusters = Array.isArray(data && data.clusters) ? data.clusters : [];
    if (!clusters.length) return;

    const catNames = [];
    clusters.forEach((cl) => {
      const dist = cl.distribution && typeof cl.distribution === "object" ? cl.distribution : {};
      Object.keys(dist).forEach((name) => {
        if (catNames.indexOf(name) === -1) catNames.push(name);
      });
    });
    catNames.sort();
    const colors = categoryColorMap(catNames);

    els.catLegend.textContent = "";
    catNames.forEach((name) => {
      const item = document.createElement("span");
      item.className = "legend-item";
      item.innerHTML = '<span class="legend-swatch" style="background:' + colors[name] + '"></span>' + esc(name);
      els.catLegend.appendChild(item);
    });

    els.overallPurity.textContent = fmtPct(data.overall_purity);

    els.revealBars.textContent = "";
    clusters.forEach((cl) => {
      const c = Number(cl.cluster);
      const size = Number(cl.size) || 0;
      const dist = cl.distribution && typeof cl.distribution === "object" ? cl.distribution : {};
      const purity = Number(cl.purity);

      const row = document.createElement("div");
      row.className = "reveal-row";

      const head = document.createElement("div");
      head.className = "reveal-row-head";
      let headHTML = '<span class="cc-dot" style="background:' + CLUSTER_COLORS[c % CLUSTER_COLORS.length] +
        '"></span><strong>Cluster ' + (c + 1) + "</strong>" +
        '<span class="reveal-size">' + size + (size === 1 ? " photo" : " photos") + "</span>";
      if (isFinite(purity)) {
        headHTML += '<span class="reveal-purity' + (purity < MIXED_THRESHOLD ? " low" : "") + '">purity ' + fmtPct(purity) + "</span>";
        if (purity < MIXED_THRESHOLD) headHTML += '<span class="mixed-chip">mixed</span>';
      }
      head.innerHTML = headHTML;
      row.appendChild(head);

      const bar = document.createElement("div");
      bar.className = "stack-bar";
      const names = Object.keys(dist).sort((a, b) => (Number(dist[b]) || 0) - (Number(dist[a]) || 0));
      if (size > 0) {
        names.forEach((name) => {
          const n = Number(dist[name]) || 0;
          if (n <= 0) return;
          const seg = document.createElement("span");
          seg.className = "stack-seg";
          seg.style.width = ((n * 100) / size) + "%";
          seg.style.background = colors[name];
          seg.title = name + ": " + n;
          bar.appendChild(seg);
        });
      }
      row.appendChild(bar);

      const purityLess = isFinite(purity) && purity < MIXED_THRESHOLD;
      const examples = Array.isArray(cl.examples) ? cl.examples : [];
      if (purityLess && examples.length) {
        const ex = document.createElement("div");
        ex.className = "reveal-examples";
        const label = document.createElement("span");
        label.className = "reveal-ex-label";
        label.textContent = "For example:";
        ex.appendChild(label);
        examples.forEach((sample) => {
          const fig = document.createElement("figure");
          fig.className = "ex-item";
          const img = document.createElement("img");
          img.loading = "lazy";
          img.alt = "Image number " + Number(sample.image_id);
          img.src = fileURL(sample.filename);
          const cap = document.createElement("figcaption");
          cap.textContent = "Image #" + Number(sample.image_id) + " · " + String(sample.category);
          fig.appendChild(img);
          fig.appendChild(cap);
          ex.appendChild(fig);
        });
        row.appendChild(ex);
      }

      els.revealBars.appendChild(row);
    });

    els.revealEmpty.hidden = true;
    els.revealOutput.hidden = false;
  }

  /* --------------------------------------------------------------------------
     Wiring
     -------------------------------------------------------------------------- */

  function buildKSelect() {
    for (let k = 2; k <= 8; k++) {
      const opt = document.createElement("option");
      opt.value = String(k);
      opt.textContent = String(k);
      if (k === 5) opt.selected = true;
      els.kSelect.appendChild(opt);
    }
  }

  let resizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (state.lastVector) drawHeatmap(state.lastVector);
      else if (state.selectedIdx < 0) drawHeatEmpty("Select an image to see its numbers.");
    }, 150);
  });

  els.embedBtn.addEventListener("click", startEmbedding);
  els.pickerStrip.addEventListener("click", (evt) => {
    const btn = evt.target.closest(".pick-thumb");
    if (btn) selectImage(Number(btn.dataset.idx));
  });
  els.pickerSelect.addEventListener("change", () => selectImage(Number(els.pickerSelect.value)));

  els.btnStart.addEventListener("click", startClustering);
  els.btnStep.addEventListener("click", () => { stopAuto(); stepOnce(); });
  els.btnAuto.addEventListener("click", () => setAuto(els.btnAuto.getAttribute("aria-pressed") !== "true"));
  els.btnReset.addEventListener("click", resetRun);

  els.clusterChips.addEventListener("click", (evt) => {
    const chip = evt.target.closest(".cluster-chip");
    if (chip) selectCluster(Number(chip.dataset.c), false);
  });

  els.btnReveal.addEventListener("click", reveal);

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      clearTimeout(statusTimer);
      pollStatus();
    }
  });

  buildKSelect();
  updateRunUI();
  drawHeatEmpty("Select an image to see its numbers.");
  refreshData();
  pollStatus();
})();

/* AI Animal Lab — live statistics dashboard.
   Polls GET /api/admin/summary every 5 s; charts update in place. */
"use strict";

(function () {
  const POLL_MS = 5000;
  const FETCH_TIMEOUT_MS = 10000;
  const PALETTE = ["#6366f1", "#8b5cf6", "#06b6d4", "#f59e0b", "#f43f5e", "#10b981", "#0ea5e9", "#a855f7"];
  const INK = "#201d45";
  const MUTED = "#67668d";
  const GRID = "#eef0f8";
  const FONT_STACK = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';

  const els = {
    statusDot: document.getElementById("status-dot"),
    statusText: document.getElementById("status-text"),
    kpiVotes: document.getElementById("kpi-votes"),
    kpiStudents: document.getElementById("kpi-students"),
    kpiImages: document.getElementById("kpi-images"),
    kpiImagesSub: document.getElementById("kpi-images-sub"),
    kpiAccuracy: document.getElementById("kpi-accuracy"),
    kpiAccuracySub: document.getElementById("kpi-accuracy-sub"),
    pcPct: document.getElementById("pc-pct"),
    pcFill: document.getElementById("pc-fill"),
    pcBar: document.getElementById("pc-bar"),
    pcLabels: document.getElementById("pc-labels"),
    pcCoverage: document.getElementById("pc-coverage"),
    emptyLine: document.getElementById("empty-line"),
    emptyBar: document.getElementById("empty-bar"),
    emptyDonut: document.getElementById("empty-donut"),
    disputeBody: document.getElementById("dispute-body"),
  };

  const charts = {};
  let lastSummary = null;
  let lastUpdated = 0;
  let pollBusy = false;

  Chart.defaults.font.family = FONT_STACK;
  Chart.defaults.font.size = 12.5;
  Chart.defaults.color = MUTED;
  Chart.defaults.borderColor = GRID;

  /* --------------------------------------------------------------------------
     Helpers
     -------------------------------------------------------------------------- */

  async function fetchJSON(url, options) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), FETCH_TIMEOUT_MS);
    try {
      const res = await fetch(url, Object.assign({}, options, { signal: ctrl.signal }));
      let data = null;
      try { data = await res.json(); } catch (err) { /* non-JSON body */ }
      if (!res.ok) {
        const detail = data && data.detail ? String(data.detail) : "HTTP " + res.status;
        throw new Error(detail);
      }
      return data;
    } finally {
      clearTimeout(timer);
    }
  }

  function fmtPct(value) {
    if (value === null || value === undefined) return "—";
    const n = Number(value);
    if (!isFinite(n)) return "—";
    const rounded = Math.round(n * 10) / 10;
    return (Number.isInteger(rounded) ? rounded.toFixed(0) : rounded.toFixed(1)) + "%";
  }

  function fmtTime(iso) {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
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

  function pulse(el) {
    el.classList.remove("refresh");
    void el.offsetWidth; // restart the CSS animation
    el.classList.add("refresh");
  }

  /* --------------------------------------------------------------------------
     Charts (created once, updated in place on every poll)
     -------------------------------------------------------------------------- */

  const centerTextPlugin = {
    id: "centerText",
    afterDraw(chart) {
      const info = chart.$centerInfo;
      const area = chart.chartArea;
      if (!info || !area) return;
      const x = (area.left + area.right) / 2;
      const y = (area.top + area.bottom) / 2;
      const ctx = chart.ctx;
      ctx.save();
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillStyle = INK;
      ctx.font = "700 26px " + FONT_STACK;
      ctx.fillText(info.value, x, y - 9);
      ctx.fillStyle = MUTED;
      ctx.font = "500 12px " + FONT_STACK;
      ctx.fillText(info.caption, x, y + 13);
      ctx.restore();
    },
  };

  function createCharts() {
    charts.line = new Chart(document.getElementById("chart-line"), {
      type: "line",
      data: {
        labels: [],
        datasets: [{
          label: "Votes",
          data: [],
          borderColor: "#6366f1",
          backgroundColor: "rgba(99, 102, 241, 0.12)",
          fill: true,
          tension: 0.35,
          borderWidth: 2,
          pointRadius: 0,
          pointHitRadius: 12,
          pointHoverRadius: 4,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 300 },
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title: (items) => items.length ? items[0].label : "",
              label: (item) => " " + item.parsed.y + (item.parsed.y === 1 ? " vote" : " votes"),
            },
          },
        },
        scales: {
          x: { grid: { display: false }, ticks: { maxTicksLimit: 10, maxRotation: 0, autoSkip: true } },
          y: { beginAtZero: true, ticks: { precision: 0 }, border: { display: false }, grid: { color: GRID } },
        },
      },
    });

    charts.bar = new Chart(document.getElementById("chart-bar"), {
      type: "bar",
      data: {
        labels: [],
        datasets: [{ data: [], backgroundColor: [], borderRadius: 8, maxBarThickness: 44 }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 300 },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (item) => {
                const row = lastSummary && Array.isArray(lastSummary.category_accuracy)
                  ? lastSummary.category_accuracy[item.dataIndex]
                  : null;
                if (!row) return " " + fmtPct(item.parsed.y);
                return " " + fmtPct(row.accuracy) + " accuracy — " + row.correct + " of " + row.votes + " votes correct";
              },
            },
          },
        },
        scales: {
          x: { grid: { display: false } },
          y: {
            min: 0,
            max: 100,
            border: { display: false },
            grid: { color: GRID },
            ticks: { callback: (v) => v + "%" },
          },
        },
      },
    });

    charts.donut = new Chart(document.getElementById("chart-donut"), {
      type: "doughnut",
      data: {
        labels: [],
        datasets: [{ data: [], backgroundColor: [], borderWidth: 2, borderColor: "#ffffff", hoverOffset: 6 }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "64%",
        animation: { duration: 300 },
        plugins: {
          legend: {
            position: "right",
            labels: { usePointStyle: true, pointStyle: "circle", boxWidth: 8, padding: 14 },
          },
          tooltip: {
            callbacks: {
              label: (item) => {
                const data = item.dataset.data || [];
                const total = data.reduce((sum, v) => sum + v, 0);
                const pct = total > 0 ? Math.round((item.parsed * 1000) / total) / 10 : 0;
                return " " + item.parsed + " votes (" + pct + "%)";
              },
            },
          },
        },
      },
      plugins: [centerTextPlugin],
    });
  }

  function updateCharts(s) {
    const points = Array.isArray(s.votes_over_time) ? s.votes_over_time : [];
    charts.line.data.labels = points.map((p) => fmtTime(p.t));
    charts.line.data.datasets[0].data = points.map((p) => Number(p.count) || 0);
    charts.line.update();
    els.emptyLine.hidden = points.length > 0;

    const acc = Array.isArray(s.category_accuracy) ? s.category_accuracy : [];
    charts.bar.data.labels = acc.map((r) => String(r.category));
    charts.bar.data.datasets[0].data = acc.map((r) => Number(r.accuracy) || 0);
    charts.bar.data.datasets[0].backgroundColor = acc.map((r, i) => PALETTE[i % PALETTE.length]);
    charts.bar.update();
    els.emptyBar.hidden = acc.length > 0;

    const dist = s.label_distribution && typeof s.label_distribution === "object" ? s.label_distribution : {};
    const keys = Object.keys(dist);
    const total = keys.reduce((sum, k) => sum + (Number(dist[k]) || 0), 0);
    charts.donut.data.labels = keys.map((k) => String(k));
    charts.donut.data.datasets[0].data = keys.map((k) => Number(dist[k]) || 0);
    charts.donut.data.datasets[0].backgroundColor = keys.map((k, i) => PALETTE[i % PALETTE.length]);
    charts.donut.$centerInfo = total > 0 ? { value: String(total), caption: "votes" } : null;
    charts.donut.update();
    els.emptyDonut.hidden = keys.length > 0;
  }

  /* --------------------------------------------------------------------------
     Disputed-images table
     -------------------------------------------------------------------------- */

  function chipsHtml(row) {
    const counts = row.counts && typeof row.counts === "object" ? row.counts : {};
    return Object.keys(counts).map((choice) =>
      '<span class="chip' + (choice === row.instructor_category ? " ok" : "") + '">' +
      esc(choice) + " ×" + (Number(counts[choice]) || 0) + "</span>"
    ).join("");
  }

  function renderTable(rows) {
    els.disputeBody.textContent = "";
    if (!Array.isArray(rows) || rows.length === 0) {
      const tr = document.createElement("tr");
      tr.className = "row-empty";
      const td = document.createElement("td");
      td.colSpan = 5;
      td.textContent = "No labels yet — this table fills in as students vote.";
      tr.appendChild(td);
      els.disputeBody.appendChild(tr);
      return;
    }
    rows.forEach((row) => {
      const accuracy = Number(row.accuracy) || 0;
      const width = Math.max(0, Math.min(100, accuracy));
      const tr = document.createElement("tr");
      tr.innerHTML =
        "<td><img class=\"thumb\" loading=\"lazy\" alt=\"\" src=\"/api/admin/files/" +
        encodeURIComponent(String(row.filename || "")) + '"></td>' +
        '<td><span class="badge">' + esc(row.instructor_category || "—") + "</span></td>" +
        '<td class="num">' + (Number(row.total_votes) || 0) + "</td>" +
        '<td><div class="chips">' + chipsHtml(row) + "</div></td>" +
        '<td><div class="acc-cell"><div class="meter' + (accuracy < 50 ? " low" : "") +
        '"><span style="width:' + width + '%"></span></div>' +
        '<span class="acc-num">' + fmtPct(row.accuracy) + "</span></div></td>";
      els.disputeBody.appendChild(tr);
    });
  }

  /* --------------------------------------------------------------------------
     Render + polling
     -------------------------------------------------------------------------- */

  function render(s) {
    const votes = Number(s.total_votes) || 0;
    const imagesTotal = Number(s.images_total) || 0;
    const imagesLabeled = Number(s.images_labeled) || 0;

    // workshop progress (projected bar): labels toward the session goal,
    // plus exact catalog coverage (photos with >= 1 label)
    const workshop = s.workshop && typeof s.workshop === "object" ? s.workshop : {};
    const goal = Number(workshop.goal) || 0;
    const pct = Math.max(0, Math.min(100, Number(workshop.percent) || 0));
    const coveragePct = imagesTotal > 0 ? Math.round((imagesLabeled * 100) / imagesTotal) : 0;

    els.pcPct.textContent = fmtPct(pct);
    els.pcFill.style.width = pct + "%";
    els.pcBar.setAttribute("aria-valuenow", String(Math.round(pct)));
    els.pcLabels.innerHTML = "<strong>" + votes.toLocaleString() + "</strong> of " +
      (goal > 0 ? goal.toLocaleString() : "—") + " labels";
    els.pcCoverage.innerHTML = "<strong>" + coveragePct + "%</strong> of photos have at least one label (" +
      imagesLabeled + " of " + imagesTotal + ")";

    els.kpiVotes.textContent = String(votes);
    els.kpiStudents.textContent = String(Number(s.student_count) || 0);
    els.kpiImages.textContent = String(imagesLabeled);
    els.kpiImagesSub.textContent = "of " + imagesTotal + " in the pool";
    els.kpiAccuracy.textContent = fmtPct(s.overall_accuracy);
    els.kpiAccuracySub.textContent = s.overall_accuracy === null || s.overall_accuracy === undefined
      ? "waiting for the first votes"
      : "votes matching instructor labels";

    updateCharts(s);
    renderTable(s.disagreement);
  }

  function setStatus(kind, message) {
    els.statusDot.classList.toggle("err", kind === "err");
    els.statusText.textContent = message;
  }

  async function poll() {
    if (pollBusy) return;
    pollBusy = true;
    try {
      const summary = await fetchJSON("/api/admin/summary");
      lastSummary = summary && typeof summary === "object" ? summary : {};
      lastUpdated = Date.now();
      render(lastSummary);
      setStatus("ok", "Updated 0s ago");
      pulse(els.statusDot);
    } catch (err) {
      setStatus("err", "Connection lost — retrying…");
    } finally {
      pollBusy = false;
    }
  }

  function tickAgo() {
    if (!lastUpdated) return;
    const secs = Math.max(0, Math.round((Date.now() - lastUpdated) / 1000));
    els.statusText.textContent = "Updated " + secs + "s ago";
  }

  /* --------------------------------------------------------------------------
     Init
     -------------------------------------------------------------------------- */

  createCharts();
  render({}); // zeroed placeholder until the first response arrives
  poll();
  setInterval(poll, POLL_MS);
  setInterval(tickAgo, 1000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) poll();
  });
})();

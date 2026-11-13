"use strict";

/* Dashboard de KPIs + gráficas en vivo (Rol C: auditoría estadística) */

const Dashboard = {
  _lastChartUpdate: 0,
  _ready: false,

  init() {
    if (typeof Chart === "undefined") return this;

    this.respChart = new Chart(document.getElementById("chart-responses"), {
      type: "line",
      data: {
        datasets: [
          {
            label: "Tiempo de respuesta (min)",
            data: [],
            borderColor: "#4da3ff",
            backgroundColor: "rgba(77,163,255,.15)",
            tension: 0.3,
            pointRadius: 2.5,
            fill: true,
          },
          {
            label: "Tiempo total (min)",
            data: [],
            borderColor: "#e67e22",
            backgroundColor: "rgba(230,126,34,.10)",
            tension: 0.3,
            pointRadius: 2,
            fill: false,
          },
        ],
      },
      options: {
        animation: false,
        maintainAspectRatio: false,
        plugins: { legend: { display: true, labels: { color: "#8fa3c0", boxWidth: 12 } } },
        scales: {
          x: {
            type: "linear",
            title: { display: true, text: "T (min de simulación)", color: "#8fa3c0" },
            ticks: { color: "#8fa3c0" },
            grid: { color: "rgba(143,163,192,.12)" },
          },
          y: {
            beginAtZero: true,
            title: { display: true, text: "min", color: "#8fa3c0" },
            ticks: { color: "#8fa3c0" },
            grid: { color: "rgba(143,163,192,.12)" },
          },
        },
      },
    });

    this.sevChart = new Chart(document.getElementById("chart-severity"), {
      type: "bar",
      data: {
        labels: SEV_NAMES,
        datasets: [
          { label: "Llegadas", data: [0, 0, 0, 0], backgroundColor: "rgba(143,163,192,.45)" },
          {
            label: "Atendidas",
            data: [0, 0, 0, 0],
            backgroundColor: SEV_COLORS.map((c) => c + "cc"),
          },
        ],
      },
      options: {
        animation: false,
        maintainAspectRatio: false,
        plugins: { legend: { labels: { color: "#8fa3c0" } } },
        scales: {
          x: { ticks: { color: "#8fa3c0" }, grid: { display: false } },
          y: {
            beginAtZero: true,
            ticks: { color: "#8fa3c0", precision: 0 },
            grid: { color: "rgba(143,163,192,.12)" },
          },
        },
      },
    });

    this._ready = true;
    return this;
  },

  update(state) {
    if (!state || !state.stats) return;
    const s = state.stats;

    setText("kpi-total", s.calls_total);
    setText("kpi-served", s.calls_served);
    setText("kpi-waiting", s.calls_waiting);
    setText("kpi-active", s.calls_active);
    setText("kpi-util", `${s.utilization_pct}%`);
    setText("kpi-busy", `${s.busy_ambulances}/${s.busy_ambulances + s.idle_ambulances}`);
    setText("kpi-avg-wait", s.avg_wait_min.toFixed(1));
    setText("kpi-avg-response", s.avg_response_min.toFixed(1));
    setText("kpi-avg-total", s.avg_total_min.toFixed(1));
    setText("kpi-avg-queue", s.avg_queue_min.toFixed(2));
    setText("kpi-max-queue", s.max_queue);

    // tabla por gravedad
    const tbody = document.getElementById("sev-tbody");
    if (tbody) {
      tbody.innerHTML = s.by_severity
        .map(
          (row) => `<tr>
            <td><span class="dot" style="background:${SEV_COLORS[row.severity]}"></span> ${row.name}</td>
            <td>${row.arrived}</td>
            <td>${row.served}</td>
            <td>${row.waiting}</td>
            <td>${row.avg_response_min.toFixed(1)}</td>
          </tr>`
        )
        .join("");
    }

    // ocupación por base/hospital
    const basesEl = document.getElementById("bases-list");
    if (basesEl) {
      basesEl.innerHTML = (state.bases || [])
        .map(
          (b) =>
            `<div class="base-stat">
              <span>${b.is_hospital ? "🏥" : "🏠"} <b>${esc(b.name)}</b></span>
              <span>${b.available}/${b.num_ambulances} libres</span>
            </div>`
        )
        .join("");
    }

    // gráficas (con limitación de refresco para no saturar el navegador)
    const now = performance.now();
    if (this._ready && now - this._lastChartUpdate > 400) {
      this._lastChartUpdate = now;

      const hist = s.response_history || [];
      this.respChart.data.datasets[0].data = hist.map((h) => ({ x: h.t, y: h.minutes }));
      this.respChart.data.datasets[1].data = hist.map((h) => ({ x: h.t, y: h.total }));
      this.respChart.update("none");

      this.sevChart.data.datasets[0].data = s.by_severity.map((r) => r.arrived);
      this.sevChart.data.datasets[1].data = s.by_severity.map((r) => r.served);
      this.sevChart.update("none");
    }
  },
};

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

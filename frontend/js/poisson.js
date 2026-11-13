"use strict";

/* VITALIS — Prueba de χ² de bondad de ajuste a un proceso de Poisson (Rol C).
 *
 * Toma el histograma agregado de la corrida desde GET /api/audit/poisson
 * (calculado en el backend sobre el dataset persistido en disco), agrupa las
 * llegadas en ventanas de Δ minutos y contrasta el histograma observado contra
 * el esperado de una distribución de Poisson con λ̂ estimada de los propios datos:
 *
 *     H0: las llegadas siguen un proceso de Poisson.
 *     χ² = Σ (O_k − E_k)² / E_k,   E_k = n · P(K=k ; μ),   μ = λ̂·Δ
 *
 * El p-valor se obtiene con la función gamma incompleta regularizada. */

const $ = (id) => document.getElementById(id);

document.addEventListener("DOMContentLoaded", () => {
  $("a-run").addEventListener("click", () => run().catch(showError));
  run().catch(showError);
});

function showError(err) {
  const el = $("a-msg");
  el.textContent = err.message;
  el.className = "msg error";
}

/* -------------------------------------------- histograma desde el backend -- */
async function loadHistogram(delta) {
  const res = await fetch(`/api/audit/poisson?delta=${encodeURIComponent(delta)}`);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const p = await res.json();
      detail = typeof p.detail === "string" ? p.detail : JSON.stringify(p.detail);
    } catch (e) { /* sin cuerpo JSON */ }
    throw new Error(detail);
  }
  return res.json();
}

/* ------------------------------------------------ contraste χ² Poisson ----- */
function chiSquarePoisson(total, nWin, horizon, obs, delta) {
  if (!total) return null;
  nWin = Math.max(1, nWin);
  const span = horizon;

  // distribuciones observada y esperada por nº de eventos k en una ventana
  // (el histograma observado `obs[k]` viene ya agrupado por el backend)
  const mu = total / nWin; // media por ventana (MLE de λ·Δ)
  const kmax = obs.length - 1;

  const exp = [];
  let p = Math.exp(-mu);
  for (let k = 0; k <= kmax; k++) {
    exp.push(nWin * p);
    p = (p * mu) / (k + 1);
  }

  // agrupar categorías para que toda E ≥ 5 (condición del χ²)
  const bins = [];
  let accO = 0, accE = 0, kStart = 0;
  for (let k = 0; k <= kmax; k++) {
    accO += obs[k];
    accE += exp[k];
    if (accE >= 5) {
      bins.push({ kStart, kEnd: k, o: accO, e: accE });
      accO = 0; accE = 0; kStart = k + 1;
    }
  }
  if (accO > 0 || accE > 0) {
    if (bins.length) {
      const last = bins[bins.length - 1];
      last.o += accO; last.e += accE; last.kEnd = kmax;
    } else {
      bins.push({ kStart, kEnd: kmax, o: accO, e: accE });
    }
  }

  let chi2 = 0;
  for (const b of bins) chi2 += ((b.o - b.e) ** 2) / b.e;

  const df = bins.length - 2; // -1 por categorías, -1 por estimar λ
  const usable = bins.length >= 3 && df >= 1 && Number.isFinite(chi2);
  const pValue = usable ? chiSquarePValue(chi2, df) : null;

  return {
    total, nWin, delta, horizon: span, mu,
    lambdaPerMin: mu / delta,
    bins, chi2, df, pValue, usable,
  };
}

/* ------------------------------------- p-valor de χ² (gamma incompleta) ---- */
function chiSquarePValue(chi2, df) {
  // Cola superior: P(χ²_df > chi2) = Q(df/2, chi2/2)
  const p = gammaQ(df / 2, chi2 / 2);
  return Number.isFinite(p) ? Math.min(1, Math.max(0, p)) : null;
}

function logGamma(x) {
  const g = 7;
  const c = [
    0.99999999999980993, 676.5203681218851, -1259.1392167224028,
    771.32342877765313, -176.61502916214059, 12.507343278686905,
    -0.13857109526572012, 9.9843695780195716e-6, 1.5056327351493116e-7,
  ];
  if (x < 0.5) return Math.log(Math.PI / Math.sin(Math.PI * x)) - logGamma(1 - x);
  x -= 1;
  let a = c[0];
  const t = x + g + 0.5;
  for (let i = 1; i < g + 2; i++) a += c[i] / (x + i);
  return 0.5 * Math.log(2 * Math.PI) + (x + 0.5) * Math.log(t) - t + Math.log(a);
}

function lowerGammaSeries(a, x) {
  let sum = 1 / a, term = sum;
  for (let n = 1; n < 500; n++) {
    term *= x / (a + n);
    sum += term;
    if (Math.abs(term) < Math.abs(sum) * 1e-12) break;
  }
  return sum * Math.exp(-x + a * Math.log(x) - logGamma(a));
}

function upperGammaCF(a, x) {
  const FPMIN = 1e-300, EPS = 1e-12;
  let b = x + 1 - a, c = 1 / FPMIN, d = 1 / b, h = d;
  for (let i = 1; i < 500; i++) {
    const an = -i * (i - a);
    b += 2;
    d = an * d + b; if (Math.abs(d) < FPMIN) d = FPMIN;
    c = b + an / c; if (Math.abs(c) < FPMIN) c = FPMIN;
    d = 1 / d;
    const del = d * c;
    h *= del;
    if (Math.abs(del - 1) < EPS) break;
  }
  return Math.exp(-x + a * Math.log(x) - logGamma(a)) * h;
}

function gammaQ(a, x) {
  if (!(a > 0) || x < 0) return NaN;
  if (x === 0) return 1;
  if (x < a + 1) return 1 - lowerGammaSeries(a, x);
  return upperGammaCF(a, x);
}

/* --------------------------------------------------------- ejecución ------- */
async function run() {
  const delta = Math.max(1, parseFloat($("a-window").value) || 10);
  const msg = $("a-msg");
  msg.textContent = "Analizando la corrida…";
  msg.className = "msg";

  const h = await loadHistogram(delta);
  const total = Number(h.total) || 0;
  if (total === 0) {
    throw new Error("El dataset está vacío. Inicia la simulación (▶ Iniciar) y déjala correr antes de analizar.");
  }

  if (total < 20) {
    msg.textContent = `Aviso: solo ${total} llamadas. Para un χ² fiable deja correr la simulación más tiempo.`;
    msg.className = "msg error";
  } else {
    msg.textContent = "";
    msg.className = "msg ok";
  }

  render(chiSquarePoisson(total, h.nWin, h.horizon, h.obs, h.delta));
}

/* ----------------------------------------------------------- render -------- */
let _chart = null;

function render(r) {
  if (!r) {
    $("a-detail").innerHTML = '<p class="hint">Sin datos.</p>';
    return;
  }
  const f = (x, d = 2) => (x === null || x === undefined ? "—" : Number(x).toFixed(d));

  $("a-summary").innerHTML = `
    <div class="audit-kpis">
      <div class="audit-kpi"><span>${r.total}</span><label>Llamadas</label></div>
      <div class="audit-kpi"><span>${r.nWin}</span><label>Ventanas</label></div>
      <div class="audit-kpi"><span>${f(r.lambdaPerMin, 3)}</span><label>λ̂ (llam/min)</label></div>
      <div class="audit-kpi"><span>${f(r.mu)}</span><label>μ por ventana</label></div>
      <div class="audit-kpi"><span>${f(r.chi2)}</span><label>χ²</label></div>
      <div class="audit-kpi"><span>${r.df}</span><label>grados libertad</label></div>
      <div class="audit-kpi"><span>${f(r.pValue, 3)}</span><label>p-valor</label></div>
    </div>`;

  const v = $("a-verdict");
  if (!r.usable) {
    v.className = "verdict warn";
    v.textContent = "⚠️ Datos insuficientes para un χ² válido (hacen falta más ventanas/categorías). Deja correr la simulación más tiempo o reduce Δ.";
  } else if (r.pValue >= 0.05) {
    v.className = "verdict ok";
    v.textContent = `✅ No se rechaza la hipótesis de Poisson (p = ${f(r.pValue, 3)} ≥ 0.05). Las llegadas de la simulación son consistentes con un proceso de Poisson de tasa λ̂ = ${f(r.lambdaPerMin, 3)} llamadas/min.`;
  } else {
    v.className = "verdict bad";
    v.textContent = `❌ Se rechaza la hipótesis de Poisson (p = ${f(r.pValue, 3)} < 0.05). Las llegadas no se ajustan bien a Poisson con Δ = ${r.delta} min (prueba con otra ventana o con más datos).`;
  }

  const rows = r.bins
    .map(
      (b) => `<tr>
        <td>${b.kStart === b.kEnd ? b.kStart : `${b.kStart}–${b.kEnd}`}</td>
        <td>${b.o}</td>
        <td>${f(b.e)}</td>
        <td>${f(((b.o - b.e) ** 2) / b.e, 3)}</td>
      </tr>`
    )
    .join("");
  $("a-detail").innerHTML = `
    <table class="audit-table">
      <thead>
        <tr><th>Llamadas por ventana (k)</th><th>Observado O</th><th>Esperado E</th><th>(O−E)²/E</th></tr>
      </thead>
      <tbody>${rows}</tbody>
      <tfoot>
        <tr><td>Total (ventanas)</td><td>${r.nWin}</td><td>${f(r.nWin, 0)}</td><td>${f(r.chi2, 3)}</td></tr>
      </tfoot>
    </table>
    <p class="hint">Δ = ${r.delta} min · horizonte ≈ ${f(r.horizon, 1)} min · las categorías con E &lt; 5 se agrupan.</p>`;

  renderChart(r.bins);
}

function renderChart(bins) {
  if (typeof Chart === "undefined") return;
  const labels = bins.map((b) => (b.kStart === b.kEnd ? `${b.kStart}` : `${b.kStart}–${b.kEnd}`));
  const data = {
    labels,
    datasets: [
      { label: "Observado", data: bins.map((b) => b.o), backgroundColor: "rgba(77,163,255,.7)" },
      { label: "Esperado (Poisson)", data: bins.map((b) => b.e), backgroundColor: "rgba(46,204,113,.7)" },
    ],
  };
  if (_chart) {
    _chart.data = data;
    _chart.update();
    return;
  }
  _chart = new Chart($("a-chart"), {
    type: "bar",
    data,
    options: {
      animation: false,
      maintainAspectRatio: false,
      plugins: { legend: { labels: { color: "#8fa3c0" } } },
      scales: {
        x: {
          ticks: { color: "#8fa3c0" },
          grid: { display: false },
          title: { display: true, text: "llamadas por ventana (k)", color: "#8fa3c0" },
        },
        y: {
          beginAtZero: true,
          ticks: { color: "#8fa3c0" },
          grid: { color: "rgba(143,163,192,.12)" },
          title: { display: true, text: "nº de ventanas", color: "#8fa3c0" },
        },
      },
    },
  });
}

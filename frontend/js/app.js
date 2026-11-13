"use strict";

/* Cableado de la interfaz: controles, fase, bases/hospitales y streaming (app.js) */

const $ = (id) => document.getElementById(id);

const DEFAULT_CENTER = { lat: 4.41361, lng: -76.1547 };

let editorTouched = false; // true si el usuario ya editó el editor de bases

// Último estado aplicado. La GUI recibe snapshots por WebSocket y (si el WS
// falla) por REST; si un snapshot viejo llega después de uno nuevo, se
// descarta para que los contadores nunca retrocedan.
let lastState = { run: null, now: -Infinity };

/* ------------------------------------------------------- utilidades -------- */
function escAttr(text) {
  return String(text == null ? "" : text)
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;");
}

async function runApi(fn, okMsg) {
  try {
    const result = await fn();
    if (result && result.state) applyState(result.state);
    if (okMsg) showMsg(okMsg, false);
  } catch (err) {
    showMsg(err.message, true);
  }
}

/* --------------------------------------------------- lectura de controles -- */
function readCenter() {
  return {
    lat: parseFloat($("p-center-lat").value) || DEFAULT_CENTER.lat,
    lng: parseFloat($("p-center-lng").value) || DEFAULT_CENTER.lng,
  };
}

function readBasesEditor() {
  return Array.from(document.querySelectorAll(".base-row")).map((row) => {
    const c = readCenter();
    return {
      name: row.querySelector(".b-name").value.trim() || "Base sin nombre",
      lat: parseFloat(row.querySelector(".b-lat").value) || c.lat,
      lng: parseFloat(row.querySelector(".b-lng").value) || c.lng,
      num_ambulances: parseInt(row.querySelector(".b-count").value, 10) || 0,
      is_hospital: row.querySelector(".b-hosp").checked,
    };
  });
}

function readParams() {
  const c = readCenter();
  return {
    seed: parseInt($("p-seed").value, 10),
    lambda_per_min: parseFloat($("p-lambda").value),
    speed_kmh: parseFloat($("p-speed").value),
    traffic_factor: parseFloat($("p-traffic").value),
    load_time_min: parseFloat($("p-load").value),
    city_radius_km: parseFloat($("p-radius").value),
    clock_speed: parseFloat($("p-clock").value),
    city_center_lat: c.lat,
    city_center_lng: c.lng,
    ambulance_bases: readBasesEditor(),
  };
}

function readLiveParams() {
  const p = readParams();
  delete p.seed; // la semilla se cambia con «Nueva semilla» (reset)
  return p;
}

function showMsg(text, isError) {
  const el = $("param-msg");
  if (!el) return;
  el.textContent = text;
  el.className = `msg ${isError ? "error" : "ok"}`;
  clearTimeout(showMsg._t);
  showMsg._t = setTimeout(() => {
    el.textContent = "";
    el.className = "msg";
  }, 5000);
}

function setPhase(phase) {
  const badge = $("phase-badge");
  if (!badge) return;
  const labels = { idle: "DETENIDA", running: "EJECUTANDO", paused: "PAUSADA" };
  badge.textContent = labels[phase] || phase;
  badge.className = `badge ${phase}`;
}

function syncNumber(id, value, fallback) {
  const el = $(id);
  if (!el || value === undefined) return;
  if (document.activeElement === el) return; // no pisar un campo en edición
  const target = value || fallback;
  if (parseFloat(el.value) !== target) el.value = target;
}

function sameBases(a, b) {
  if (a.length !== b.length) return false;
  return a.every((x, i) => {
    const y = b[i];
    return (
      x.name === y.name &&
      Math.abs(x.lat - y.lat) < 1e-9 &&
      Math.abs(x.lng - y.lng) < 1e-9 &&
      x.num_ambulances === y.num_ambulances &&
      x.is_hospital === y.is_hospital
    );
  });
}

function applyState(state) {
  if (!state) return;
  // Orden: cada corrida tiene un run_id propio y, dentro de ella, `now` solo
  // avanza. Se descarta cualquier snapshot de la misma corrida con un reloj
  // anterior (p. ej. una respuesta REST que llega tarde).
  if (state.run_id === lastState.run && state.now < lastState.now) return;
  lastState = { run: state.run_id, now: state.now };
  setPhase(state.phase);
  if (state.seed !== undefined) $("seed-value").textContent = state.seed;
  if (state.now !== undefined) $("sim-clock").textContent = state.now.toFixed(1);
  const params = state.params;
  if (params) {
    syncNumber("p-center-lat", params.city_center_lat, DEFAULT_CENTER.lat);
    syncNumber("p-center-lng", params.city_center_lng, DEFAULT_CENTER.lng);
    if (!editorTouched && params.ambulance_bases) {
      if (!sameBases(readBasesEditor(), params.ambulance_bases)) {
        populateBasesEditor(params.ambulance_bases);
      }
    }
  }
  if (state.stats) {
    MapView.update(state);
    Dashboard.update(state);
  }
}

/* ---------------------------------------------------- editor de bases ----- */
function addBaseRow(spec) {
  const c = readCenter();
  const s = Object.assign(
    { name: "", lat: c.lat, lng: c.lng, num_ambulances: 1, is_hospital: true },
    spec || {}
  );
  const row = document.createElement("div");
  row.className = "base-row";
  row.innerHTML = `
    <div class="base-head">
      <label class="chk" title="Marcada = es hospital y RECIBE los pacientes trasladados">
        <input type="checkbox" class="b-hosp" ${s.is_hospital ? "checked" : ""} /> 🏥 Atiende pacientes
      </label>
      <button type="button" class="btn-x" title="Quitar base">✖</button>
    </div>
    <div class="fields">
      <label class="span2">Nombre <input type="text" class="b-name" value="${escAttr(s.name)}" placeholder="Ej. Hospital San Juan de Dios" /></label>
      <label>Lat <input type="number" step="any" class="b-lat" value="${s.lat}" /></label>
      <label>Lng <input type="number" step="any" class="b-lng" value="${s.lng}" /></label>
      <label>Ambulancias <input type="number" min="0" max="30" class="b-count" value="${s.num_ambulances}" /></label>
      <label class="span2 hint"> Lat/Lng de la base, no del centro.</label>
    </div>`;
  row.querySelector(".btn-x").addEventListener("click", () => {
    editorTouched = true;
    row.remove();
  });
  row.addEventListener("input", () => {
    editorTouched = true;
  });
  return row;
}

function populateBasesEditor(specs) {
  const box = $("bases-editor");
  box.innerHTML = "";
  (specs && specs.length ? specs : []).forEach((s) => box.appendChild(addBaseRow(s)));
}

function bindBasesEditor() {
  let clickAddMode = false;

  const setClickAddMode = (on) => {
    clickAddMode = on;
    const btn = $("btn-clic-base");
    btn.classList.toggle("active", on);
    $("clic-label").textContent = on
      ? "clic activo — Esc para salir"
      : "clic en el mapa";
    MapView.enableClickToAdd(on, (lat, lng) => {
      editorTouched = true;
      const row = addBaseRow({ name: "", lat, lng, is_hospital: false });
      $("bases-editor").appendChild(row);
      row.scrollIntoView({ behavior: "smooth", block: "nearest" });
      const nameInput = row.querySelector(".b-name");
      nameInput.focus();
      nameInput.select();
      showMsg(
        `📍 Nueva base en ${lat.toFixed(6)}, ${lng.toFixed(6)} — ponle nombre, nº de ambulancias y marca si atiende pacientes.`,
        false
      );
    });
  };

  $("btn-add-base").addEventListener("click", () => {
    editorTouched = true;
    $("bases-editor").appendChild(
      addBaseRow({ name: "", is_hospital: false })
    );
  });

  $("btn-clic-base").addEventListener("click", () => setClickAddMode(!clickAddMode));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && clickAddMode) setClickAddMode(false);
  });

  $("btn-apply-bases").addEventListener("click", () => {
    const bases = readBasesEditor();
    if (!bases.length) {
      return showMsg("Agrega al menos una base (➕ Añadir base).", true);
    }
    if (!bases.some((b) => b.is_hospital)) {
      return showMsg("Marca al menos una base como hospital (🏥 Atiende pacientes).", true);
    }
    const c = readCenter();
    runApi(
      () =>
        API.patchParams({
          city_center_lat: c.lat,
          city_center_lng: c.lng,
          ambulance_bases: bases,
        }),
      "📍 Ciudad y bases aplicadas EN CALIENTE."
    );
  });
}

/* ------------------------------------------------------------- controles -- */
function bindControls() {
  $("btn-start").addEventListener("click", () =>
    runApi(
      () => API.start(readParams()),
      "▶ Simulación iniciada con la configuración actual."
    )
  );
  $("btn-pause").addEventListener("click", () => runApi(() => API.pause(), "⏸ Simulación en pausa."));
  $("btn-resume").addEventListener("click", () => runApi(() => API.resume(), "⏵ Simulación reanudada."));
  $("btn-reset").addEventListener("click", () =>
    runApi(
      () => API.reset({ seed: parseInt($("p-seed").value, 10) }),
      "↺ Corrida reiniciada: misma semilla ⇒ misma trayectoria."
    )
  );
  $("btn-step").addEventListener("click", () =>
    runApi(() => API.step({ minutes: 5 }), "⏭ Avance manual de 5 minutos.")
  );
  $("btn-apply").addEventListener("click", () =>
    runApi(
      () => API.patchParams(readLiveParams()),
      "🔥 Parámetros aplicados EN CALIENTE (la corrida continúa)."
    )
  );
  $("btn-dataset").addEventListener("click", () =>
    API.downloadDataset().catch((err) => showMsg(err.message, true))
  );
  $("btn-poisson").addEventListener("click", () =>
    window.open("poisson.html", "_blank")
  );
}

function init() {
  MapView.init();
  Dashboard.init();
  bindControls();
  bindBasesEditor();

  // filas por defecto: hospital que atiende + estación de bomberos (despacho)
  populateBasesEditor([
    {
      name: "Hospital Departamental San Antonio",
      lat: 4.404525,
      lng: -76.152172,
      num_ambulances: 4,
      is_hospital: true,
    },
    {
      name: "Cuerpo de Bomberos Voluntarios",
      lat: 4.407223,
      lng: -76.152638,
      num_ambulances: 2,
      is_hospital: false,
    },
  ]);

  Realtime.onState = applyState;
  Realtime.onStatus = (connected) => {
    const el = $("ws-indicator");
    if (!el) return;
    el.textContent = connected ? "● en vivo" : "○ reconectando…";
    el.classList.toggle("off", !connected);
  };
  Realtime.connect();

  // Estado inicial + respaldo por REST SOLO si el WebSocket no está conectado.
  // Tener los dos canales activos hacía que un snapshot REST viejo pisara uno
  // del WebSocket y los KPIs retrocedieran (λ alto, reloj rápido).
  API.state()
    .then((state) => applyState(state))
    .catch(() => setPhase("idle"));
  setInterval(() => {
    if (Realtime.isOpen()) return;
    API.state()
      .then((state) => applyState(state))
      .catch(() => {});
  }, 3000);
}

document.addEventListener("DOMContentLoaded", init);
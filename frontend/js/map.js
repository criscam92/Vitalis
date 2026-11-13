"use strict";

/* Mapa interactivo + animación en tiempo real (Rol C) */

// Origen del sistema de coordenadas de simulación (km) sobre la ciudad.
// (x = este, y = norte). El centro de ciudad es editable desde la GUI.
const MAP_ORIGIN_DEFAULT = { lat: 4.41361, lng: -76.1547 };
let mapOrigin = { ...MAP_ORIGIN_DEFAULT };

const SEV_COLORS = ["#e74c3c", "#e67e22", "#f1c40f", "#2ecc71"];
const SEV_NAMES = ["Rojo", "Naranja", "Amarillo", "Verde"];

const STATUS_LABEL = {
  libre: "Libre",
  en_camino: "En camino al sitio",
  en_escena: "Atendiendo en escena",
  traslado: "Traslado al hospital",
};

const CALL_STATUS_LABEL = {
  en_camino: "Ambulancia en camino",
  en_escena: "Ambulancia en escena",
  traslado: "Traslado al hospital",
};

// Proveedores de mapa base (todos WGS84: las mismas lat/lng se ven en cada uno).
// Google se usa sin API key mediante los endpoints clásicos de tiles (mt0..mt3);
// OpenStreetMap y Esri son los proveedores públicos estándar.
const BASE_PROVIDERS = {
  osm: {
    name: "OpenStreetMap (Leaflet)",
    url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    options: { maxZoom: 19, attribution: "&copy; OpenStreetMap contributors" },
  },
  google_roads: {
    name: "Google Maps · calles",
    url: "https://{s}.google.com/vt/lyrs=m&x={x}&y={y}&z={z}",
    options: {
      subdomains: ["mt0", "mt1", "mt2", "mt3"],
      maxZoom: 20,
      attribution: "&copy; Google",
    },
  },
  google_hybrid: {
    name: "Google Maps · satélite",
    url: "https://{s}.google.com/vt/lyrs=y&x={x}&y={y}&z={z}",
    options: {
      subdomains: ["mt0", "mt1", "mt2", "mt3"],
      maxZoom: 20,
      attribution: "&copy; Google",
    },
  },
  esri: {
    name: "Esri World Imagery",
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    options: {
      maxZoom: 19,
      attribution: "&copy; Esri, Maxar, Earthstar Geographics",
    },
  },
};

// Proyección local espejo del backend (app/core/geo.py):
//   y_km = (lat - center_lat) * 110.574
//   x_km = (lng - center_lng) * 111.32 * cos(center_lat)
function toLatLng(x, y) {
  const lat = mapOrigin.lat + y / 110.574;
  const lng =
    mapOrigin.lng + x / (111.32 * Math.cos((mapOrigin.lat * Math.PI) / 180));
  return [lat, lng];
}

function esc(text) {
  const div = document.createElement("div");
  div.textContent = text == null ? "" : String(text);
  return div.innerHTML;
}

const MapView = {
  init() {
    this.map = L.map("map", { zoomControl: true }).setView(toLatLng(0, 0), 13);

    // Capas base intercambiables (OSM por defecto; Google/Esri opcionales)
    this._baseLayers = {};
    for (const [key, provider] of Object.entries(BASE_PROVIDERS)) {
      this._baseLayers[key] = L.tileLayer(provider.url, provider.options);
    }
    this._currentBase = "osm";
    this._baseLayers.osm.addTo(this.map);

    // Control para cambiar el proveedor del mapa (arriba a la derecha)
    const view = this;
    const ProviderControl = L.Control.extend({
      options: { position: "topright" },
      onAdd() {
        const container = L.DomUtil.create("div", "layer-switcher");
        const options = Object.keys(BASE_PROVIDERS)
          .map((k) => `<option value="${k}">${BASE_PROVIDERS[k].name}</option>`)
          .join("");
        container.innerHTML = `<select class="layer-select" title="Mapa base">${options}</select>`;
        container.querySelector("select").value = view._currentBase;
        L.DomEvent.disableClickPropagation(container);
        L.DomEvent.disableScrollPropagation(container);
        L.DomEvent.on(container.querySelector("select"), "change", (e) => {
          view.setProvider(e.target.value);
        });
        return container;
      },
    });
    this.map.addControl(new ProviderControl());

    this._origin = [mapOrigin.lat, mapOrigin.lng];
    this.ambMarkers = new Map(); // id -> marker
    this.callMarkers = new Map(); // id -> marker
    this.routeLines = new Map(); // ambId -> polyline
    this.baseMarkers = new Map(); // baseId -> marker

    // Área de cobertura (se redibuja alrededor del centro configurable)
    this.coverage = L.circle(toLatLng(0, 0), {
      radius: 6000,
      color: "#4da3ff",
      weight: 3,
      opacity: 0.9,
      fillOpacity: 0.2,
      dashArray: "6 8",
    }).addTo(this.map);

    this._routeLayer = L.layerGroup().addTo(this.map);

    // Modo "añadir base con un clic": solo dispara cuando enableClickToAdd(true)
    // está activo (lo gestiona app.js).
    this._clickAdd = false;
    this._clickAddCb = null;
    this.map.on("click", (e) => {
      if (!this._clickAdd || !this._clickAddCb) return;
      this._clickAddCb(
        Math.round(e.latlng.lat * 1e6) / 1e6,
        Math.round(e.latlng.lng * 1e6) / 1e6
      );
    });

    return this;
  },

  /** Cambia el proveedor del mapa base (OSM / Google / Esri) sin mover marcadores. */
  setProvider(key) {
    if (key === this._currentBase || !this._baseLayers[key]) return;
    this.map.removeLayer(this._baseLayers[this._currentBase]);
    this._baseLayers[key].addTo(this.map);
    this._currentBase = key;
  },

  /** Activa/desactiva el modo "añadir base con un clic en el mapa". */
  enableClickToAdd(active, cb) {
    this._clickAdd = !!active;
    this._clickAddCb = typeof cb === "function" ? cb : null;
    if (this.map) {
      this.map.getContainer().classList.toggle("map-click-to-add", this._clickAdd);
    }
  },

  /** Cambia el centro de la ciudad (lat/lng) y recentra la proyección. */
  setOrigin(lat, lng) {
    mapOrigin = { lat, lng };
    this._origin = [lat, lng];
    if (this.coverage) this.coverage.setLatLng(toLatLng(0, 0));
    if (this.map) {
      this.map.panTo(toLatLng(0, 0));
      // los marcadores de bases se reposicionan en el próximo update()
      this._updateBases(this._lastState || { bases: [] });
    }
  },

  update(state) {
    if (!this.map || !state) return;
    this._lastState = state;
    const params = state.params || {};
    if (params.city_radius_km) {
      this.coverage.setRadius(params.city_radius_km * 1000);
    }
    // si el centro cambió (se aplicó desde la GUI) => nueva proyección
    if (
      params.city_center_lat !== undefined &&
      params.city_center_lng !== undefined &&
      (params.city_center_lat !== this._origin[0] ||
        params.city_center_lng !== this._origin[1])
    ) {
      this.setOrigin(params.city_center_lat, params.city_center_lng);
    }
    // el círculo sigue siempre centrado en el origen de simulación (0,0)
    this.coverage.setLatLng(toLatLng(0, 0));
    this._updateBases(state);
    this._updateAmbulances(state);
    this._updateCalls(state);
    this._updateRoutes(state);
  },

  _updateBases(state) {
    const seen = new Set();
    for (const base of state.bases || []) {
      seen.add(base.id);
      let marker = this.baseMarkers.get(base.id);
      if (!marker) {
        const cls = base.is_hospital ? "hospital-marker" : "station-marker";
        const icon = L.divIcon({
          className: "marker-wrap",
          html: `<div class="${cls}" title="${esc(base.name)}">${
            base.is_hospital ? "🏥" : "🏠"
          }</div>`,
          iconSize: [30, 30],
          iconAnchor: [15, 15],
        });
        marker = L.marker(toLatLng(base.x, base.y), {
          icon,
          zIndexOffset: 600,
          keyboard: false,
        }).addTo(this.map);
        marker.bindTooltip("", { direction: "top", offset: [0, -16] });
        this.baseMarkers.set(base.id, marker);
      }
      marker.setLatLng(toLatLng(base.x, base.y));
      const kind = base.is_hospital
        ? '<span class="c-hosp">🏥 atiende pacientes</span>'
        : '<span class="c-move">🏠 estación de despacho · no atiende pacientes</span>';
      marker.setTooltipContent(
        `<b>${esc(base.name)}</b> ${kind}<br>` +
          `Flota: <b>${base.num_ambulances}</b> · libres: <b>${base.available}</b> · ocupadas: <b>${base.busy}</b>` +
          `<br>${base.lat.toFixed(5)}, ${base.lng.toFixed(5)}`
      );
    }
    for (const [id, marker] of this.baseMarkers) {
      if (!seen.has(id)) {
        this.map.removeLayer(marker);
        this.baseMarkers.delete(id);
      }
    }
  },

  _updateAmbulances(state) {
    const seen = new Set();
    for (const amb of state.ambulances || []) {
      seen.add(amb.id);
      let marker = this.ambMarkers.get(amb.id);
      if (!marker) {
        const icon = L.divIcon({
          className: "marker-wrap",
          html: `<div class="amb-marker ${amb.status}" data-id="${amb.id}">🚑</div>`,
          iconSize: [30, 30],
          iconAnchor: [15, 15],
        });
        marker = L.marker(toLatLng(amb.x, amb.y), {
          icon,
          zIndexOffset: 500,
          keyboard: false,
        }).addTo(this.map);
        marker.bindTooltip("", { direction: "top", offset: [0, -14] });
        this.ambMarkers.set(amb.id, marker);
      }
      marker.setLatLng(toLatLng(amb.x, amb.y));

      // actualiza el anillo de estado sin recrear el ícono (sin parpadeo)
      const el = marker.getElement();
      if (el) {
        const div = el.querySelector(".amb-marker");
        if (div && div.dataset.status !== amb.status) {
          div.dataset.status = amb.status;
          div.className = `amb-marker ${amb.status}`;
        }
      }
      const callText = amb.call_id ? ` · llamada #${amb.call_id}` : "";
      marker.setTooltipContent(
        `<b>Ambulancia ${amb.id + 1}</b><br>${STATUS_LABEL[amb.status] || amb.status}${callText}`
      );
    }
    for (const [id, marker] of this.ambMarkers) {
      if (!seen.has(id)) {
        this.map.removeLayer(marker);
        this.ambMarkers.delete(id);
      }
    }
  },

  _updateCalls(state) {
    const seen = new Set();
    const draw = (call, active) => {
      seen.add(call.id);
      let marker = this.callMarkers.get(call.id);
      const cls = `call-marker sev-${call.severity} ${active ? "active" : "waiting"}`;
      if (!marker) {
        const icon = L.divIcon({
          className: "marker-wrap",
          html: `<div class="${cls}"></div>`,
          iconSize: [18, 18],
          iconAnchor: [9, 9],
        });
        marker = L.marker(toLatLng(call.x, call.y), {
          icon,
          zIndexOffset: active ? 200 : 400,
          keyboard: false,
        }).addTo(this.map);
        this.callMarkers.set(call.id, marker);
      }
      marker.setLatLng(toLatLng(call.x, call.y));
      const el = marker.getElement();
      if (el) {
        const div = el.querySelector(".call-marker");
        if (div) div.className = cls;
      }
      const detail = active
        ? `${CALL_STATUS_LABEL[call.status] || call.status} · ${call.elapsed_min} min`
        : `esperando ambulancia · ${call.wait_min} min`;
      marker.setTooltipContent(
        `<b>Llamada #${call.id}</b> — ${call.severity_name}<br>${detail}`
      );
    };

    for (const call of state.waiting_calls || []) draw(call, false);
    for (const call of state.active_calls || []) draw(call, true);

    for (const [id, marker] of this.callMarkers) {
      if (!seen.has(id)) {
        this.map.removeLayer(marker);
        this.callMarkers.delete(id);
      }
    }
  },

  _updateRoutes(state) {
    const seen = new Set();
    for (const amb of state.ambulances || []) {
      if (!amb.target) continue;
      seen.add(amb.id);
      const latlngs = [toLatLng(amb.x, amb.y), toLatLng(amb.target.x, amb.target.y)];
      let line = this.routeLines.get(amb.id);
      if (!line) {
        line = L.polyline(latlngs, {
          color: "#4da3ff",
          weight: 2,
          opacity: 0.75,
          dashArray: "5 7",
        });
        this._routeLayer.addLayer(line);
        this.routeLines.set(amb.id, line);
      } else {
        line.setLatLngs(latlngs);
      }
    }
    for (const [id, line] of this.routeLines) {
      if (!seen.has(id)) {
        this._routeLayer.removeLayer(line);
        this.routeLines.delete(id);
      }
    }
  },
};

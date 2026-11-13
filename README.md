# 🚑 VITALIS — Sistema Logístico y de Despacho de Emergencias Médicas

**Proyecto Final · Modelos de Simulación (8.° Semestre) · UNINTEP**
Docente: Profesor Julián Meléndez.

Simulador **híbrido** (eventos discretos + dinámica continua) de un sistema de
despacho de ambulancias: llamadas entrantes con **tasas estocásticas de Poisson**,
**tiempo de viaje continuo por distancia euclídea** y un **mapa interactivo con
animación en vivo** de la flota.

---

## Requisitos

- [uv](https://docs.astral.sh/uv/) (gestor de entornos y dependencias) — o Python ≥ 3.11 + pip
- Navegador con acceso a internet (mapa OpenStreetMap/Google/Esri y CDN de Leaflet/Chart.js)

## Puesta en marcha

```bash
# 1. instalar dependencias (crea el entorno .venv; no se incluye en la entrega)
uv sync

# 2. arrancar el servidor (sirve API + interfaz)
uv run uvicorn app.main:app --app-dir backend --reload
```

Abrir **http://127.0.0.1:8000/** → la interfaz, el mapa y los controles quedan listos.

> Si no se dispone de `uv`, instalar con `pip install -r requirements.txt` (mismas dependencias).

## API REST

| Método | Ruta | Descripción |
|---|---|---|
| `POST` | `/api/sim/start` | (Re)inicia con semilla + parámetros |
| `POST` | `/api/sim/pause` · `/api/sim/resume` | Pausa / reanudación |
| `POST` | `/api/sim/reset` | Reinicio; con `{"seed": N}` cambia la semilla |
| `POST` | `/api/sim/step` | Avance manual (`{"minutes": 5}`) |
| `PATCH` | `/api/params` | Cambio **en caliente** de λ, velocidad, tráfico, **centro de ciudad y bases/hospitales**, reloj… |
| `GET` | `/api/state` · `/api/stats` | Snapshot completo / KPIs |
| `GET` | `/api/dataset` | CSV de la corrida (auditoría), servido en *streaming* desde disco |
| `GET` | `/api/audit/poisson` | Histograma agregado (nº de ventanas con k llamadas) para el χ² |
| `WS` | `/ws/sim` | Streaming del estado ~10 Hz (animación) |
| `GET` | `/docs` | Documentación OpenAPI generada |

## Auditoría estadística (GUI)

Desde el panel **📜 Auditoría estadística** de la interfaz:

- **💾 Descargar CSV (dataset)** — descarga `vitalis_llamadas.csv` con el registro completo de llamadas de la corrida (`GET /api/dataset`, servido en *streaming*).
- **🧪 Prueba de Poisson** — abre `poisson.html`: toma el histograma agregado de la corrida (`GET /api/audit/poisson`), agrupa las llegadas en ventanas de Δ minutos y aplica un **χ² de bondad de ajuste** a un proceso de Poisson (λ̂ estimado de los propios datos), mostrando el histograma *observado vs esperado*, el estadístico χ², los grados de libertad y el **p-valor** con su veredicto.

> **Memoria acotada, auditoría completa.** Cada llamada terminada se persiste en una base
> SQLite local (`data/vitalis_audit.db`) y se libera de la RAM. Así la simulación puede
> correr indefinidamente —incluso hasta agotar el período del PRNG— **sin saturar la
> memoria** y **sin perder la auditoría**. Cada `Iniciar`/`Reiniciar` abre una *corrida*
> nueva de auditoría, y el archivo de datos está excluido del repositorio (`.gitignore`).

## Arquitectura de la simulación

```
         eventos discretos                         continua
 ┌───────────────────────────────────┐    ┌─────────────────────────────────┐
 │ ARRIVAL   → llamada Poisson (λ)   │    │ posición(t) = lerp(inicio,fin)  │
 │ ASIGNAR   → unidad libre más      │───►│ t_viaje = (d/v)·60·coef_tráfico │
 │ EN ESCENA → carga ±25 % aleatorio │    │ (distancia euclídea en km)      │
 │ HOSPITAL  → libera y atiende cola │    └─────────────────────────────────┘
 └───────────────────────────────────┘
        reloj de simulación (min) salta de evento en evento;
        entre eventos la posición se interpola -> animación fluida.
```

- **Cola con prioridad**: Rojo > Naranja > Amarillo > Verde, FIFO dentro de cada nivel.
- **Asignación**: unidad libre más cercana (distancia euclídea al cuadrado).
- **Múltiples bases configurable**: cada punto puede ser **hospital** (recibe pacientes)
  o **estación** de ambulancias; tras atender, la unidad traslada al paciente al
  **hospital más cercano** (`is_hospital=True`). Centro de ciudad y bases se editan
  desde la GUI (`📍 Ciudad y hospitales (bases)`). Por defecto: **Hospital Departamental
  San Antonio** (4 amb. · atiende) + **Cuerpo de Bomberos Voluntarios** (2 amb. · solo despacho).
- **Mapa base conmutable**: selector en el mapa para alternar entre OpenStreetMap
  (Leaflet), **Google Maps** (calles o satélite/híbrido) y **Esri World Imagery**.
- **Reproducibilidad**: misma semilla ⇒ misma corrida, exacta paso a paso.
- **Métricas por llamada**: espera = `t_asignación − t_llegada`; **respuesta =
  `t_subida − t_llegada`** (espera + viaje al sitio + carga en escena, es decir hasta que
  el paciente está a bordo); total = `t_fin − t_llegada`.
- **Streaming acotado**: el WebSocket envía como máximo **150 llamadas en espera** (las
  más urgentes) para mantener fluida la animación aunque la cola crezca; los KPIs, la
  gráfica y el CSV contabilizan **todas**. Cada snapshot lleva un `run_id` y la GUI
  descarta los estados que lleguen fuera de orden, de modo que los contadores nunca
  retroceden.

## Repartición de roles

| Rol | Responsable(s) | Responsabilidad | Módulos principales |
|---|---|---|---|
| **A** — Arquitectura de hardware y modelado físico | **Cristian Camilo Herrera Muñoz** | Formulación: distancia euclídea, cinemática del vehículo, coeficiente de tráfico, tasas físicas | `entities.py`, `engine.py::_travel_time` |
| **B** — Ingeniería de software y motores de simulación | **Francisco Javier García García** · **Carlos Augusto Calvo Castillo** | PRNG, reloj, calendario de eventos, colas, backend FastAPI | `prng.py`, `distributions.py`, `engine.py`, `api/` |
| **C** — Diseño de interfaces (GUI) y datos | **Carlos Andrés Vélez Córdoba** | Mapa Leaflet, animación en vivo, KPIs, dataset CSV, auditoría estadística | `frontend/`, `stats.py` |

## Estructura del proyecto

```
Vitalis_Entrega_Final/
├── pyproject.toml / requirements.txt
├── README.md
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI + bucle en tiempo real + estáticos
│   │   ├── schemas.py           # validación de parámetros (bases/hospitales)
│   │   ├── core/
│   │   │   ├── prng.py          # FASE 1 · PRNG U(0,1) con semilla
│   │   │   ├── distributions.py # FASE 1 · exponencial (tiempos entre llegadas)
│   │   │   ├── geo.py           # proyección lat/lng <-> km (espejo de map.js)
│   │   │   ├── entities.py      # llamadas, ambulancias, bases (cinemática)
│   │   │   ├── engine.py        # FASE 2 · motor híbrido (multi-base)
│   │   │   ├── stats.py         # KPIs y auditoría estadística
│   │   │   └── audit.py         # auditoría persistente en disco (SQLite)
│   │   └── api/
│   │       ├── routes.py        # REST
│   │       └── ws.py            # WebSocket en vivo
└── frontend/
    ├── index.html           # FASE 3 · GUI
    ├── poisson.html         # FASE 3 · auditoría: prueba de Poisson (χ²)
    ├── css/style.css
    └── js/
        ├── api.js           # REST + WebSocket + descarga del dataset
        ├── map.js           # Leaflet: marcadores y rutas animadas
        ├── dashboard.js     # KPIs + gráficas Chart.js
        ├── poisson.js       # χ² de bondad de ajuste a Poisson (datos reales)
        └── app.js           # controles y fase de simulación
```
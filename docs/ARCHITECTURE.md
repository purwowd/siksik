# SATRIA — Arsitektur Modular

Dokumen ini menjelaskan struktur proyek backend (FastAPI) dan frontend (React/Vite) setelah modularisasi.

## Prinsip

| Lapisan | Backend | Frontend |
|---------|---------|----------|
| **Entry** | `app/main.py`, `run.py` | `src/main.tsx`, `src/app/App.tsx` |
| **HTTP / routing** | `app/api/v1/*` (router per domain) | `src/app/routes.ts` |
| **Domain logic** | `app/{acquisition,selection,dashboard,services}` | `src/features/*` |
| **Shared infra** | `app/core`, `app/models` | `src/shared/*` |
| **Cross-cutting** | `app/api/deps.py` | `src/shared/ui`, hooks |

---

## Backend (`backend/app/`)

```
app/
├── main.py                 # FastAPI app, middleware, lifespan
├── api/
│   ├── deps.py             # Pagination, media path, GPU helpers
│   ├── routes.py           # Re-export → v1.router (compat)
│   └── v1/
│       ├── router.py       # Aggregator semua sub-router
│       ├── health.py
│       ├── auth.py
│       ├── devices.py
│       ├── agent.py
│       ├── sessions.py
│       ├── selection.py
│       ├── findings.py
│       ├── gallery.py
│       ├── media.py
│       ├── reports.py
│       ├── dashboard.py
│       └── admin.py
├── core/                   # Config, DB, logging, branding
├── models/                 # Pydantic DTOs (schemas per domain + barrel schemas.py)
├── acquisition/
│   └── orchestration.py    # Pipeline akuisisi (canonical)
├── selection/              # Crawl & review kandidat
├── dashboard/              # Statistik agregat (stats.py)
└── services/
    └── acquisition.py      # Shim → acquisition.orchestration (compat)
```

### Konvensi API

- Semua endpoint tetap di prefix `/api/v1` (tidak breaking).
- Satu file router = satu domain HTTP.
- Query/SQL bersama → `api/deps.py`; logika bisnis berat → package domain (`acquisition/`, `dashboard/`).

---

## Frontend (`frontend/src/`)

```
src/
├── main.tsx
├── app/
│   ├── App.tsx             # Entry tipis — hook + shell
│   ├── AppShell.tsx        # Layout, nav, route outlet
│   ├── hooks/useConsoleApp.ts  # State & side-effects konsol
│   └── routes.ts           # URL builder & query params
├── features/
│   ├── auth/components/
│   ├── sessions/components/ + hooks/useSessionStream.ts
│   ├── operator/           # OperatorPage + pipeline UI
│   ├── findings/
│   ├── gallery/
│   ├── report/
│   └── dashboard/          # DashboardPage + lib (satriaModules, labels)
├── shared/
│   ├── api/                # http.ts, endpoints.ts, types/, media.ts
│   ├── constants/
│   ├── types/
│   ├── lib/                # workflow, mediaFetchQueue
│   └── ui/                 # Pagination, Toast, PanelTitle, EmptyState
└── styles/
    ├── index.css           # src/styles.css (barrel @import)
    ├── base.css
    ├── satria-dash.css
    ├── enterprise.css
    └── extras.css
```

### Alias import

Gunakan `@/` untuk import absolut:

```ts
import { api } from "@/shared/api/client";
import { OperatorPage } from "@/features/operator/OperatorPage";
```

Dikonfigurasi di `vite.config.ts` (`resolve.alias`) dan `tsconfig.json` (`paths`).

### Migrasi berikutnya (opsional)

1. Pecah `App.tsx` → providers + hooks per feature (`useFindings`, `useSession`)
2. Pecah `shared/api/client.ts` → `types/` per domain
3. Co-locate CSS per feature di `styles/features/`

---

## Menjalankan

```bash
# Backend
cd backend && python run.py

# Frontend
cd frontend && npm run dev

# Docker (smoke)
docker compose up --build
```

Lihat juga [`docs/LAB_HOST.md`](LAB_HOST.md) untuk perbedaan host lab vs kontainer.

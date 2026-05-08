# GEX Dashboard

Per-strike Gamma Exposure dashboard for **SPX**, with native projection onto **ES futures price space** via basis adjustment. Live-streaming-ready architecture (Databento `OPRA.PILLAR` for options, `GLBX.MDP3` for ES futures).

## Stack

- **Backend** — Python 3.11, FastAPI, Poetry, Databento, NumPy/SciPy.
- **Frontend** — Vite + React + TypeScript + Tailwind + shadcn/ui + Recharts.

## Layout

```
backend/   FastAPI app (gex/basis/greeks math + Databento client + REST API)
frontend/  Vite + React + TS dashboard
```

## Running locally

### Backend

Requires Poetry and Python 3.11+. Two Databento keys (or one legacy key) are read from the environment:

| Env var | Dataset |
| --- | --- |
| `DATABENTO_API_KEY_OPRA` | `OPRA.PILLAR` (US options) |
| `DATABENTO_API_KEY_GLBX` | `GLBX.MDP3` (ES futures) |
| `DATABENTO_API_KEY` | Legacy fallback (used for both if specific key absent) |

```bash
cd backend
poetry install
poetry run fastapi dev app/main.py
# REST: http://localhost:8000/api/health
#       http://localhost:8000/api/expiries/SPX
#       http://localhost:8000/api/gex/SPX?expiry=YYYY-MM-DD
#       http://localhost:8000/api/gex/SPX?use_mock=true   # offline demo
```

Quality gates:

```bash
poetry run ruff check app/
poetry run mypy app/
poetry run pytest
```

### Frontend

Requires Node 20+ and npm.

```bash
cd frontend
cp .env.example .env       # adjust VITE_API_BASE_URL if backend not on :8000
npm install
npm run dev                # http://localhost:5173
```

Quality gates:

```bash
npm run lint
npm run build
```

## GEX model

- Greeks via Black–Scholes; IV solved per strike from NBBO mid via Brent's method.
- Per-strike GEX (USD per 1 % move):

  ```
  call_gex = -gamma_call * OI_call * 100 * S²
  put_gex  = +gamma_put  * OI_put  * 100 * S²
  net_gex  =  call_gex + put_gex
  ```

  Sign convention follows the SqueezeMetrics dealer-naive model: dealers
  short calls (negative gamma) and long puts (positive gamma).

- **Basis** — `basis = ES_front − SPX_synthetic_forward`, where
  `SPX_synthetic_forward ≈ K + (C_mid − P_mid)` at the ATM strike of a
  short-dated expiry (put-call parity).

- **ES projection** — flip the strike axis to `strike + basis` so each level
  is plotted in ES futures price space; reference line tracks the front-month
  ES last price.

## Roadmap

- [x] REST snapshot endpoint + Recharts dashboard (this PR).
- [ ] Databento Live engine (OPRA `cmbp-1` + `definition` + `statistics`,
      GLBX `mbp-1` ES front-month) with in-memory chain state.
- [ ] WebSocket `/ws/gex/SPX` broadcast + animated frontend updates.
- [ ] Heatmap across multiple expiries.
- [ ] Multi-symbol expansion (NDX/QQQ → NQ, RUT/IWM → RTY).

## License

Private repo — owned by the user.

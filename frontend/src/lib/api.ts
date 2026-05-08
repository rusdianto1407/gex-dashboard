// Typed API client matching backend Pydantic models in `backend/app/models.py`.
// Stable contract; bumped when backend ships a breaking change.

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "")

export interface GexLevel {
  strike: number
  call_oi: number
  put_oi: number
  call_iv: number | null
  put_iv: number | null
  call_gamma: number | null
  put_gamma: number | null
  call_gex: number
  put_gex: number
  net_gex: number
}

export interface ExpiryInfo {
  expiry: string // ISO date
  dte: number
  instrument_count: number
}

export interface BasisInfo {
  spot: number
  futures: number
  basis: number
  front_month_symbol: string
  futures_expiry: string | null
  spot_source: string
}

export interface GexSnapshot {
  symbol: string
  expiry: string
  as_of: string
  is_live: boolean
  is_mock: boolean
  levels: GexLevel[]
  total_call_gex: number
  total_put_gex: number
  total_net_gex: number
  gamma_flip: number | null
  largest_positive_strike: number | null
  largest_negative_strike: number | null
  basis: BasisInfo
}

export interface Health {
  status: string
  opra_key_configured: boolean
  glbx_key_configured: boolean
  version: string
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { headers: { Accept: "application/json" } })
  if (!res.ok) {
    const text = await res.text().catch(() => "")
    throw new Error(`${res.status} ${res.statusText}: ${text || path}`)
  }
  return (await res.json()) as T
}

export const api = {
  health: () => getJson<Health>("/api/health"),
  symbols: () => getJson<string[]>("/api/symbols"),
  expiries: (symbol: string, opts: { useMock?: boolean; horizonDays?: number } = {}) => {
    const params = new URLSearchParams()
    if (opts.useMock) params.set("use_mock", "true")
    if (opts.horizonDays) params.set("horizon_days", String(opts.horizonDays))
    const qs = params.toString()
    return getJson<ExpiryInfo[]>(`/api/expiries/${symbol}${qs ? `?${qs}` : ""}`)
  },
  gex: (
    symbol: string,
    opts: { expiry?: string; useMock?: boolean } = {},
  ) => {
    const params = new URLSearchParams()
    if (opts.expiry) params.set("expiry", opts.expiry)
    if (opts.useMock) params.set("use_mock", "true")
    const qs = params.toString()
    return getJson<GexSnapshot>(`/api/gex/${symbol}${qs ? `?${qs}` : ""}`)
  },
}

export const fmt = {
  /** Compact USD: $1.2B / $34.7M / $987K */
  usd(value: number, opts: { signed?: boolean } = {}): string {
    const sign = opts.signed && value > 0 ? "+" : ""
    const abs = Math.abs(value)
    if (abs >= 1e9) return `${sign}${(value / 1e9).toFixed(2)}B`
    if (abs >= 1e6) return `${sign}${(value / 1e6).toFixed(2)}M`
    if (abs >= 1e3) return `${sign}${(value / 1e3).toFixed(1)}K`
    return `${sign}${value.toFixed(0)}`
  },
  price(value: number): string {
    return value.toLocaleString("en-US", { maximumFractionDigits: 2, minimumFractionDigits: 2 })
  },
  signed(value: number, decimals = 2): string {
    const sign = value > 0 ? "+" : ""
    return `${sign}${value.toFixed(decimals)}`
  },
  date(iso: string): string {
    const d = new Date(iso)
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })
  },
  time(iso: string): string {
    const d = new Date(iso)
    return d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit" })
  },
}

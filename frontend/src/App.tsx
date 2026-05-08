import { useEffect, useMemo, useState } from "react"
import { Activity, AlertTriangle, FlaskConical, Radio } from "lucide-react"

import { AxisToggle, type AxisMode } from "@/components/AxisToggle"
import { ExpirySelector } from "@/components/ExpirySelector"
import { GexChart } from "@/components/GexChart"
import { MetricTile } from "@/components/MetricTile"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { api, fmt, type ExpiryInfo, type GexSnapshot, type Health } from "@/lib/api"
import { type StreamStatus, useGexStream } from "@/lib/useGexStream"

const SYMBOL = "SPX"

function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [expiries, setExpiries] = useState<ExpiryInfo[]>([])
  const [expiry, setExpiry] = useState<string | undefined>(undefined)
  const [axis, setAxis] = useState<AxisMode>("spot")
  const [useMock, setUseMock] = useState(false)
  const [expiriesLoading, setExpiriesLoading] = useState(true)
  const [expiriesError, setExpiriesError] = useState<string | null>(null)

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null))
  }, [])

  useEffect(() => {
    let cancelled = false
    setExpiriesLoading(true)
    setExpiriesError(null)
    api
      .expiries(SYMBOL, { useMock })
      .then((rows) => {
        if (cancelled) return
        setExpiries(rows)
        setExpiry((current) => {
          if (current && rows.some((r) => r.expiry === current)) return current
          return rows[0]?.expiry
        })
      })
      .catch((e: Error) => {
        if (!cancelled) setExpiriesError(e.message)
      })
      .finally(() => {
        if (!cancelled) setExpiriesLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [useMock])

  const { snapshot, status, error, lastUpdateAt } = useGexStream({
    symbol: SYMBOL,
    expiry,
    useMock,
    enabled: Boolean(expiry),
  })

  const isLoading = !snapshot && (expiriesLoading || status === "connecting" || status === "idle")
  const fatalError = expiriesError ?? (snapshot ? null : error)

  const totalNetSign = useMemo(() => {
    if (!snapshot) return 0
    return Math.sign(snapshot.total_net_gex)
  }, [snapshot])

  return (
    <div className="min-h-screen bg-[#0a0a0f] text-zinc-100">
      <div className="pointer-events-none fixed inset-0 -z-10">
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top_left,rgba(52,211,153,0.10),transparent_55%)]" />
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_bottom_right,rgba(96,165,250,0.10),transparent_55%)]" />
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,rgba(167,139,250,0.05),transparent_70%)]" />
      </div>

      <div className="mx-auto max-w-[1400px] px-6 py-8">
        <Header
          snapshot={snapshot}
          health={health}
          status={status}
          lastUpdateAt={lastUpdateAt}
          useMock={useMock}
          onUseMockChange={setUseMock}
        />

        <Controls
          expiries={expiries}
          expiry={expiry}
          onExpiryChange={setExpiry}
          axis={axis}
          onAxisChange={setAxis}
          loading={expiriesLoading && expiries.length === 0}
          basisHint={
            snapshot ? (
              <span className="text-zinc-500">
                basis ={" "}
                <span
                  className={
                    snapshot.basis.basis >= 0 ? "text-emerald-300" : "text-rose-300"
                  }
                >
                  {fmt.signed(snapshot.basis.basis, 2)}
                </span>{" "}
                pts ({snapshot.basis.front_month_symbol})
              </span>
            ) : null
          }
        />

        <Metrics snapshot={snapshot} loading={isLoading} />

        <ChartCard
          snapshot={snapshot}
          axis={axis}
          loading={isLoading}
          error={fatalError}
          totalNetSign={totalNetSign}
        />

        <Footer />
      </div>
    </div>
  )
}

function StreamPill({
  status,
  isMock,
  lastUpdateAt,
}: {
  status: StreamStatus
  isMock: boolean
  lastUpdateAt: number | null
}) {
  if (isMock) {
    return (
      <Badge variant="mock">
        <FlaskConical className="h-3 w-3" />
        Mock
      </Badge>
    )
  }
  const cfg = pillConfigForStatus(status)
  return (
    <Badge variant={cfg.variant}>
      <span className={`h-1.5 w-1.5 rounded-full ${cfg.dotClass}`} />
      {cfg.label}
      {lastUpdateAt && status !== "idle" && (
        <span className="ml-1 text-[10px] text-zinc-500 tabular-nums">
          {ageString(lastUpdateAt)}
        </span>
      )}
    </Badge>
  )
}

function pillConfigForStatus(status: StreamStatus): {
  label: string
  variant: "live" | "positive" | "negative" | "mock"
  dotClass: string
} {
  switch (status) {
    case "live":
      return {
        label: "Live",
        variant: "live",
        dotClass: "bg-emerald-400 [animation:pulse_1.5s_ease-in-out_infinite]",
      }
    case "fallback":
      return {
        label: "REST fallback",
        variant: "positive",
        dotClass: "bg-amber-400",
      }
    case "connecting":
    case "idle":
      return {
        label: "Connecting",
        variant: "positive",
        dotClass: "bg-zinc-500 [animation:pulse_1s_ease-in-out_infinite]",
      }
    case "error":
      return {
        label: "Disconnected",
        variant: "negative",
        dotClass: "bg-rose-400",
      }
  }
}

function ageString(ts: number): string {
  const seconds = Math.max(0, Math.floor((Date.now() - ts) / 1000))
  if (seconds < 5) return "now"
  if (seconds < 60) return `${seconds}s ago`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  return `${Math.floor(minutes / 60)}h ago`
}

function Header({
  snapshot,
  health,
  status,
  lastUpdateAt,
  useMock,
  onUseMockChange,
}: {
  snapshot: GexSnapshot | null
  health: Health | null
  status: StreamStatus
  lastUpdateAt: number | null
  useMock: boolean
  onUseMockChange: (v: boolean) => void
}) {
  const isMockData = snapshot?.is_mock ?? useMock
  return (
    <header className="mb-7 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <div className="flex items-center gap-3">
          <div className="grid h-9 w-9 place-items-center rounded-xl bg-gradient-to-br from-emerald-500/30 to-sky-500/20 ring-1 ring-emerald-500/40">
            <Activity className="h-4 w-4 text-emerald-300" />
          </div>
          <div>
            <h1 className="text-xl font-semibold tracking-tight">
              GEX Dashboard <span className="text-zinc-500 font-normal">·</span>{" "}
              <span className="text-emerald-300">SPX</span>
            </h1>
            <p className="mt-0.5 text-xs text-zinc-500">
              Per-strike Gamma Exposure with ES futures basis projection
              {snapshot && (
                <>
                  {" · "}
                  <span className="tabular-nums">{fmt.time(snapshot.as_of)} UTC</span>
                </>
              )}
            </p>
          </div>
        </div>
      </div>
      <div className="flex items-center gap-3">
        <StreamPill status={status} isMock={isMockData} lastUpdateAt={lastUpdateAt} />
        <div className="flex items-center gap-2 rounded-full border border-zinc-800/70 bg-zinc-900/40 px-3 py-1.5 backdrop-blur-xl">
          <Radio className="h-3 w-3 text-zinc-500" />
          <span className="text-[11px] uppercase tracking-wider text-zinc-500">Mock</span>
          <Switch checked={useMock} onCheckedChange={onUseMockChange} />
        </div>
        {health && !health.opra_key_configured && (
          <Badge variant="negative">
            <AlertTriangle className="h-3 w-3" />
            OPRA key missing
          </Badge>
        )}
      </div>
    </header>
  )
}

function Controls({
  expiries,
  expiry,
  onExpiryChange,
  axis,
  onAxisChange,
  loading,
  basisHint,
}: {
  expiries: ExpiryInfo[]
  expiry: string | undefined
  onExpiryChange: (v: string) => void
  axis: AxisMode
  onAxisChange: (v: AxisMode) => void
  loading: boolean
  basisHint: React.ReactNode
}) {
  return (
    <div className="mb-5 flex flex-wrap items-center gap-3 rounded-2xl border border-zinc-800/70 bg-zinc-900/40 p-3 backdrop-blur-xl">
      <div className="flex items-center gap-2">
        <span className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">
          Expiry
        </span>
        <ExpirySelector
          expiries={expiries}
          value={expiry}
          onChange={onExpiryChange}
          loading={loading}
        />
      </div>
      <div className="hidden h-6 w-px bg-zinc-800 md:block" />
      <div className="flex items-center gap-2">
        <span className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">
          Axis
        </span>
        <AxisToggle value={axis} onChange={onAxisChange} />
      </div>
      <div className="ml-auto text-[11px] tabular-nums">{basisHint}</div>
    </div>
  )
}

function Metrics({
  snapshot,
  loading,
}: {
  snapshot: GexSnapshot | null
  loading: boolean
}) {
  if (!snapshot && loading) {
    return (
      <div className="mb-5 grid grid-cols-2 gap-3 md:grid-cols-5">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-[88px]" />
        ))}
      </div>
    )
  }
  if (!snapshot) return null

  const { basis, total_net_gex, total_call_gex, total_put_gex, gamma_flip } = snapshot
  return (
    <div className="mb-5 grid grid-cols-2 gap-3 md:grid-cols-5">
      <MetricTile
        label="SPX spot"
        value={fmt.price(basis.spot)}
        hint={basis.spot_source}
        accent="amber"
      />
      <MetricTile
        label={`ES front (${basis.front_month_symbol.split(" ")[0]})`}
        value={fmt.price(basis.futures)}
        hint={basis.futures_expiry ? `expires ${fmt.date(basis.futures_expiry)}` : undefined}
        accent="sky"
      />
      <MetricTile
        label="Basis (F − S)"
        value={fmt.signed(basis.basis, 2)}
        hint={`${basis.basis >= 0 ? "contango" : "backwardation"} pts`}
        accent={basis.basis >= 0 ? "emerald" : "rose"}
      />
      <MetricTile
        label="Net GEX"
        value={fmt.usd(total_net_gex, { signed: true })}
        hint={`calls ${fmt.usd(total_call_gex, { signed: true })} · puts ${fmt.usd(total_put_gex, { signed: true })}`}
        accent={total_net_gex >= 0 ? "emerald" : "rose"}
      />
      <MetricTile
        label="Gamma flip"
        value={gamma_flip !== null ? gamma_flip.toFixed(0) : "—"}
        hint="Σ net-GEX zero crossing"
        accent="violet"
      />
    </div>
  )
}

function ChartCard({
  snapshot,
  axis,
  loading,
  error,
  totalNetSign,
}: {
  snapshot: GexSnapshot | null
  axis: AxisMode
  loading: boolean
  error: string | null
  totalNetSign: number
}) {
  return (
    <div className="rounded-2xl border border-zinc-800/70 bg-zinc-900/40 p-5 backdrop-blur-xl shadow-[0_8px_30px_rgba(0,0,0,0.35)]">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">
            Per-strike Net GEX
          </div>
          <h2 className="mt-1 text-lg font-semibold tracking-tight">
            {snapshot ? (
              <>
                {snapshot.symbol} · {fmt.date(snapshot.expiry)}{" "}
                <span className="text-zinc-500 font-normal">
                  ({((new Date(snapshot.expiry).getTime() - Date.now()) / 86_400_000).toFixed(0)}{" "}
                  DTE)
                </span>
              </>
            ) : (
              "Loading…"
            )}
          </h2>
          <p className="mt-1 text-xs text-zinc-500">
            Naive dealer convention · dealers <span className="text-rose-300">short calls</span>,{" "}
            <span className="text-emerald-300">long puts</span> · USD per 1% move
          </p>
        </div>
        {snapshot && (
          <div className="flex items-center gap-2">
            <Badge variant={totalNetSign >= 0 ? "positive" : "negative"}>
              {totalNetSign >= 0 ? "Net long gamma" : "Net short gamma"}
            </Badge>
            {snapshot.largest_positive_strike !== null && (
              <span className="text-xs text-zinc-500">
                Call wall <span className="text-zinc-300 tabular-nums">
                  {snapshot.largest_positive_strike.toFixed(0)}
                </span>
              </span>
            )}
            {snapshot.largest_negative_strike !== null && (
              <span className="text-xs text-zinc-500">
                Put wall <span className="text-zinc-300 tabular-nums">
                  {snapshot.largest_negative_strike.toFixed(0)}
                </span>
              </span>
            )}
          </div>
        )}
      </div>

      <div className="h-[600px]">
        {error ? (
          <div className="flex h-full items-center justify-center">
            <div className="text-center text-rose-300">
              <AlertTriangle className="mx-auto mb-2 h-6 w-6" />
              <div className="text-sm font-medium">Failed to load snapshot</div>
              <div className="mt-1 text-xs text-rose-300/70">{error}</div>
            </div>
          </div>
        ) : !snapshot && loading ? (
          <Skeleton className="h-full w-full" />
        ) : !snapshot ? (
          <div className="flex h-full items-center justify-center text-zinc-500">
            No data
          </div>
        ) : (
          <GexChart snapshot={snapshot} axis={axis} />
        )}
      </div>
    </div>
  )
}

function Footer() {
  return (
    <footer className="mt-8 flex flex-wrap items-center justify-between gap-3 text-[11px] text-zinc-600">
      <div>
        Data via Databento · OPRA.PILLAR (options) + GLBX.MDP3 (futures) · Greeks via Black-Scholes
      </div>
      <div>
        Sign convention: dealer short calls, long puts (a la SqueezeMetrics) · GEX in USD per 1% move
      </div>
    </footer>
  )
}

export default App

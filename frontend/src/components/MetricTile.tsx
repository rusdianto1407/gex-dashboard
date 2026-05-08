import { cn } from "@/lib/utils"

interface MetricTileProps {
  label: string
  value: string
  hint?: string
  accent?: "default" | "emerald" | "rose" | "amber" | "sky" | "violet"
  loading?: boolean
}

const ACCENT: Record<NonNullable<MetricTileProps["accent"]>, string> = {
  default: "text-zinc-100",
  emerald: "text-emerald-300",
  rose: "text-rose-300",
  amber: "text-amber-300",
  sky: "text-sky-300",
  violet: "text-violet-300",
}

const GLOW: Record<NonNullable<MetricTileProps["accent"]>, string> = {
  default: "from-zinc-700/0 via-zinc-700/0 to-zinc-700/0",
  emerald: "from-emerald-500/10 via-emerald-500/0 to-emerald-500/0",
  rose: "from-rose-500/10 via-rose-500/0 to-rose-500/0",
  amber: "from-amber-500/10 via-amber-500/0 to-amber-500/0",
  sky: "from-sky-500/10 via-sky-500/0 to-sky-500/0",
  violet: "from-violet-500/10 via-violet-500/0 to-violet-500/0",
}

export function MetricTile({ label, value, hint, accent = "default", loading }: MetricTileProps) {
  return (
    <div className="relative overflow-hidden rounded-2xl border border-zinc-800/70 bg-zinc-900/40 backdrop-blur-xl px-5 py-4 transition-all hover:border-zinc-700/80">
      <div
        className={cn(
          "absolute inset-0 bg-gradient-to-br opacity-80 pointer-events-none",
          GLOW[accent],
        )}
      />
      <div className="relative">
        <div className="text-[11px] font-medium uppercase tracking-[0.14em] text-zinc-500">
          {label}
        </div>
        <div
          className={cn(
            "mt-1.5 text-2xl font-semibold tabular-nums tracking-tight",
            ACCENT[accent],
            loading && "opacity-30",
          )}
        >
          {value}
        </div>
        {hint && (
          <div className="mt-1 text-[11px] tabular-nums text-zinc-500">{hint}</div>
        )}
      </div>
    </div>
  )
}

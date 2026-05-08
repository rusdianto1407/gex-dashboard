import { useMemo } from "react"
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"

import type { GexSnapshot } from "@/lib/api"
import { fmt } from "@/lib/api"
import type { AxisMode } from "@/components/AxisToggle"

interface GexChartProps {
  snapshot: GexSnapshot
  axis: AxisMode
}

interface ChartDatum {
  display_strike: number
  raw_strike: number
  net_gex: number
  call_gex: number
  put_gex: number
  call_oi: number
  put_oi: number
}

const COLOR_POS = "#34d399" // emerald-400
const COLOR_NEG = "#fb7185" // rose-400
const COLOR_GRID = "#27272a" // zinc-800
const COLOR_AXIS_LBL = "#71717a" // zinc-500
const COLOR_SPOT = "#fbbf24" // amber-400
const COLOR_FUTURES = "#60a5fa" // sky-400 (slightly lighter than 500)
const COLOR_FLIP = "#a78bfa" // violet-400

export function GexChart({ snapshot, axis }: GexChartProps) {
  const basis = snapshot.basis.basis
  const data: ChartDatum[] = useMemo(() => {
    return snapshot.levels
      .filter((lv) => lv.net_gex !== 0 || lv.call_gex !== 0 || lv.put_gex !== 0)
      .map((lv) => ({
        raw_strike: lv.strike,
        display_strike: axis === "futures" ? lv.strike + basis : lv.strike,
        net_gex: lv.net_gex,
        call_gex: lv.call_gex,
        put_gex: lv.put_gex,
        call_oi: lv.call_oi,
        put_oi: lv.put_oi,
      }))
      .sort((a, b) => a.display_strike - b.display_strike)
  }, [snapshot.levels, basis, axis])

  const referenceLevel = axis === "futures" ? snapshot.basis.futures : snapshot.basis.spot
  const referenceLabel = axis === "futures" ? "ES Front" : "SPX Spot"
  const referenceColor = axis === "futures" ? COLOR_FUTURES : COLOR_SPOT
  const flipDisplay =
    snapshot.gamma_flip === null
      ? null
      : axis === "futures"
        ? snapshot.gamma_flip + basis
        : snapshot.gamma_flip

  const xDomain = useMemo<[number, number]>(() => {
    if (data.length === 0) return [-1, 1]
    const max = Math.max(...data.map((d) => Math.abs(d.net_gex)), 1)
    return [-max * 1.1, max * 1.1]
  }, [data])

  const yDomain = useMemo<[number, number]>(() => {
    if (data.length === 0) return [0, 1]
    const min = data[0].display_strike
    const max = data[data.length - 1].display_strike
    const pad = Math.max((max - min) * 0.02, 0.5)
    return [min - pad, max + pad]
  }, [data])

  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart
        data={data}
        layout="vertical"
        margin={{ top: 16, right: 32, bottom: 8, left: 56 }}
        barCategoryGap={1}
      >
        <defs>
          <linearGradient id="bar-pos" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor={COLOR_POS} stopOpacity={0.85} />
            <stop offset="100%" stopColor={COLOR_POS} stopOpacity={0.55} />
          </linearGradient>
          <linearGradient id="bar-neg" x1="1" y1="0" x2="0" y2="0">
            <stop offset="0%" stopColor={COLOR_NEG} stopOpacity={0.85} />
            <stop offset="100%" stopColor={COLOR_NEG} stopOpacity={0.55} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke={COLOR_GRID} strokeDasharray="3 3" horizontal={false} />
        <XAxis
          type="number"
          domain={xDomain}
          tickFormatter={(v) => fmt.usd(v as number, { signed: true })}
          stroke={COLOR_AXIS_LBL}
          tick={{ fill: COLOR_AXIS_LBL, fontSize: 11 }}
          axisLine={false}
          tickLine={false}
        />
        <YAxis
          dataKey="display_strike"
          type="number"
          domain={yDomain}
          allowDecimals={false}
          stroke={COLOR_AXIS_LBL}
          tick={{ fill: COLOR_AXIS_LBL, fontSize: 11 }}
          axisLine={false}
          tickLine={false}
          width={56}
          tickFormatter={(v) => (v as number).toFixed(0)}
        />
        <ReferenceLine x={0} stroke="#3f3f46" strokeWidth={1} />
        <ReferenceLine
          y={referenceLevel}
          stroke={referenceColor}
          strokeDasharray="6 4"
          strokeWidth={1.5}
          label={{
            value: `${referenceLabel} ${fmt.price(referenceLevel)}`,
            position: "right",
            fill: referenceColor,
            fontSize: 11,
            fontWeight: 600,
          }}
          ifOverflow="extendDomain"
        />
        {flipDisplay !== null && (
          <ReferenceLine
            y={flipDisplay}
            stroke={COLOR_FLIP}
            strokeDasharray="2 4"
            strokeWidth={1}
            label={{
              value: `Γ-flip ${flipDisplay.toFixed(0)}`,
              position: "left",
              fill: COLOR_FLIP,
              fontSize: 10,
            }}
          />
        )}
        <Tooltip
          cursor={{ fill: "rgba(255,255,255,0.04)" }}
          content={<GexTooltip />}
          isAnimationActive={false}
        />
        <Bar dataKey="net_gex" barSize={6} radius={[2, 2, 2, 2]} isAnimationActive={false}>
          {data.map((entry, index) => (
            <Cell
              key={index}
              fill={entry.net_gex >= 0 ? "url(#bar-pos)" : "url(#bar-neg)"}
            />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}

interface TooltipPayloadDatum {
  payload: ChartDatum
}
function GexTooltip({ active, payload }: { active?: boolean; payload?: TooltipPayloadDatum[] }) {
  if (!active || !payload || payload.length === 0) return null
  const d = payload[0].payload
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-950/90 px-3.5 py-2.5 text-xs text-zinc-100 shadow-2xl backdrop-blur-xl">
      <div className="text-[11px] uppercase tracking-wider text-zinc-500">Strike</div>
      <div className="mb-1.5 font-semibold tabular-nums">
        {d.display_strike.toFixed(0)}
        {d.display_strike !== d.raw_strike && (
          <span className="ml-1.5 text-[10px] text-zinc-500">
            (cash {d.raw_strike.toFixed(0)})
          </span>
        )}
      </div>
      <Row label="Net GEX" value={fmt.usd(d.net_gex, { signed: true })}
           color={d.net_gex >= 0 ? "text-emerald-300" : "text-rose-300"} />
      <Row label="Calls" value={fmt.usd(d.call_gex, { signed: true })} color="text-rose-300/80" />
      <Row label="Puts" value={fmt.usd(d.put_gex, { signed: true })} color="text-emerald-300/80" />
      <div className="mt-1.5 border-t border-zinc-800 pt-1.5 text-[10px] text-zinc-500">
        OI · {Math.round(d.call_oi)} call · {Math.round(d.put_oi)} put
      </div>
    </div>
  )
}

function Row({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="flex items-center justify-between gap-6 leading-tight">
      <span className="text-zinc-500">{label}</span>
      <span className={`font-semibold tabular-nums ${color}`}>{value}</span>
    </div>
  )
}

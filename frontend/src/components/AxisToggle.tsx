import { cn } from "@/lib/utils"

export type AxisMode = "spot" | "futures"

interface AxisToggleProps {
  value: AxisMode
  onChange: (next: AxisMode) => void
  className?: string
}

export function AxisToggle({ value, onChange, className }: AxisToggleProps) {
  return (
    <div
      role="radiogroup"
      aria-label="Strike axis projection"
      className={cn(
        "inline-flex items-center gap-1 rounded-full border border-zinc-800/70 bg-zinc-900/60 p-1 backdrop-blur-xl",
        className,
      )}
    >
      <Pill active={value === "spot"} onClick={() => onChange("spot")}>
        SPX Spot
      </Pill>
      <Pill active={value === "futures"} onClick={() => onChange("futures")}>
        ES Futures
      </Pill>
    </div>
  )
}

function Pill({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={active}
      onClick={onClick}
      className={cn(
        "rounded-full px-3.5 py-1.5 text-xs font-medium transition-all",
        active
          ? "bg-zinc-100 text-zinc-900 shadow-[0_0_15px_rgba(255,255,255,0.08)]"
          : "text-zinc-400 hover:text-zinc-100",
      )}
    >
      {children}
    </button>
  )
}

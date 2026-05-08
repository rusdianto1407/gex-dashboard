import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

const badgeVariants = cva(
  "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wider transition-colors",
  {
    variants: {
      variant: {
        default: "border-zinc-700/60 bg-zinc-800/60 text-zinc-200",
        live: "border-emerald-500/50 bg-emerald-500/10 text-emerald-300",
        mock: "border-amber-500/50 bg-amber-500/10 text-amber-300",
        positive: "border-emerald-500/50 bg-emerald-500/10 text-emerald-300",
        negative: "border-rose-500/50 bg-rose-500/10 text-rose-300",
        outline: "border-zinc-700 text-zinc-300",
      },
    },
    defaultVariants: { variant: "default" },
  },
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />
}

export { Badge, badgeVariants }

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import type { ExpiryInfo } from "@/lib/api"
import { fmt } from "@/lib/api"

interface ExpirySelectorProps {
  expiries: ExpiryInfo[]
  value: string | undefined
  onChange: (next: string) => void
  loading?: boolean
}

export function ExpirySelector({ expiries, value, onChange, loading }: ExpirySelectorProps) {
  const placeholder = loading ? "Loading expiries…" : "Select expiry"
  return (
    <Select value={value} onValueChange={onChange} disabled={loading || expiries.length === 0}>
      <SelectTrigger className="min-w-[220px]">
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        {expiries.map((e) => (
          <SelectItem key={e.expiry} value={e.expiry}>
            <span className="flex items-center gap-2">
              <span className="font-medium tabular-nums">{fmt.date(e.expiry)}</span>
              <span className="text-zinc-500">·</span>
              <span className="tabular-nums text-zinc-400">
                {e.dte === 0 ? "0DTE" : `${e.dte}d`}
              </span>
              <span className="text-zinc-600">·</span>
              <span className="text-zinc-500">{e.instrument_count} contracts</span>
            </span>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

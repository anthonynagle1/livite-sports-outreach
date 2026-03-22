/**
 * Small "Updated X min ago" indicator for cached data.
 * Shows green dot when fresh, amber when stale (refreshing in background).
 */

interface CacheIndicatorProps {
  age: number | null | undefined   // seconds
  stale?: boolean
}

export default function CacheIndicator({ age, stale }: CacheIndicatorProps) {
  if (age == null) return null

  const label = age < 60
    ? 'just now'
    : age < 3600
    ? `${Math.floor(age / 60)}m ago`
    : `${Math.floor(age / 3600)}h ago`

  return (
    <span className="inline-flex items-center gap-1.5 text-[10px] text-brand-muted tabular-nums">
      <span className={`w-1.5 h-1.5 rounded-full ${
        stale ? 'bg-amber-400 animate-pulse' : 'bg-emerald-400'
      }`} />
      {stale ? 'Updating…' : label}
    </span>
  )
}

import { useState } from 'react'
import type { PlayingLater } from '../api/types'

interface PlayingLaterBadgeProps {
  data: PlayingLater
}

export default function PlayingLaterBadge({ data }: PlayingLaterBadgeProps) {
  const [expanded, setExpanded] = useState(false)

  if (!data || data.total <= 1) {
    return null
  }

  const otherCount = data.others.length

  return (
    <div className="relative">
      <button
        onClick={() => setExpanded(!expanded)}
        className="inline-flex items-center gap-1 text-xs font-medium rounded-full
                   bg-emerald-50 text-emerald-700 px-2 py-0.5
                   hover:bg-emerald-100 transition-colors cursor-pointer"
      >
        <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
        {data.total} games in Boston
      </button>

      {expanded && otherCount > 0 && (
        <div className="absolute z-20 top-full left-0 mt-1 bg-white rounded-lg shadow-lg
                        border border-brand-dark/10 p-3 min-w-48">
          <p className="text-xs font-medium text-brand-muted mb-2">
            Also playing:
          </p>
          <ul className="space-y-1.5">
            {data.others.map(g => (
              <li key={g.game_id} className="text-xs text-brand-dark">
                <span className="font-medium">{g.game_date_display}</span>
                {g.home_school && (
                  <span className="text-brand-muted"> at {g.home_school}</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

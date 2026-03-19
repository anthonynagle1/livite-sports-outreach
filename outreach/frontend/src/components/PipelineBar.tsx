import type { PipelineStats } from '../api/types'

const STAGES = [
  { key: 'Not Contacted', color: 'bg-status-none', label: 'Not Contacted' },
  { key: 'Introduction Email - Sent', color: 'bg-status-sent', label: 'Intro Sent' },
  { key: 'Follow-Up Email - Sent', color: 'bg-amber-400', label: 'Follow-Up Sent' },
  { key: 'Responded', color: 'bg-status-responded', label: 'Responded' },
  { key: 'In Conversation', color: 'bg-purple-400', label: 'In Conversation' },
  { key: 'Interested', color: 'bg-yellow-400', label: 'Interested' },
  { key: 'Booked', color: 'bg-status-booked', label: 'Booked' },
]

interface PipelineBarProps {
  stats: PipelineStats | null
}

export default function PipelineBar({ stats }: PipelineBarProps) {
  if (!stats) {
    return (
      <div className="bg-white rounded-xl border border-brand-dark/5 p-4
                      shadow-[0_1px_3px_rgba(71,84,23,0.04)]">
        <h3 className="text-sm font-semibold text-brand-dark mb-3">Pipeline</h3>
        <div className="animate-pulse space-y-2">
          <div className="h-3 bg-gray-100 rounded-full" />
          <div className="flex gap-4">
            <div className="h-3 w-20 bg-gray-100 rounded" />
            <div className="h-3 w-16 bg-gray-100 rounded" />
            <div className="h-3 w-18 bg-gray-100 rounded" />
          </div>
        </div>
      </div>
    )
  }

  const total = stats.games_total || 1

  return (
    <div className="bg-white rounded-xl border border-brand-dark/5 p-4
                    shadow-[0_1px_3px_rgba(71,84,23,0.04)]">
      <h3 className="text-sm font-semibold text-brand-dark mb-3">Pipeline</h3>

      {/* Horizontal stacked bar */}
      <div className="flex rounded-full overflow-hidden h-3 bg-gray-100 mb-3">
        {STAGES.map(stage => {
          const count = stats.games[stage.key] || 0
          const pct = (count / total) * 100
          if (pct === 0) return null
          return (
            <div
              key={stage.key}
              className={`${stage.color} transition-all duration-500`}
              style={{ width: `${pct}%` }}
              title={`${stage.label}: ${count}`}
            />
          )
        })}
      </div>

      {/* Legend */}
      <div className="flex flex-wrap gap-x-4 gap-y-1">
        {STAGES.map(stage => {
          const count = stats.games[stage.key] || 0
          return (
            <div key={stage.key} className="flex items-center gap-1.5">
              <span className={`w-2 h-2 rounded-full ${stage.color}`} />
              <span className="text-xs text-brand-muted">
                {stage.label}
              </span>
              <span className="text-xs font-semibold text-brand-dark tabular-nums">
                {count}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

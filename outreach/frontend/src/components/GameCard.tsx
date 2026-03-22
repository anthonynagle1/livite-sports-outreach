import { useNavigate } from 'react-router-dom'
import type { Game } from '../api/types'
import StatusBadge from './StatusBadge'
import PlayingLaterBadge from './PlayingLaterBadge'

interface GameCardProps {
  game: Game
  compact?: boolean
  selectable?: boolean
  selected?: boolean
  onSelect?: (gameId: string) => void
}

export default function GameCard({ game, compact = false, selectable, selected, onSelect }: GameCardProps) {
  const navigate = useNavigate()

  return (
    <div
      onClick={() => navigate(`/schedule/${game.id}`)}
      className={`bg-white rounded-xl border p-4
                    hover:border-brand-sage/20 transition-colors cursor-pointer
                    shadow-[0_1px_3px_rgba(71,84,23,0.04)]
                    ${selected ? 'border-brand-sage/40 ring-1 ring-brand-sage/20' : 'border-brand-dark/5'}`}>
      <div className="flex items-start justify-between gap-3">
        {selectable && (
          <button
            onClick={(e) => { e.stopPropagation(); onSelect?.(game.id) }}
            className={`mt-0.5 w-5 h-5 rounded border-2 shrink-0 flex items-center justify-center
                        transition-colors ${
              selected
                ? 'bg-brand-sage border-brand-sage text-white'
                : 'border-brand-dark/20 hover:border-brand-sage/40'
            }`}>
            {selected && (
              <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
              </svg>
            )}
          </button>
        )}
        <div className="flex-1 min-w-0">
          {/* Date + Sport */}
          <div className="flex items-center gap-2 mb-1">
            <span className="text-sm font-semibold text-brand-dark">
              {game.game_date_display || 'TBD'}
            </span>
            <span className="text-xs text-brand-muted">
              {game.gender} {game.sport}
            </span>
          </div>

          {/* Visiting team */}
          <p className="text-sm text-brand-dark truncate">
            {game.visiting_team || 'Unknown opponent'}
          </p>

          {/* Home school + venue */}
          {!compact && (
            <p className="text-xs text-brand-muted mt-0.5">
              at {game.home_school || 'TBD'}
              {game.venue && ` · ${game.venue}`}
            </p>
          )}

          {/* Contact name */}
          {game.contact?.name && (
            <p className="text-xs text-brand-sage mt-0.5">{game.contact.name}</p>
          )}

          {/* Recommendation */}
          {game.recommendation && (
            <p className="text-xs text-brand-sage font-medium mt-1.5">
              {game.recommendation}
            </p>
          )}
        </div>

        {/* Right side: status + response tag + lead score */}
        <div className="flex flex-col items-end gap-1.5 shrink-0">
          <StatusBadge status={game.outreach_status || 'Not Contacted'} />
          {game.contact?.last_response_type && (
            <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-semibold border leading-none ${
              game.contact.last_response_type === 'Interested'
                ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                : game.contact.last_response_type === 'Booked'
                ? 'bg-status-booked/15 text-status-booked border-status-booked/30'
                : game.contact.last_response_type === 'Not Interested'
                ? 'bg-status-declined/15 text-status-declined border-status-declined/30'
                : game.contact.last_response_type === 'Question'
                ? 'bg-amber-50 text-amber-700 border-amber-200'
                : game.contact.last_response_type === 'Out of Office'
                ? 'bg-gray-100 text-gray-500 border-gray-200'
                : 'bg-gray-100 text-gray-500 border-gray-200'
            }`}>
              {game.contact.last_response_type}
            </span>
          )}
          {game.lead_score != null && (
            <span className={`text-xs font-semibold tabular-nums ${
              game.lead_score >= 80 ? 'text-status-booked' :
              game.lead_score >= 50 ? 'text-brand-sage' :
              'text-brand-muted'
            }`}>
              {game.lead_score}
            </span>
          )}
        </div>
      </div>

      {/* Playing later badge */}
      {game._playing_later && game._playing_later.total > 1 && (
        <div className="mt-2.5 pt-2.5 border-t border-brand-dark/5">
          <PlayingLaterBadge data={game._playing_later} />
        </div>
      )}
    </div>
  )
}

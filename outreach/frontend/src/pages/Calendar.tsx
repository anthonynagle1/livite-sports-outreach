import { useState, useEffect, useCallback, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { Game } from '../api/types'
import StatusBadge from '../components/StatusBadge'

/* ── Status → dot color mapping ── */
const STATUS_DOT: Record<string, string> = {
  'Not Contacted': 'bg-status-none',
  'Introduction Email - Sent': 'bg-status-sent',
  'Follow-Up Email - Sent': 'bg-amber-400',
  'Responded': 'bg-status-responded',
  'In Conversation': 'bg-purple-400',
  'Interested': 'bg-yellow-400',
  'Booked': 'bg-status-booked',
  'Not Interested': 'bg-status-declined',
  'No Response': 'bg-gray-400',
  'Out of Office': 'bg-gray-300',
  'Missed': 'bg-gray-300',
}

function dotColor(status: string) {
  return STATUS_DOT[status] || 'bg-gray-300'
}

/* ── Date helpers ── */
function parseDate(s: string): Date | null {
  if (!s) return null
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})/)
  if (m) return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]))
  const d = new Date(s)
  return isNaN(d.getTime()) ? null : d
}

function sameDay(a: Date, b: Date) {
  return a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
}

function isToday(d: Date) {
  return sameDay(d, new Date())
}

function daysInMonth(year: number, month: number) {
  return new Date(year, month + 1, 0).getDate()
}

const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]
const DAY_LABELS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

export default function Calendar() {
  const navigate = useNavigate()
  const now = new Date()
  const [year, setYear] = useState(now.getFullYear())
  const [month, setMonth] = useState(now.getMonth())
  const [games, setGames] = useState<Game[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedDay, setSelectedDay] = useState<Date | null>(null)

  /* Fetch games for current month window (± 1 week for edge days) */
  const fetchGames = useCallback(async () => {
    setLoading(true)
    try {
      const from = new Date(year, month, 1)
      const to = new Date(year, month + 1, 0)
      // Pad range by a week each side for calendar display overlap
      from.setDate(from.getDate() - 7)
      to.setDate(to.getDate() + 7)
      const dateFrom = from.toISOString().split('T')[0]
      const dateTo = to.toISOString().split('T')[0]
      const data = await api.get(`/api/games?date_from=${dateFrom}&date_to=${dateTo}`)
      setGames(data.games)
    } catch (err) {
      console.error('Failed to fetch calendar games:', err)
    } finally {
      setLoading(false)
    }
  }, [year, month])

  useEffect(() => { fetchGames() }, [fetchGames])

  /* Build calendar grid — map date → games */
  const gamesByDate = useMemo(() => {
    const map = new Map<string, Game[]>()
    for (const g of games) {
      const d = parseDate(g.game_date)
      if (!d) continue
      const key = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`
      const list = map.get(key) || []
      list.push(g)
      map.set(key, list)
    }
    return map
  }, [games])

  function getGamesForDay(d: Date): Game[] {
    return gamesByDate.get(`${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`) || []
  }

  /* Month navigation */
  function prevMonth() {
    setSelectedDay(null)
    if (month === 0) { setMonth(11); setYear(y => y - 1) }
    else setMonth(m => m - 1)
  }
  function nextMonth() {
    setSelectedDay(null)
    if (month === 11) { setMonth(0); setYear(y => y + 1) }
    else setMonth(m => m + 1)
  }
  function goToday() {
    setSelectedDay(null)
    setYear(now.getFullYear())
    setMonth(now.getMonth())
  }

  /* Build grid cells */
  const firstDow = new Date(year, month, 1).getDay()
  const totalDays = daysInMonth(year, month)
  const cells: (Date | null)[] = []
  for (let i = 0; i < firstDow; i++) cells.push(null)
  for (let d = 1; d <= totalDays; d++) cells.push(new Date(year, month, d))
  // Pad end to full weeks
  while (cells.length % 7 !== 0) cells.push(null)

  /* Selected day's games */
  const selectedGames = selectedDay ? getGamesForDay(selectedDay) : []

  /* Priority groups for the detail panel */
  const needsAction = selectedGames.filter(g =>
    ['Responded', 'In Conversation', 'Interested'].includes(g.outreach_status)
  )
  const otherGames = selectedGames.filter(g =>
    !['Responded', 'In Conversation', 'Interested'].includes(g.outreach_status)
  )

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="font-display text-2xl font-bold text-brand-dark">Calendar</h2>
        <button
          onClick={goToday}
          className="text-xs font-medium text-brand-sage border border-brand-sage/30 px-3 py-1.5 rounded-lg
                     hover:bg-brand-sage/5 active:bg-brand-sage/10 transition-colors"
        >
          Today
        </button>
      </div>

      {/* Month nav */}
      <div className="flex items-center justify-between">
        <button onClick={prevMonth}
          className="p-2 rounded-lg hover:bg-brand-cream/50 text-brand-muted hover:text-brand-dark transition-colors">
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
          </svg>
        </button>
        <h3 className="font-display text-lg font-semibold text-brand-dark">
          {MONTH_NAMES[month]} {year}
        </h3>
        <button onClick={nextMonth}
          className="p-2 rounded-lg hover:bg-brand-cream/50 text-brand-muted hover:text-brand-dark transition-colors">
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
          </svg>
        </button>
      </div>

      {/* Calendar grid */}
      <div className="bg-white rounded-xl border border-brand-dark/5 shadow-[0_1px_3px_rgba(71,84,23,0.04)] overflow-hidden">
        {/* Day headers */}
        <div className="grid grid-cols-7 border-b border-brand-dark/5">
          {DAY_LABELS.map(d => (
            <div key={d} className="py-2 text-center text-xs font-semibold text-brand-muted uppercase tracking-wider">
              {d}
            </div>
          ))}
        </div>

        {/* Day cells */}
        {loading ? (
          <div className="h-64 flex items-center justify-center">
            <div className="animate-pulse font-display text-lg text-brand-sage/50">Loading...</div>
          </div>
        ) : (
          <div className="grid grid-cols-7">
            {cells.map((date, i) => {
              if (!date) {
                return <div key={`empty-${i}`} className="min-h-[4.5rem] md:min-h-[5.5rem] bg-brand-cream/20 border-b border-r border-brand-dark/5" />
              }

              const dayGames = getGamesForDay(date)
              const hasGames = dayGames.length > 0
              const today = isToday(date)
              const isSelected = selectedDay ? sameDay(date, selectedDay) : false
              const isPast = date < new Date(now.getFullYear(), now.getMonth(), now.getDate())
              const hasActionNeeded = dayGames.some(g =>
                ['Responded', 'In Conversation', 'Interested'].includes(g.outreach_status)
              )

              return (
                <button
                  key={date.toISOString()}
                  onClick={() => setSelectedDay(isSelected ? null : date)}
                  className={`min-h-[4.5rem] md:min-h-[5.5rem] p-1.5 md:p-2 text-left border-b border-r border-brand-dark/5
                              transition-colors relative group
                              ${isSelected ? 'bg-brand-sage/5 ring-1 ring-inset ring-brand-sage/20' : ''}
                              ${!isSelected && hasGames ? 'hover:bg-brand-cream/40' : 'hover:bg-brand-cream/20'}
                              ${isPast && !hasGames ? 'opacity-40' : ''}`}
                >
                  {/* Day number */}
                  <span className={`text-xs font-medium tabular-nums inline-flex items-center justify-center
                                    w-6 h-6 rounded-full
                                    ${today ? 'bg-brand-sage text-white' : 'text-brand-dark'}`}>
                    {date.getDate()}
                  </span>

                  {/* Action needed indicator */}
                  {hasActionNeeded && (
                    <span className="absolute top-1.5 right-1.5 w-2 h-2 rounded-full bg-status-responded animate-pulse" />
                  )}

                  {/* Game dots */}
                  {hasGames && (
                    <div className="mt-1 flex flex-wrap gap-0.5">
                      {dayGames.slice(0, 6).map(g => (
                        <span
                          key={g.id}
                          className={`w-2 h-2 rounded-full ${dotColor(g.outreach_status)} shrink-0`}
                          title={`${g.visiting_team} — ${g.outreach_status}`}
                        />
                      ))}
                      {dayGames.length > 6 && (
                        <span className="text-[10px] text-brand-muted leading-none">+{dayGames.length - 6}</span>
                      )}
                    </div>
                  )}

                  {/* Game count on desktop */}
                  {hasGames && (
                    <p className="hidden md:block text-[10px] text-brand-muted mt-0.5 truncate">
                      {dayGames.length === 1
                        ? (dayGames[0]?.visiting_team ?? '')
                        : `${dayGames.length} games`}
                    </p>
                  )}
                </button>
              )
            })}
          </div>
        )}
      </div>

      {/* Legend */}
      <div className="flex flex-wrap gap-x-4 gap-y-1">
        {Object.entries(STATUS_DOT).slice(0, 7).map(([status, color]) => (
          <div key={status} className="flex items-center gap-1.5">
            <span className={`w-2 h-2 rounded-full ${color}`} />
            <span className="text-xs text-brand-muted">{status.replace('Email - ', '')}</span>
          </div>
        ))}
      </div>

      {/* Selected day detail panel */}
      {selectedDay && (
        <div className="space-y-4">
          <h3 className="font-display text-lg font-semibold text-brand-dark">
            {selectedDay.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' })}
          </h3>

          {selectedGames.length === 0 ? (
            <div className="bg-white rounded-xl border border-brand-dark/5 p-6
                            shadow-[0_1px_3px_rgba(71,84,23,0.04)] text-center">
              <p className="text-sm text-brand-muted">No games on this day.</p>
            </div>
          ) : (
            <>
              {/* Action needed — show first */}
              {needsAction.length > 0 && (
                <div>
                  <p className="text-xs font-semibold text-status-responded uppercase tracking-wider mb-2">
                    Needs Action ({needsAction.length})
                  </p>
                  <div className="space-y-2">
                    {needsAction.map(g => (
                      <CalendarGameCard key={g.id} game={g} onNavigate={() => navigate(`/schedule/${g.id}`)} />
                    ))}
                  </div>
                </div>
              )}

              {/* Other games */}
              {otherGames.length > 0 && (
                <div>
                  {needsAction.length > 0 && (
                    <p className="text-xs font-semibold text-brand-muted uppercase tracking-wider mb-2">
                      Other Games ({otherGames.length})
                    </p>
                  )}
                  <div className="space-y-2">
                    {otherGames.map(g => (
                      <CalendarGameCard key={g.id} game={g} onNavigate={() => navigate(`/schedule/${g.id}`)} />
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}


/* ── Compact game card for calendar detail ── */
function CalendarGameCard({ game, onNavigate }: { game: Game; onNavigate: () => void }) {
  return (
    <button
      onClick={onNavigate}
      className="w-full text-left bg-white rounded-xl border border-brand-dark/5 p-3.5
                 hover:border-brand-sage/20 transition-colors cursor-pointer
                 shadow-[0_1px_3px_rgba(71,84,23,0.04)]"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-brand-dark truncate">
            {game.visiting_team || 'Unknown opponent'}
          </p>
          <p className="text-xs text-brand-muted mt-0.5">
            {game.gender} {game.sport}
            {game.home_school && ` · at ${game.home_school}`}
          </p>
          {game.contact?.name && (
            <p className="text-xs text-brand-sage mt-1">
              {game.contact.name}
              {game.contact.last_response_type && (
                <span className="text-brand-muted"> · {game.contact.last_response_type}</span>
              )}
            </p>
          )}
        </div>
        <div className="shrink-0 flex flex-col items-end gap-1">
          <StatusBadge status={game.outreach_status || 'Not Contacted'} />
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
    </button>
  )
}

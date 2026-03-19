import { useState, useEffect, useCallback } from 'react'
import { api } from '../api/client'
import type { Game } from '../api/types'
import GameCard from '../components/GameCard'
import FilterBar from '../components/FilterBar'

function todayStr(): string {
  const d = new Date()
  return d.toISOString().split('T')[0] ?? ''
}

export default function Schedule() {
  const [games, setGames] = useState<Game[]>([])
  const [loading, setLoading] = useState(true)
  const [sport, setSport] = useState('')
  const [status, setStatus] = useState('')
  const [gender, setGender] = useState('')
  const [showPast, setShowPast] = useState(false)

  // Multi-select — always available, no "mode" needed
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [batchDrafting, setBatchDrafting] = useState(false)
  const [batchResult, setBatchResult] = useState<{ ok: number; errors: string[]; warnings: string[] } | null>(null)

  const fetchGames = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (sport) params.set('sport', sport)
      if (status) params.set('status', status)
      if (gender) params.set('gender', gender)
      if (!showPast) params.set('date_from', todayStr())

      const data = await api.get(`/api/games?${params.toString()}`)
      setGames(data.games)
    } catch (err) {
      console.error('Failed to fetch games:', err)
    } finally {
      setLoading(false)
    }
  }, [sport, status, gender, showPast])

  useEffect(() => { fetchGames() }, [fetchGames])

  function toggleSelect(gameId: string) {
    setBatchResult(null)
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(gameId)) next.delete(gameId)
      else next.add(gameId)
      return next
    })
  }

  async function handleBatchDraft() {
    if (selected.size === 0 || batchDrafting) return
    setBatchDrafting(true)
    setBatchResult(null)

    let ok = 0
    const errors: string[] = []
    const warnings: string[] = []

    for (const gameId of selected) {
      try {
        const result = await api.post(`/api/games/${gameId}/draft`)
        ok++
        if (result.warnings?.length) {
          const game = games.find(g => g.id === gameId)
          const label = game?.visiting_team || gameId.slice(0, 8)
          warnings.push(`${label}: ${result.warnings[0]}`)
        }
      } catch (err: unknown) {
        const game = games.find(g => g.id === gameId)
        const label = game?.visiting_team || gameId.slice(0, 8)
        const msg = err instanceof Error ? err.message : 'Unknown error'
        errors.push(`${label}: ${msg}`)
      }
    }

    setBatchDrafting(false)
    setBatchResult({ ok, errors, warnings })
    setSelected(new Set())
  }

  // Group games by month
  const grouped = groupByMonth(games)

  return (
    <div className="space-y-6 pb-20">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <h2 className="font-display text-2xl font-bold text-brand-dark">Schedule</h2>
        <FilterBar
          sport={sport} onSportChange={setSport}
          status={status} onStatusChange={setStatus}
          gender={gender} onGenderChange={setGender}
        />
      </div>

      {/* Summary + controls */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-4 text-sm text-brand-muted">
          <span>{games.length} games</span>
          {games.filter(g => (g._playing_later?.total ?? 0) > 1).length > 0 && (
            <span className="text-emerald-600 font-medium">
              {games.filter(g => (g._playing_later?.total ?? 0) > 1).length} with repeat visits
            </span>
          )}
        </div>
        <button
          onClick={() => setShowPast(!showPast)}
          className={`text-xs px-3 py-1.5 rounded-full border transition-colors ${
            showPast
              ? 'bg-brand-sage/10 border-brand-sage/30 text-brand-sage font-medium'
              : 'border-brand-dark/10 text-brand-muted hover:border-brand-dark/20'
          }`}>
          {showPast ? 'Showing all' : 'Show past'}
        </button>
      </div>

      {/* Batch result banner */}
      {batchResult && (
        <div className="space-y-2">
          <div className={`rounded-xl p-3 text-sm ${
            batchResult.errors.length > 0
              ? 'bg-amber-50 border border-amber-200 text-amber-800'
              : 'bg-emerald-50 border border-emerald-200 text-emerald-800'
          }`}>
            <p className="font-medium">
              {batchResult.ok} draft{batchResult.ok !== 1 ? 's' : ''} created
              {batchResult.errors.length > 0 && `, ${batchResult.errors.length} skipped`}
            </p>
            {batchResult.errors.length > 0 && (
              <ul className="mt-1 text-xs space-y-0.5">
                {batchResult.errors.map((e, i) => <li key={i}>{e}</li>)}
              </ul>
            )}
          </div>
          {batchResult.warnings.length > 0 && (
            <div className="rounded-xl p-3 text-sm bg-amber-50 border border-amber-200 text-amber-800">
              <p className="font-medium text-xs uppercase tracking-wider mb-1">Previously contacted</p>
              <ul className="text-xs space-y-0.5">
                {batchResult.warnings.map((w, i) => <li key={i}>{w}</li>)}
              </ul>
            </div>
          )}
        </div>
      )}

      {/* Game list grouped by month */}
      {loading ? (
        <div className="space-y-3">
          {[1, 2, 3, 4, 5].map(i => (
            <div key={i} className="animate-pulse h-20 bg-white rounded-xl" />
          ))}
        </div>
      ) : games.length === 0 ? (
        <div className="text-center py-12">
          <p className="text-brand-muted">No games found matching your filters.</p>
        </div>
      ) : (
        <div className="space-y-6">
          {grouped.map(([month, monthGames]) => (
            <section key={month}>
              <h3 className="text-xs font-semibold text-brand-muted uppercase tracking-wider mb-3">
                {month}
              </h3>
              <div className="space-y-2.5">
                {monthGames.map(game => (
                  <GameCard
                    key={game.id}
                    game={game}
                    selectable
                    selected={selected.has(game.id)}
                    onSelect={toggleSelect}
                  />
                ))}
              </div>
            </section>
          ))}
        </div>
      )}

      {/* Floating action bar when games are selected */}
      {selected.size > 0 && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50">
          <div className="bg-brand-dark text-white rounded-2xl px-5 py-3
                          shadow-[0_8px_30px_rgba(0,0,0,0.25)] flex items-center gap-4">
            <span className="text-sm font-medium">
              {selected.size} game{selected.size !== 1 ? 's' : ''}
            </span>
            <button
              onClick={handleBatchDraft}
              disabled={batchDrafting}
              className="inline-flex items-center gap-2 px-4 py-1.5 rounded-lg text-sm font-medium
                         bg-brand-sage text-white hover:bg-brand-sage/90
                         disabled:opacity-50 disabled:cursor-not-allowed
                         active:scale-[0.98] transition-transform">
              {batchDrafting ? (
                <>
                  <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                  Drafting...
                </>
              ) : (
                'Draft Emails'
              )}
            </button>
            <button
              onClick={() => setSelected(new Set())}
              className="text-white/60 hover:text-white transition-colors">
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>
      )}
    </div>
  )
}


function groupByMonth(games: Game[]): [string, Game[]][] {
  const groups = new Map<string, Game[]>()

  for (const game of games) {
    let monthKey = 'Unknown'
    if (game.game_date) {
      try {
        const dt = new Date(game.game_date)
        monthKey = dt.toLocaleDateString('en-US', { month: 'long', year: 'numeric' })
      } catch {
        // keep 'Unknown'
      }
    }
    const list = groups.get(monthKey) || []
    list.push(game)
    groups.set(monthKey, list)
  }

  return Array.from(groups.entries())
}

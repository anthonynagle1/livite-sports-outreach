import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { PipelineStats, Game, ActivityItem } from '../api/types'
import PipelineBar from '../components/PipelineBar'
import GameCard from '../components/GameCard'
import CacheIndicator from '../components/CacheIndicator'

interface CacheMeta { _cache_age?: number | null; _cache_stale?: boolean }

export default function Dashboard() {
  const [pipeline, setPipeline] = useState<PipelineStats | null>(null)
  const [upcoming, setUpcoming] = useState<Game[]>([])
  const [activity, setActivity] = useState<ActivityItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [cacheMeta, setCacheMeta] = useState<CacheMeta>({})

  const fetchAll = async () => {
    setError(null)
    try {
      const [pipeRes, gamesRes, actRes] = await Promise.allSettled([
        api.get('/api/pipeline'),
        api.get('/api/games?date_from=' + todayISO()),
        api.get('/api/pipeline/activity'),
      ])

      if (pipeRes.status === 'fulfilled') {
        setPipeline(pipeRes.value)
        setCacheMeta({ _cache_age: pipeRes.value._cache_age, _cache_stale: pipeRes.value._cache_stale })
      } else console.error('Pipeline fetch error:', pipeRes.reason)

      if (gamesRes.status === 'fulfilled') setUpcoming(gamesRes.value.games.slice(0, 8))
      else console.error('Games fetch error:', gamesRes.reason)

      if (actRes.status === 'fulfilled') setActivity(actRes.value.activity || [])
      else console.error('Activity fetch error:', actRes.reason)

      // If all three failed, show an error message
      if (pipeRes.status === 'rejected' && gamesRes.status === 'rejected' && actRes.status === 'rejected') {
        setError('Unable to load data. Make sure the backend is running.')
      }
    } catch (err) {
      console.error('Dashboard fetch error:', err)
      setError('Unable to connect to the server.')
    }
    setLoading(false)
  }

  useEffect(() => { fetchAll() }, [])

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h2 className="font-display text-2xl font-bold text-brand-dark">Dashboard</h2>
          {!loading && <CacheIndicator age={cacheMeta._cache_age} stale={cacheMeta._cache_stale} />}
        </div>
        <button
          onClick={() => { setLoading(true); fetchAll() }}
          className="text-xs text-brand-muted hover:text-brand-sage transition-colors"
        >
          Refresh
        </button>
      </div>

      {/* Error banner */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-xl px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {/* Pipeline */}
      <PipelineBar stats={pipeline} />

      {/* Quick stats */}
      {pipeline && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <StatCard label="Drafts Pending" value={pipeline.emails['Draft'] || 0} href="/emails" />
          <StatCard label="Awaiting Send" value={pipeline.emails['Approved'] || 0} href="/emails" />
          <StatCard label="Total Games" value={pipeline.games_total} href="/schedule" />
          <StatCard label="Booked" value={pipeline.games['Booked'] || 0} accent />
        </div>
      )}

      {/* Upcoming games */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-brand-dark">Upcoming Games</h3>
          <Link to="/schedule" className="text-xs text-brand-sage hover:underline">
            View all
          </Link>
        </div>
        {loading ? (
          <div className="space-y-3">
            {[1, 2, 3].map(i => (
              <div key={i} className="bg-white rounded-xl border border-brand-dark/5 p-4
                                     shadow-[0_1px_3px_rgba(71,84,23,0.04)]">
                <div className="animate-pulse space-y-2">
                  <div className="flex items-center gap-2">
                    <div className="h-3 w-20 bg-gray-100 rounded" />
                    <div className="h-3 w-16 bg-gray-100 rounded" />
                  </div>
                  <div className="h-3 w-32 bg-gray-100 rounded" />
                </div>
              </div>
            ))}
          </div>
        ) : upcoming.length === 0 ? (
          <p className="text-sm text-brand-muted">No upcoming games found.</p>
        ) : (
          <div className="space-y-3">
            {upcoming.map(game => (
              <GameCard key={game.id} game={game} />
            ))}
          </div>
        )}
      </section>

      {/* Recent activity */}
      {activity.length > 0 && (
        <section>
          <h3 className="text-sm font-semibold text-brand-dark mb-3">Recent Activity</h3>
          <div className="bg-white rounded-xl border border-brand-dark/5 divide-y divide-brand-dark/5
                          shadow-[0_1px_3px_rgba(71,84,23,0.04)]">
            {activity.slice(0, 5).map(item => (
              <div key={item.id} className="px-4 py-3 flex items-center justify-between">
                <div className="min-w-0 flex-1">
                  <p className="text-sm text-brand-dark truncate">{item.subject}</p>
                  <p className="text-xs text-brand-muted">
                    {item.school} · {item.sport}
                  </p>
                </div>
                <div className="text-xs text-brand-muted shrink-0 ml-3">
                  {item.status}
                </div>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}


function StatCard({ label, value, href, accent }: {
  label: string
  value: number
  href?: string
  accent?: boolean
}) {
  const inner = (
    <div className={`bg-white rounded-xl border border-brand-dark/5 p-3
                     shadow-[0_1px_3px_rgba(71,84,23,0.04)]
                     ${href ? 'hover:border-brand-sage/20 transition-colors cursor-pointer' : ''}`}>
      <p className="text-xs text-brand-muted">{label}</p>
      <p className={`text-2xl font-bold tabular-nums mt-0.5 ${
        accent ? 'text-status-booked' : 'text-brand-dark'
      }`}>
        {value}
      </p>
    </div>
  )

  if (href) {
    return <Link to={href}>{inner}</Link>
  }
  return inner
}


function todayISO() {
  return new Date().toISOString().split('T')[0]!
}

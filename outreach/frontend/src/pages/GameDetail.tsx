import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { Game, EmailEntry } from '../api/types'
import StatusBadge from '../components/StatusBadge'

export default function GameDetail() {
  const { gameId } = useParams<{ gameId: string }>()
  const navigate = useNavigate()
  const [game, setGame] = useState<Game | null>(null)
  const [emails, setEmails] = useState<EmailEntry[]>([])
  const [contactEmails, setContactEmails] = useState<EmailEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [expandedThread, setExpandedThread] = useState<string | null>(null)
  const [showOtherOutreach, setShowOtherOutreach] = useState(false)
  const [drafting, setDrafting] = useState(false)
  const [draftError, setDraftError] = useState('')
  const [draftWarnings, setDraftWarnings] = useState<string[]>([])
  const [creatingOrder, setCreatingOrder] = useState(false)
  const [orderResult, setOrderResult] = useState<{ ok: boolean; order_id?: string; error?: string } | null>(null)

  useEffect(() => {
    if (!gameId) return
    setLoading(true)
    Promise.all([
      api.get(`/api/games/${gameId}`),
      api.get(`/api/games/${gameId}/emails`),
    ])
      .then(([gameData, emailData]) => {
        setGame(gameData)
        setEmails(emailData.emails)
        // Load full contact history if contact exists
        if (gameData.contact?.id) {
          api.get(`/api/contacts/${gameData.contact.id}/emails`)
            .then(data => {
              // Filter out emails already shown for this game
              const gameEmailIds = new Set(emailData.emails.map((e: EmailEntry) => e.id))
              setContactEmails(data.emails.filter((e: EmailEntry) => !gameEmailIds.has(e.id)))
            })
            .catch(() => {})
        }
      })
      .catch(err => console.error('Failed to load game detail:', err))
      .finally(() => setLoading(false))
  }, [gameId])

  async function handleDraftEmail() {
    if (!gameId || drafting) return
    setDrafting(true)
    setDraftError('')
    setDraftWarnings([])
    try {
      const result = await api.post(`/api/games/${gameId}/draft`)
      if (result.email) {
        setEmails(prev => [result.email, ...prev])
        const threadKey = result.email.gmail_thread_id || `solo-${result.email.id}`
        setExpandedThread(threadKey)
      }
      if (result.warnings?.length) {
        setDraftWarnings(result.warnings)
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to create draft'
      setDraftError(msg)
    } finally {
      setDrafting(false)
    }
  }

  async function handleCreateOrder() {
    if (!gameId || creatingOrder) return
    setCreatingOrder(true)
    setOrderResult(null)
    try {
      const result = await api.post(`/api/games/${gameId}/order`)
      setOrderResult({ ok: true, order_id: result.order_id })
      // Update game status locally
      if (game) setGame({ ...game, outreach_status: 'Booked' })
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to create order'
      setOrderResult({ ok: false, error: msg })
    } finally {
      setCreatingOrder(false)
    }
  }

  if (loading) {
    return (
      <div className="space-y-4">
        <div className="animate-pulse h-8 w-48 bg-white rounded-lg" />
        <div className="animate-pulse h-40 bg-white rounded-xl" />
        <div className="animate-pulse h-60 bg-white rounded-xl" />
      </div>
    )
  }

  if (!game) {
    return (
      <div className="text-center py-12">
        <p className="text-brand-muted">Game not found.</p>
        <button onClick={() => navigate('/schedule')}
          className="mt-3 text-sm text-brand-sage hover:underline">
          Back to schedule
        </button>
      </div>
    )
  }

  const contact = game.contact

  return (
    <div className="space-y-6 max-w-3xl">
      {/* Back button */}
      <button onClick={() => navigate('/schedule')}
        className="inline-flex items-center gap-1.5 text-sm text-brand-muted
                   hover:text-brand-dark transition-colors">
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
        </svg>
        Schedule
      </button>

      {/* Game header card */}
      <div className="bg-white rounded-xl border border-brand-dark/5 p-5
                      shadow-[0_1px_3px_rgba(71,84,23,0.04)]">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <h2 className="font-display text-xl font-bold text-brand-dark">
                {game.visiting_team || 'Unknown opponent'}
              </h2>
              <StatusBadge status={game.outreach_status || 'Not Contacted'} size="md" />
            </div>
            <p className="text-sm text-brand-muted">
              {game.game_date_display} &middot; {game.gender} {game.sport}
            </p>
            <p className="text-sm text-brand-muted mt-0.5">
              at {game.home_school || 'TBD'}
              {game.venue && ` \u00b7 ${game.venue}`}
            </p>
          </div>
          {game.lead_score != null && (
            <div className="text-right">
              <span className="text-xs text-brand-muted">Lead Score</span>
              <p className={`text-2xl font-bold tabular-nums ${
                game.lead_score >= 80 ? 'text-status-booked' :
                game.lead_score >= 50 ? 'text-brand-sage' :
                'text-brand-muted'
              }`}>
                {game.lead_score}
              </p>
            </div>
          )}
        </div>

        {/* Contact info with response tracking */}
        {contact && (
          <div className="mt-4 pt-4 border-t border-brand-dark/5">
            <span className="text-xs font-semibold text-brand-muted uppercase tracking-wider">Contact</span>
            <div className="mt-1.5 flex items-start justify-between gap-3">
              <div>
                <p className="text-sm font-medium text-brand-dark">{contact.name}</p>
                {contact.title && (
                  <p className="text-xs text-brand-muted">{contact.title}</p>
                )}
                {contact.email && (
                  <p className="text-xs text-brand-sage mt-0.5">{contact.email}</p>
                )}
              </div>
              <div className="flex flex-col items-end gap-1">
                {contact.relationship && (
                  <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                    contact.relationship === 'Previous Customer'
                      ? 'bg-status-booked/15 text-status-booked'
                      : contact.relationship === 'Previously Responded'
                      ? 'bg-status-responded/15 text-status-responded'
                      : contact.relationship === 'Previously Contacted'
                      ? 'bg-status-sent/15 text-status-sent'
                      : 'bg-gray-100 text-gray-500'
                  }`}>
                    {contact.relationship}
                  </span>
                )}
                {contact.do_not_contact && (
                  <span className="text-xs px-2 py-0.5 rounded-full font-medium bg-red-100 text-red-700">
                    Do Not Contact
                  </span>
                )}
              </div>
            </div>

            {/* Previous response alert */}
            {contact.last_response_type && (
              <div className={`mt-2.5 p-2.5 rounded-lg text-xs ${
                contact.last_response_type === 'Not Interested'
                  ? 'bg-red-50 border border-red-100'
                  : contact.last_response_type === 'Booked'
                  ? 'bg-emerald-50 border border-emerald-100'
                  : contact.last_response_type === 'Interested'
                  ? 'bg-amber-50 border border-amber-100'
                  : 'bg-gray-50 border border-gray-100'
              }`}>
                <span className="font-semibold">
                  Previous response: {contact.last_response_type}
                </span>
                {contact.response_notes && (
                  <p className="mt-1 text-brand-dark/70">{contact.response_notes}</p>
                )}
              </div>
            )}

            {/* Contact timeline */}
            {(contact.first_emailed || contact.last_emailed) && (
              <div className="mt-2 flex gap-4 text-xs text-brand-muted">
                {contact.first_emailed && (
                  <span>First emailed: {formatDate(contact.first_emailed)}</span>
                )}
                {contact.last_emailed && (
                  <span>Last emailed: {formatDate(contact.last_emailed)}</span>
                )}
              </div>
            )}
          </div>
        )}

        {/* Notes */}
        {game.notes && (
          <div className="mt-4 pt-4 border-t border-brand-dark/5">
            <span className="text-xs font-semibold text-brand-muted uppercase tracking-wider">Notes</span>
            <p className="mt-1 text-sm text-brand-dark whitespace-pre-wrap">{game.notes}</p>
          </div>
        )}

        {/* Upcoming games for this team */}
        {game._playing_later?.others && game._playing_later.others.length > 0 && (
          <div className="mt-4 pt-4 border-t border-brand-dark/5">
            <span className="text-xs font-semibold text-brand-muted uppercase tracking-wider">
              Upcoming Games in Boston
            </span>
            <p className="text-xs text-brand-sage font-medium mt-1">
              This game + {game._playing_later.others.length} more — offer multi-game deal
            </p>
            <div className="mt-2 space-y-1.5">
              {game._playing_later.others.map(g => (
                <button
                  key={g.game_id}
                  onClick={() => navigate(`/schedule/${g.game_id}`)}
                  className="flex items-center gap-2 w-full text-left px-2.5 py-1.5 rounded-lg
                             hover:bg-brand-cream/40 transition-colors group">
                  <span className="text-xs font-medium text-brand-dark tabular-nums">
                    {g.game_date_display || g.game_date}
                  </span>
                  {g.home_school && (
                    <span className="text-xs text-brand-muted">
                      at {g.home_school}
                    </span>
                  )}
                  <svg className="w-3.5 h-3.5 text-brand-muted/0 group-hover:text-brand-muted ml-auto
                                  transition-colors" fill="none" viewBox="0 0 24 24"
                       stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                  </svg>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Timing recommendation */}
        {game.recommendation && !game._playing_later?.others?.length && (
          <div className="mt-3">
            <p className="text-xs text-brand-sage font-medium">{game.recommendation}</p>
          </div>
        )}
        {game.recommendation && game._playing_later?.others?.length && (() => {
          // Show only timing recs (strip the multi-game part since it's shown above)
          const timingRec = game.recommendation.split(' | ')
            .filter((p: string) => !p.includes('multi-game'))
            .join(' | ')
          return timingRec ? (
            <div className="mt-2">
              <p className="text-xs text-brand-sage font-medium">{timingRec}</p>
            </div>
          ) : null
        })()}
      </div>

      {/* Action buttons */}
      <div className="flex items-center gap-3 flex-wrap">
        {contact && contact.email && !contact.do_not_contact && (
          <button
            onClick={handleDraftEmail}
            disabled={drafting}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium
                       bg-brand-sage text-white hover:bg-brand-sage/90
                       disabled:opacity-50 disabled:cursor-not-allowed
                       focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-sage/50
                       active:scale-[0.98] transition-transform">
            {drafting ? (
              <>
                <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                Creating draft...
              </>
            ) : (
              <>
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round"
                    d="M16 12H8m8 0l-4-4m4 4l-4 4M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                Draft Email
              </>
            )}
          </button>
        )}

        {game.outreach_status !== 'Booked' && (
          <button
            onClick={handleCreateOrder}
            disabled={creatingOrder}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium
                       bg-brand-dark text-white hover:bg-brand-dark/90
                       disabled:opacity-50 disabled:cursor-not-allowed
                       focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-dark/50
                       active:scale-[0.98] transition-transform">
            {creatingOrder ? (
              <>
                <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                Creating order...
              </>
            ) : (
              <>
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round"
                    d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
                Convert to Order
              </>
            )}
          </button>
        )}

        {draftError && (
          <span className="text-xs text-red-600">{draftError}</span>
        )}
      </div>

      {/* Order result */}
      {orderResult && (
        <div className={`rounded-xl p-3 text-sm ${
          orderResult.ok
            ? 'bg-emerald-50 border border-emerald-200 text-emerald-800'
            : 'bg-red-50 border border-red-200 text-red-800'
        }`}>
          {orderResult.ok
            ? `Order ${orderResult.order_id} created in Notion — game marked as Booked`
            : `Failed to create order: ${orderResult.error}`
          }
        </div>
      )}

      {/* Draft warnings (e.g., previously emailed) */}
      {draftWarnings.length > 0 && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl p-3 text-sm text-amber-800">
          {draftWarnings.map((w, i) => <p key={i}>{w}</p>)}
        </div>
      )}

      {/* Email threads — grouped by Gmail Thread ID */}
      {(() => {
        // Combine all emails, then split by thread
        const allEmails = [...emails, ...contactEmails]
        const gameEmailIds = new Set(emails.map(e => e.id))

        // Group by thread ID (emails without thread ID get their own group)
        const threadMap = new Map<string, EmailEntry[]>()
        for (const email of allEmails) {
          const key = email.gmail_thread_id || `solo-${email.id}`
          const list = threadMap.get(key) || []
          list.push(email)
          threadMap.set(key, list)
        }

        // Sort messages within each thread by date (oldest first = conversation order)
        for (const msgs of threadMap.values()) {
          msgs.sort((a, b) => (a.sent_at || a.created || '').localeCompare(b.sent_at || b.created || ''))
        }

        // Split threads: "this game" vs "other outreach"
        // A thread belongs to this game if ANY email in it is linked to this game
        const gameThreads: [string, EmailEntry[]][] = []
        const otherThreads: [string, EmailEntry[]][] = []

        for (const [threadKey, msgs] of threadMap.entries()) {
          const belongsToGame = msgs.some(e => gameEmailIds.has(e.id))
          if (belongsToGame) {
            gameThreads.push([threadKey, msgs])
          } else {
            otherThreads.push([threadKey, msgs])
          }
        }

        // If game has no direct threads, promote all contact threads to primary
        const primaryThreads = gameThreads.length > 0 ? gameThreads : otherThreads
        const secondaryThreads = gameThreads.length > 0 ? otherThreads : []
        const showingContactThreads = gameThreads.length === 0 && otherThreads.length > 0

        return (
          <>
            <div>
              <h3 className="text-xs font-semibold text-brand-muted uppercase tracking-wider mb-3">
                {showingContactThreads
                  ? `Email threads with this contact (${primaryThreads.length})`
                  : `Email thread${primaryThreads.length !== 1 ? 's' : ''} for this game (${primaryThreads.length})`
                }
              </h3>

              {primaryThreads.length === 0 ? (
                <div className="bg-white rounded-xl border border-brand-dark/5 p-6
                                shadow-[0_1px_3px_rgba(71,84,23,0.04)]">
                  {game.outreach_status && game.outreach_status !== 'Not Contacted' && game.outreach_status !== 'Missed' ? (
                    <div className="text-center">
                      <p className="text-sm text-brand-dark font-medium">Emailed manually (pre-CRM)</p>
                      <p className="text-xs text-brand-muted mt-1">
                        This outreach was sent by Meire before the CRM was set up.
                        {contact?.last_emailed && ` Last emailed ${formatDate(contact.last_emailed)}.`}
                        {contact?.first_emailed && contact.first_emailed !== contact?.last_emailed &&
                          ` First emailed ${formatDate(contact.first_emailed)}.`}
                      </p>
                    </div>
                  ) : (
                    <p className="text-sm text-brand-muted text-center">No emails sent yet.</p>
                  )}
                </div>
              ) : (
                <div className="space-y-3">
                  {primaryThreads.map(([threadKey, msgs]) => (
                    <ThreadCard
                      key={threadKey}
                      messages={msgs}
                      expanded={expandedThread === threadKey}
                      onToggle={() => setExpandedThread(expandedThread === threadKey ? null : threadKey)}
                    />
                  ))}
                </div>
              )}
            </div>

            {/* Other outreach threads — different campaigns/games */}
            {secondaryThreads.length > 0 && (
              <div>
                <button
                  onClick={() => setShowOtherOutreach(!showOtherOutreach)}
                  className="flex items-center gap-2 text-xs font-semibold text-brand-muted uppercase tracking-wider mb-3
                             hover:text-brand-dark transition-colors">
                  <svg className={`w-3.5 h-3.5 transition-transform ${showOtherOutreach ? 'rotate-90' : ''}`}
                    fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                  </svg>
                  Other outreach threads ({secondaryThreads.length})
                </button>

                {showOtherOutreach && (
                  <div className="space-y-3">
                    {secondaryThreads.map(([threadKey, msgs]) => (
                      <ThreadCard
                        key={threadKey}
                        messages={msgs}
                        expanded={expandedThread === threadKey}
                        onToggle={() => setExpandedThread(expandedThread === threadKey ? null : threadKey)}
                        muted
                      />
                    ))}
                  </div>
                )}
              </div>
            )}
          </>
        )
      })()}
    </div>
  )
}


function ThreadCard({ messages, expanded, onToggle, muted = false }: {
  messages: EmailEntry[]
  expanded: boolean
  onToggle: () => void
  muted?: boolean
}) {
  // First message is the original outreach, rest are follow-ups/replies
  const first = messages[0]
  if (!first) return null
  const hasReplies = messages.length > 1
  const latestStatus = messages[messages.length - 1]?.status ?? first.status
  const anyResponse = messages.some(m => m.response_date)
  const responseType = messages.find(m => m.response_type)?.response_type

  const responseTypeStyles: Record<string, string> = {
    'Interested': 'bg-emerald-50 text-emerald-700 border-emerald-200',
    'Booked': 'bg-status-booked/15 text-status-booked border-status-booked/30',
    'Not Interested': 'bg-status-declined/15 text-status-declined border-status-declined/30',
    'Question': 'bg-amber-50 text-amber-700 border-amber-200',
    'Out of Office': 'bg-gray-100 text-gray-500 border-gray-200',
  }

  return (
    <div className={`bg-white rounded-xl border border-brand-dark/5
                     shadow-[0_1px_3px_rgba(71,84,23,0.04)] overflow-hidden
                     ${muted ? 'opacity-75' : ''}`}>
      {/* Thread header — click to expand */}
      <button
        onClick={onToggle}
        className="w-full text-left p-4 hover:bg-brand-cream/30 transition-colors">
        <div className="flex items-start justify-between gap-3">
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-brand-dark truncate">
              {first.subject || '(no subject)'}
            </p>
            <p className="text-xs text-brand-muted mt-0.5">
              To: {first.to_email}
              {first.sent_at && ` \u00b7 ${formatDate(first.sent_at)}`}
              {hasReplies && ` \u00b7 ${messages.length} messages`}
            </p>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <StatusBadge status={latestStatus} />
            {anyResponse && (
              <span className="text-xs text-status-responded font-medium">
                Replied
              </span>
            )}
            {responseType && (
              <span className={`text-xs px-1.5 py-0.5 rounded-full font-medium border
                                ${responseTypeStyles[responseType] || 'bg-gray-100 text-gray-500 border-gray-200'}`}>
                {responseType}
              </span>
            )}
            <svg className={`w-4 h-4 text-brand-muted transition-transform ${
              expanded ? 'rotate-180' : ''
            }`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
            </svg>
          </div>
        </div>
      </button>

      {/* Expanded: show all messages in thread */}
      {expanded && (
        <div className="border-t border-brand-dark/5">
          {messages.map((msg, i) => (
            <div key={msg.id} className={`px-4 py-3 ${i > 0 ? 'border-t border-brand-dark/5' : ''}`}>
              <div className="flex items-center gap-2 mb-1.5 flex-wrap">
                <span className="text-xs font-medium text-brand-dark">
                  {msg.to_email === first.to_email ? 'Meire' : msg.to_email}
                </span>
                {msg.sent_at && (
                  <span className="text-xs text-brand-muted">{formatDate(msg.sent_at)}</span>
                )}
                <StatusBadge status={msg.status} />
                {msg.response_date && (
                  <span className="text-xs text-status-responded font-medium">
                    Replied {formatDate(msg.response_date)}
                  </span>
                )}
                {msg.response_type && (
                  <span className={`text-xs px-1.5 py-0.5 rounded-full font-medium border
                                    ${responseTypeStyles[msg.response_type] || 'bg-gray-100 text-gray-500 border-gray-200'}`}>
                    {msg.response_type}
                  </span>
                )}
              </div>

              {/* Response notes — the actual reply text */}
              {msg.response_notes && (
                <div className="mb-2 p-2.5 rounded-lg bg-status-responded/5 border border-status-responded/15">
                  <p className="text-xs font-semibold text-status-responded uppercase tracking-wider mb-1">
                    Their Reply
                  </p>
                  <p className="text-sm text-brand-dark leading-relaxed italic">
                    {msg.response_notes}
                  </p>
                </div>
              )}

              <div className="text-sm text-brand-dark whitespace-pre-wrap leading-relaxed">
                {msg.body || '(no body)'}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}


function formatDate(dateStr: string): string {
  if (!dateStr) return ''
  try {
    const dt = new Date(dateStr)
    return dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
  } catch {
    return dateStr
  }
}

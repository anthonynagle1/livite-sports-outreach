import { useState } from 'react'
import type { EmailEntry } from '../api/types'
import { RESPONSE_TYPES } from '../api/types'
import StatusBadge from './StatusBadge'
import { api } from '../api/client'

const RESPONSE_TYPE_STYLES: Record<string, string> = {
  'Interested': 'bg-emerald-50 text-emerald-700 border-emerald-200',
  'Booked': 'bg-status-booked/15 text-status-booked border-status-booked/30',
  'Not Interested': 'bg-status-declined/15 text-status-declined border-status-declined/30',
  'Question': 'bg-amber-50 text-amber-700 border-amber-200',
  'Out of Office': 'bg-gray-100 text-gray-500 border-gray-200',
}

interface EmailPreviewProps {
  email: EmailEntry
  onUpdate: () => void
  highlight?: boolean
  muted?: boolean
}

export default function EmailPreview({ email, onUpdate, highlight, muted }: EmailPreviewProps) {
  const [approving, setApproving] = useState(false)
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const [editSubject, setEditSubject] = useState(email.subject)
  const [editBody, setEditBody] = useState(email.body)
  const [updatingType, setUpdatingType] = useState(false)

  const handleApprove = async () => {
    setApproving(true)
    try {
      await api.post(`/api/emails/${email.id}/approve`)
      onUpdate()
    } catch (err) {
      console.error('Failed to approve:', err)
    } finally {
      setApproving(false)
    }
  }

  const handleEdit = () => {
    setEditSubject(email.subject)
    setEditBody(email.body)
    setEditing(true)
  }

  const handleCancel = () => {
    setEditing(false)
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      await api.put(`/api/emails/${email.id}`, {
        subject: editSubject,
        body: editBody,
      })
      setEditing(false)
      onUpdate()
    } catch (err) {
      console.error('Failed to save:', err)
    } finally {
      setSaving(false)
    }
  }

  const handleResponseTypeChange = async (newType: string) => {
    setUpdatingType(true)
    try {
      await api.put(`/api/emails/${email.id}/response-type`, {
        response_type: newType,
      })
      onUpdate()
    } catch (err) {
      console.error('Failed to update response type:', err)
    } finally {
      setUpdatingType(false)
    }
  }

  if (editing) {
    return (
      <div className="bg-white rounded-xl border-2 border-brand-sage/30 p-4
                      shadow-[0_1px_3px_rgba(71,84,23,0.04)]">
        <div className="flex items-start justify-between gap-3 mb-3">
          <p className="text-xs font-medium text-brand-sage">Editing Draft</p>
          <StatusBadge status={email.status} />
        </div>

        <input
          type="text"
          value={editSubject}
          onChange={e => setEditSubject(e.target.value)}
          className="w-full px-3 py-2 text-sm font-semibold text-brand-dark
                     bg-brand-cream/50 border border-brand-dark/10 rounded-lg
                     focus:outline-none focus:ring-2 focus:ring-brand-sage/30
                     focus:border-brand-sage"
          placeholder="Subject"
        />

        <p className="text-xs text-brand-muted mt-2">
          To: {email.to_email}
          {email.school && ` · ${email.school}`}
          {email.sport && ` · ${email.sport}`}
        </p>

        <textarea
          value={editBody}
          onChange={e => setEditBody(e.target.value)}
          rows={12}
          className="w-full mt-2 px-3 py-2 text-sm text-brand-dark font-body
                     bg-brand-cream/50 border border-brand-dark/10 rounded-lg
                     focus:outline-none focus:ring-2 focus:ring-brand-sage/30
                     focus:border-brand-sage resize-y leading-relaxed"
          placeholder="Email body"
        />

        <div className="flex gap-2 mt-3 pt-3 border-t border-brand-dark/5">
          <button
            onClick={handleSave}
            disabled={saving}
            className="text-xs font-medium bg-brand-sage text-white px-3 py-1.5 rounded-lg
                       hover:bg-brand-sage/90 active:bg-brand-sage/80
                       disabled:opacity-50 transition-colors"
          >
            {saving ? 'Saving...' : 'Save'}
          </button>
          <button
            onClick={handleCancel}
            disabled={saving}
            className="text-xs font-medium text-brand-muted px-3 py-1.5 rounded-lg
                       hover:bg-brand-cream transition-colors"
          >
            Cancel
          </button>
        </div>

        <div className="flex items-center gap-3 mt-2 text-xs text-brand-muted/70">
          {email.game_date && <span>Game: {email.game_date}</span>}
          {email.created && <span>Created: {new Date(email.created).toLocaleDateString()}</span>}
        </div>
      </div>
    )
  }

  return (
    <div className={`bg-white rounded-xl border shadow-[0_1px_3px_rgba(71,84,23,0.04)]
                     ${highlight ? 'border-status-responded/30 ring-1 ring-status-responded/10' : 'border-brand-dark/5'}
                     ${muted ? 'opacity-60' : ''}`}>
      {/* Clickable header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full text-left p-4 hover:bg-brand-cream/30 transition-colors">
        <div className="flex items-start justify-between gap-3">
          <div className="flex-1 min-w-0">
            <p className="text-sm font-semibold text-brand-dark truncate">
              {email.subject || '(No subject)'}
            </p>
            <p className="text-xs text-brand-muted mt-0.5">
              To: {email.to_email || '(unknown)'}
              {email.sport && ` · ${email.sport}`}
            </p>
            {/* Response info */}
            {email.response_date && (
              <div className="flex items-center gap-2 mt-1">
                <span className="text-xs text-status-responded font-medium">
                  Replied {formatMetaDate(email.response_date)}
                </span>
                {email.response_type && (
                  <span className={`text-xs px-1.5 py-0.5 rounded-full font-medium border
                                    ${RESPONSE_TYPE_STYLES[email.response_type] || 'bg-gray-100 text-gray-500 border-gray-200'}`}>
                    {email.response_type}
                  </span>
                )}
              </div>
            )}
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <StatusBadge status={email.status} />
            <svg className={`w-4 h-4 text-brand-muted transition-transform ${
              expanded ? 'rotate-180' : ''
            }`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
            </svg>
          </div>
        </div>

        {/* Collapsed preview */}
        {!expanded && (
          <>
            {/* Show response notes snippet if available */}
            {email.response_notes ? (
              <p className="text-xs text-brand-dark/70 mt-1.5 line-clamp-2 italic">
                {email.response_notes}
              </p>
            ) : (
              <p className="text-xs text-brand-muted mt-1.5 line-clamp-2">
                {email.body}
              </p>
            )}
          </>
        )}
      </button>

      {/* Expanded: full email body + response details */}
      {expanded && (
        <div className="px-4 pb-4">
          {/* Response section — shown prominently when there's a reply */}
          {email.response_received && email.response_notes && (
            <div className="mb-3 p-3 rounded-lg bg-status-responded/5 border border-status-responded/15">
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-xs font-semibold text-status-responded uppercase tracking-wider">
                  Response
                </span>
                {email.response_date && (
                  <span className="text-xs text-brand-muted">{formatMetaDate(email.response_date)}</span>
                )}
              </div>
              <p className="text-sm text-brand-dark leading-relaxed italic">
                {email.response_notes}
              </p>

              {/* Response type selector */}
              <div className="mt-2 flex items-center gap-2 flex-wrap" onClick={e => e.stopPropagation()}>
                <span className="text-xs text-brand-muted">Vibe:</span>
                {RESPONSE_TYPES.map(type => (
                  <button
                    key={type}
                    onClick={() => handleResponseTypeChange(type)}
                    disabled={updatingType}
                    className={`text-xs px-2 py-0.5 rounded-full font-medium border transition-colors
                                disabled:opacity-50
                                ${email.response_type === type
                                  ? RESPONSE_TYPE_STYLES[type]
                                  : 'bg-white border-brand-dark/10 text-brand-muted hover:border-brand-dark/20'
                                }`}
                  >
                    {type}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Original email body */}
          <div className="text-sm text-brand-dark leading-relaxed whitespace-pre-wrap font-body">
            {email.body}
          </div>

          {/* Actions */}
          {(email.status === 'Draft' || email.status === 'Approved') && (
            <div className="flex gap-2 mt-3 pt-3 border-t border-brand-dark/5">
              <button
                onClick={(e) => { e.stopPropagation(); handleEdit() }}
                className="text-xs font-medium text-brand-sage border border-brand-sage/30 px-3 py-1.5 rounded-lg
                           hover:bg-brand-sage/5 active:bg-brand-sage/10
                           transition-colors"
              >
                Edit
              </button>
              {email.status === 'Draft' && (
                <button
                  onClick={(e) => { e.stopPropagation(); handleApprove() }}
                  disabled={approving}
                  className="text-xs font-medium bg-brand-sage text-white px-3 py-1.5 rounded-lg
                             hover:bg-brand-sage/90 active:bg-brand-sage/80
                             disabled:opacity-50 transition-colors"
                >
                  {approving ? 'Approving...' : 'Approve'}
                </button>
              )}
            </div>
          )}

          {/* Response type selector for responded emails without notes */}
          {email.status === 'Responded' && !email.response_notes && (
            <div className="mt-3 pt-3 border-t border-brand-dark/5 flex items-center gap-2 flex-wrap"
                 onClick={e => e.stopPropagation()}>
              <span className="text-xs text-brand-muted">Vibe:</span>
              {RESPONSE_TYPES.map(type => (
                <button
                  key={type}
                  onClick={() => handleResponseTypeChange(type)}
                  disabled={updatingType}
                  className={`text-xs px-2 py-0.5 rounded-full font-medium border transition-colors
                              disabled:opacity-50
                              ${email.response_type === type
                                ? RESPONSE_TYPE_STYLES[type]
                                : 'bg-white border-brand-dark/10 text-brand-muted hover:border-brand-dark/20'
                              }`}
                >
                  {type}
                </button>
              ))}
            </div>
          )}

          {/* Meta */}
          <div className="flex items-center gap-3 mt-2 text-xs text-brand-muted/70">
            {email.game_date && <span>Game: {email.game_date}</span>}
            {email.sent_at && <span>Sent: {email.sent_at}</span>}
            {email.response_date && <span className="text-status-responded">Replied: {formatMetaDate(email.response_date)}</span>}
            {email.created && <span>Created: {new Date(email.created).toLocaleDateString()}</span>}
          </div>
        </div>
      )}
    </div>
  )
}

function formatMetaDate(dateStr: string): string {
  if (!dateStr) return ''
  try {
    return new Date(dateStr).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
  } catch {
    return dateStr
  }
}

import { useState } from 'react'
import type { EmailEntry } from '../api/types'
import StatusBadge from './StatusBadge'
import { api } from '../api/client'

interface EmailPreviewProps {
  email: EmailEntry
  onUpdate: () => void
}

export default function EmailPreview({ email, onUpdate }: EmailPreviewProps) {
  const [approving, setApproving] = useState(false)
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const [editSubject, setEditSubject] = useState(email.subject)
  const [editBody, setEditBody] = useState(email.body)

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

  if (editing) {
    return (
      <div className="bg-white rounded-xl border-2 border-brand-sage/30 p-4
                      shadow-[0_1px_3px_rgba(71,84,23,0.04)]">
        <div className="flex items-start justify-between gap-3 mb-3">
          <p className="text-xs font-medium text-brand-sage">Editing Draft</p>
          <StatusBadge status={email.status} />
        </div>

        {/* Subject */}
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

        {/* To line (read-only) */}
        <p className="text-xs text-brand-muted mt-2">
          To: {email.to_email}
          {email.school && ` · ${email.school}`}
          {email.sport && ` · ${email.sport}`}
        </p>

        {/* Body */}
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

        {/* Edit actions */}
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

        {/* Meta */}
        <div className="flex items-center gap-3 mt-2 text-xs text-brand-muted/70">
          {email.game_date && <span>Game: {email.game_date}</span>}
          {email.created && <span>Created: {new Date(email.created).toLocaleDateString()}</span>}
        </div>
      </div>
    )
  }

  return (
    <div className="bg-white rounded-xl border border-brand-dark/5
                    shadow-[0_1px_3px_rgba(71,84,23,0.04)]">
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

        {/* Collapsed preview — just first 2 lines */}
        {!expanded && (
          <p className="text-xs text-brand-muted mt-1.5 line-clamp-2">
            {email.body}
          </p>
        )}
      </button>

      {/* Expanded: full email body */}
      {expanded && (
        <div className="px-4 pb-4">
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

          {/* Meta */}
          <div className="flex items-center gap-3 mt-2 text-xs text-brand-muted/70">
            {email.game_date && <span>Game: {email.game_date}</span>}
            {email.sent_at && <span>Sent: {email.sent_at}</span>}
            {email.created && <span>Created: {new Date(email.created).toLocaleDateString()}</span>}
          </div>
        </div>
      )}
    </div>
  )
}

import { useState, useEffect, useCallback } from 'react'
import { api } from '../api/client'
import type { EmailEntry } from '../api/types'
import EmailPreview from '../components/EmailPreview'

const TABS = ['Draft', 'Approved', 'Sent', 'Responded'] as const

export default function Emails() {
  const [emails, setEmails] = useState<EmailEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [activeTab, setActiveTab] = useState<string>('Draft')
  const [approvingAll, setApprovingAll] = useState(false)

  const fetchEmails = useCallback(async () => {
    setLoading(true)
    try {
      const data = await api.get(`/api/emails?status=${activeTab}`)
      setEmails(data.emails)
    } catch (err) {
      console.error('Failed to fetch emails:', err)
    } finally {
      setLoading(false)
    }
  }, [activeTab])

  useEffect(() => { fetchEmails() }, [fetchEmails])

  const handleApproveAll = async () => {
    const draftIds = emails.filter(e => e.status === 'Draft').map(e => e.id)
    if (draftIds.length === 0) return

    setApprovingAll(true)
    try {
      await api.post('/api/emails/approve-batch', { email_ids: draftIds })
      fetchEmails()
    } catch (err) {
      console.error('Failed to approve batch:', err)
    } finally {
      setApprovingAll(false)
    }
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="font-display text-2xl font-bold text-brand-dark">Emails</h2>
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-1 bg-white rounded-lg p-1 border border-brand-dark/5
                      shadow-[0_1px_3px_rgba(71,84,23,0.04)] w-fit">
        {TABS.map(tab => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
              activeTab === tab
                ? 'bg-brand-sage text-white'
                : 'text-brand-muted hover:text-brand-dark'
            }`}
          >
            {tab}
          </button>
        ))}
      </div>

      {/* Bulk actions */}
      {activeTab === 'Draft' && emails.length > 0 && (
        <div className="flex items-center gap-3">
          <button
            onClick={handleApproveAll}
            disabled={approvingAll}
            className="text-xs font-medium bg-brand-sage text-white px-3 py-1.5 rounded-lg
                       hover:bg-brand-sage/90 active:bg-brand-sage/80
                       disabled:opacity-50 transition-all"
          >
            {approvingAll ? 'Approving...' : `Approve All (${emails.length})`}
          </button>
          <span className="text-xs text-brand-muted">
            {emails.length} draft{emails.length !== 1 ? 's' : ''}
          </span>
        </div>
      )}

      {/* Email list */}
      {loading ? (
        <div className="space-y-3">
          {[1, 2, 3].map(i => (
            <div key={i} className="animate-pulse h-32 bg-white rounded-xl" />
          ))}
        </div>
      ) : emails.length === 0 ? (
        <div className="text-center py-12">
          <p className="text-brand-muted">
            No {activeTab.toLowerCase()} emails.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {emails.map(email => (
            <EmailPreview
              key={email.id}
              email={email}
              onUpdate={fetchEmails}
            />
          ))}
        </div>
      )}
    </div>
  )
}

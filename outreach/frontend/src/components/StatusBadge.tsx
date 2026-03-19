const STATUS_STYLES: Record<string, string> = {
  'Not Contacted': 'bg-status-none/15 text-status-none',
  'Introduction Email - Sent': 'bg-status-sent/15 text-status-sent',
  'Follow-Up Email - Sent': 'bg-amber-100 text-amber-700',
  'Responded': 'bg-status-responded/15 text-status-responded',
  'In Conversation': 'bg-purple-100 text-purple-700',
  'Interested': 'bg-yellow-100 text-yellow-700',
  'Booked': 'bg-status-booked/15 text-status-booked',
  'Not Interested': 'bg-status-declined/15 text-status-declined',
  'No Response': 'bg-gray-200 text-gray-600',
  'Out of Office': 'bg-gray-100 text-gray-500',
  'Missed': 'bg-gray-100 text-gray-500',
  'Draft': 'bg-gray-100 text-gray-600',
  'Approved': 'bg-amber-100 text-amber-700',
  'Sent': 'bg-blue-100 text-blue-700',
}

interface StatusBadgeProps {
  status: string
  size?: 'sm' | 'md'
}

export default function StatusBadge({ status, size = 'sm' }: StatusBadgeProps) {
  const style = STATUS_STYLES[status] || 'bg-gray-100 text-gray-500'
  const sizeClass = size === 'sm' ? 'text-xs px-2 py-0.5' : 'text-sm px-2.5 py-1'

  return (
    <span className={`inline-flex items-center rounded-full font-medium ${style} ${sizeClass}`}>
      {status}
    </span>
  )
}

interface FilterBarProps {
  sport: string
  onSportChange: (sport: string) => void
  status: string
  onStatusChange: (status: string) => void
  gender: string
  onGenderChange: (gender: string) => void
}

const SPORTS = ['', 'Baseball', 'Softball', 'Lacrosse', 'Soccer', 'Basketball', 'Football', 'Hockey', 'Volleyball']
const STATUSES = ['', 'Not Contacted', 'Introduction Email - Sent', 'Follow-Up Email - Sent', 'Responded', 'In Conversation', 'Interested', 'Booked', 'Not Interested', 'No Response', 'Out of Office', 'Missed']
const GENDERS = ['', 'Men', 'Women']

export default function FilterBar({
  sport, onSportChange,
  status, onStatusChange,
  gender, onGenderChange,
}: FilterBarProps) {
  const selectClass = `text-sm bg-white border border-brand-dark/10 rounded-lg px-2.5 py-1.5
                       text-brand-dark focus:outline-none focus:ring-2 focus:ring-brand-sage/30
                       focus:border-brand-sage transition-colors`

  return (
    <div className="flex flex-wrap gap-2">
      <select
        value={sport}
        onChange={e => onSportChange(e.target.value)}
        className={selectClass}
      >
        <option value="">All Sports</option>
        {SPORTS.filter(Boolean).map(s => (
          <option key={s} value={s}>{s}</option>
        ))}
      </select>

      <select
        value={status}
        onChange={e => onStatusChange(e.target.value)}
        className={selectClass}
      >
        <option value="">All Statuses</option>
        {STATUSES.filter(Boolean).map(s => (
          <option key={s} value={s}>{s}</option>
        ))}
      </select>

      <select
        value={gender}
        onChange={e => onGenderChange(e.target.value)}
        className={selectClass}
      >
        <option value="">All Genders</option>
        {GENDERS.filter(Boolean).map(g => (
          <option key={g} value={g}>{g}</option>
        ))}
      </select>
    </div>
  )
}

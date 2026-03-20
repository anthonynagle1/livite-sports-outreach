import { NavLink, useLocation } from 'react-router-dom'
import { api } from '../api/client'

const NAV_ITEMS = [
  { path: '/', label: 'Dashboard', icon: '◈' },
  { path: '/schedule', label: 'Schedule', icon: '◻' },
  { path: '/calendar', label: 'Calendar', icon: '▦' },
  { path: '/emails', label: 'Emails', icon: '◎' },
]

interface LayoutProps {
  user: { name: string; role: string }
  onLogout: () => void
  children: React.ReactNode
}

export default function Layout({ user, onLogout, children }: LayoutProps) {
  const location = useLocation()

  const handleLogout = async () => {
    await api.post('/api/auth/logout').catch(() => {})
    onLogout()
  }

  return (
    <div className="min-h-screen flex flex-col md:flex-row">
      {/* Desktop sidebar */}
      <aside className="hidden md:flex md:flex-col md:w-56 bg-brand-sage text-white/90 p-4 sticky top-0 h-screen">
        <div className="mb-8">
          <h1 className="font-display text-xl font-bold tracking-tight text-white">
            Livite Outreach
          </h1>
          <p className="text-xs text-white/50 mt-1">NCAA Sports CRM</p>
        </div>

        <nav className="flex-1 space-y-1">
          {NAV_ITEMS.map(item => (
            <NavLink
              key={item.path}
              to={item.path}
              end={item.path === '/'}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors ${
                  isActive
                    ? 'bg-white/15 text-white'
                    : 'text-white/70 hover:bg-white/10 hover:text-white'
                }`
              }
            >
              <span className="text-lg">{item.icon}</span>
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className="pt-4 border-t border-white/10">
          <p className="text-xs text-white/50 mb-2">{user.name}</p>
          <button
            onClick={handleLogout}
            className="text-xs text-white/40 hover:text-white/70 transition-colors"
          >
            Sign out
          </button>
        </div>
      </aside>

      {/* Mobile header */}
      <header className="md:hidden flex items-center justify-between px-4 py-3 bg-brand-sage text-white">
        <h1 className="font-display text-lg font-bold">Livite Outreach</h1>
        <button
          onClick={handleLogout}
          className="text-xs text-white/60"
        >
          Sign out
        </button>
      </header>

      {/* Main content */}
      <main className="flex-1 min-h-0 overflow-y-auto pb-20 md:pb-0">
        <div className="max-w-5xl mx-auto p-4 md:p-6">
          {children}
        </div>
      </main>

      {/* Mobile bottom tabs */}
      <nav className="md:hidden fixed bottom-0 left-0 right-0 bg-white border-t border-brand-dark/10 flex justify-around py-2 safe-bottom">
        {NAV_ITEMS.map(item => {
          const isActive = item.path === '/'
            ? location.pathname === '/'
            : location.pathname.startsWith(item.path)
          return (
            <NavLink
              key={item.path}
              to={item.path}
              className={`flex flex-col items-center gap-0.5 px-3 py-1 text-xs font-medium transition-colors ${
                isActive ? 'text-brand-sage' : 'text-brand-muted'
              }`}
            >
              <span className="text-xl">{item.icon}</span>
              {item.label}
            </NavLink>
          )
        })}
      </nav>
    </div>
  )
}

import { useState } from 'react'
import { api } from '../api/client'

interface LoginProps {
  onLogin: (user: { name: string; role: string }) => void
}

export default function Login({ onLogin }: LoginProps) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)

    try {
      const data = await api.post('/api/auth/login', { username, password })
      onLogin({ name: data.name, role: data.role })
    } catch {
      setError('Invalid credentials')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-brand-cream p-4">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <h1 className="font-display text-3xl font-bold text-brand-sage tracking-tight">
            Livite Outreach
          </h1>
          <p className="text-sm text-brand-muted mt-1">NCAA Sports CRM</p>
        </div>

        <form
          onSubmit={handleSubmit}
          className="bg-white rounded-2xl p-6 shadow-[0_2px_12px_rgba(71,84,23,0.08)] border border-brand-dark/5"
        >
          <div className="space-y-4">
            <div>
              <label className="block text-xs font-medium text-brand-muted mb-1.5">
                Username
              </label>
              <input
                type="text"
                value={username}
                onChange={e => setUsername(e.target.value)}
                className="w-full px-3 py-2 text-sm bg-brand-cream/50 border border-brand-dark/10
                           rounded-lg focus:outline-none focus:ring-2 focus:ring-brand-sage/30
                           focus:border-brand-sage transition-colors"
                autoFocus
                required
              />
            </div>

            <div>
              <label className="block text-xs font-medium text-brand-muted mb-1.5">
                Password
              </label>
              <input
                type="password"
                value={password}
                onChange={e => setPassword(e.target.value)}
                className="w-full px-3 py-2 text-sm bg-brand-cream/50 border border-brand-dark/10
                           rounded-lg focus:outline-none focus:ring-2 focus:ring-brand-sage/30
                           focus:border-brand-sage transition-colors"
                required
              />
            </div>

            {error && (
              <p className="text-xs text-status-declined font-medium">{error}</p>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full py-2.5 text-sm font-semibold bg-brand-sage text-white rounded-lg
                         hover:bg-brand-sage/90 active:bg-brand-sage/80
                         disabled:opacity-50 transition-all"
            >
              {loading ? 'Signing in...' : 'Sign in'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

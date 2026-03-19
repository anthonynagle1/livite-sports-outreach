/**
 * API client with JSON handling and auth error detection.
 */

const API_PREFIX = '/outreach'

class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

async function request(url: string, options: RequestInit = {}) {
  const res = await fetch(`${API_PREFIX}${url}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
    credentials: 'same-origin',
  })

  if (res.status === 401) {
    throw new ApiError('Unauthorized', 401)
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({ error: res.statusText }))
    throw new ApiError(body.error || res.statusText, res.status)
  }

  return res.json()
}

export const api = {
  get: (url: string) => request(url),

  post: (url: string, data?: unknown) =>
    request(url, { method: 'POST', body: data ? JSON.stringify(data) : undefined }),

  put: (url: string, data: unknown) =>
    request(url, { method: 'PUT', body: JSON.stringify(data) }),
}

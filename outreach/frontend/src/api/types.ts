export interface Game {
  id: string
  game_id_title: string
  game_date: string
  game_date_display: string
  sport: string
  gender: string
  venue: string
  outreach_status: string
  visiting_team: string
  lead_score: number | null
  last_contacted: string
  follow_up_date: string
  notes: string
  local_game: boolean
  home_team_ids: string[]
  away_team_ids: string[]
  contact_ids: string[]
  home_school: string
  recommendation: string
  contact?: ContactSummary | null
  _playing_later: PlayingLater
}

export interface PlayingLater {
  total: number
  others: PlayingLaterGame[]
}

export interface PlayingLaterGame {
  game_id: string
  game_date: string
  game_date_display: string
  home_school?: string
}

export interface ContactSummary {
  id: string
  name: string
  email: string
  title: string
  sport?: string
  relationship?: string
  last_response_type?: string
  do_not_contact?: boolean
  response_notes?: string
  last_emailed?: string
  first_emailed?: string
}

export interface Contact {
  id: string
  name: string
  email: string
  phone: string
  title: string
  sport: string
  priority: number | null
  last_emailed: string
  school_ids: string[]
}

export interface School {
  id: string
  name: string
  athletics_url: string
  coaches_url: string
  conference: string
  division: string
  local: boolean
}

export interface EmailEntry {
  id: string
  email_id: string
  subject: string
  body: string
  status: string
  to_email: string
  school: string
  sport: string
  sent_at: string
  game_date: string
  game_ids: string[]
  contact_ids: string[]
  template_ids: string[]
  gmail_thread_id: string
  gmail_message_id: string
  response_date: string
  response_type: string
  response_notes: string
  response_received: boolean
  created: string
}

export const RESPONSE_TYPES = ['Interested', 'Not Interested', 'Booked', 'Question', 'Out of Office'] as const
export type ResponseType = typeof RESPONSE_TYPES[number]

export interface PipelineStats {
  games: Record<string, number>
  games_total: number
  emails: Record<string, number>
}

export interface ActivityItem {
  id: string
  subject: string
  status: string
  school: string
  sport: string
  to_email: string
  sent_at: string
  game_date: string
}

export type OutreachStatus = 'Not Contacted' | 'Introduction Email - Sent' | 'Follow-Up Email - Sent' | 'Responded' | 'In Conversation' | 'Interested' | 'Booked' | 'Not Interested' | 'No Response' | 'Out of Office' | 'Missed'

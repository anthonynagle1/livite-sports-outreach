import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        brand: {
          sage: '#475417',
          cream: '#F5EDDC',
          dark: '#2d2a24',
          muted: '#7a7265',
        },
        status: {
          none: '#94a3b8',      // Not Contacted — slate
          sent: '#eab308',       // Email Sent — yellow
          responded: '#3b82f6',  // Responded — blue
          booked: '#22c55e',     // Booked — green
          declined: '#ef4444',   // Declined — red
        },
      },
      fontFamily: {
        display: ['Lora', 'Georgia', 'serif'],
        body: ['DM Sans', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
} satisfies Config

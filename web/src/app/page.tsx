'use client';

import { useEffect, useState } from 'react';
import Image from 'next/image';

const tools = [
  {
    name: 'Hub',
    description: 'Real-time business health — system status, P&L, catering deliveries, and outreach pipeline.',
    href: '/hub',
    badge: 'Live',
    badgeColor: '#475417',
    symbol: '⬡',
  },
  {
    name: 'Sales Dashboard',
    description: 'Daily P&L, food cost trends, labor analysis, rolling forecasts, and anomaly detection.',
    href: '/dashboard',
    badge: 'Dashboard',
    badgeColor: '#8b7355',
    symbol: '◈',
  },
  {
    name: 'Scheduling',
    description: 'AI-driven labor scheduling, demand forecasting, and staff availability management.',
    href: '/schedule',
    badge: 'Dashboard',
    badgeColor: '#8b7355',
    symbol: '◻',
  },
  {
    name: 'Supplier Prices',
    description: 'Livite ingredient costs across Sysco, Baldor & FreshPoint — track changes and compare vendors.',
    href: '/prices',
    badge: 'Dashboard',
    badgeColor: '#8b7355',
    symbol: '◑',
  },
  {
    name: 'Restaurant Invoices',
    description: 'Livite vendor invoice tracking, cost reconciliation, and allocation by department.',
    href: '/invoices',
    badge: 'Dashboard',
    badgeColor: '#8b7355',
    symbol: '□',
  },
  {
    name: 'Outreach',
    description: 'NCAA catering contact automation — scrape schedules, match staff, draft and send at scale.',
    href: '#',
    badge: 'Automation',
    badgeColor: '#6b5a3e',
    symbol: '◎',
  },
];

function Clock() {
  const [time, setTime] = useState('');
  const [date, setDate] = useState('');

  useEffect(() => {
    function tick() {
      const now = new Date();
      setTime(now.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true }));
      setDate(now.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' }));
    }
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);

  return (
    <div style={{ textAlign: 'right' }}>
      <div style={{ fontFamily: "'Lora', Georgia, serif", fontSize: '22px', fontWeight: 600, color: '#2d2a24', letterSpacing: '-0.01em' }}>{time}</div>
      <div style={{ fontFamily: "'DM Sans', system-ui, sans-serif", fontSize: '12px', color: '#8b7355', marginTop: '2px' }}>{date}</div>
    </div>
  );
}

export default function Home() {
  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Lora:ital,wght@0,400;0,600;0,700;1,400&family=DM+Sans:wght@300;400;500;600&display=swap');

        * { box-sizing: border-box; }

        .tool-card {
          background: #fff;
          border-radius: 14px;
          padding: 24px;
          text-decoration: none;
          display: flex;
          flex-direction: column;
          gap: 10px;
          box-shadow: 0 1px 3px rgba(71,84,23,0.06), 0 4px 16px rgba(71,84,23,0.04);
          border: 1px solid rgba(71,84,23,0.07);
          cursor: pointer;
          transform: translateY(0);
          transition: transform 0.2s cubic-bezier(0.34,1.56,0.64,1), box-shadow 0.2s ease;
          position: relative;
          overflow: hidden;
        }
        .tool-card::before {
          content: '';
          position: absolute;
          top: 0; left: 0; right: 0;
          height: 3px;
          background: #475417;
          opacity: 0;
          transition: opacity 0.2s ease;
        }
        .tool-card:hover {
          transform: translateY(-3px);
          box-shadow: 0 4px 8px rgba(71,84,23,0.08), 0 12px 32px rgba(71,84,23,0.1);
        }
        .tool-card:hover::before { opacity: 1; }
        .tool-card:active { transform: translateY(-1px); }

        .divider {
          width: 100%;
          height: 1px;
          background: linear-gradient(to right, rgba(71,84,23,0.15), rgba(71,84,23,0.05), transparent);
          margin: 28px 0;
        }
      `}</style>

      <main style={{
        minHeight: '100vh',
        background: '#F5EDDC',
        padding: '48px 24px 64px',
        fontFamily: "'DM Sans', system-ui, sans-serif",
      }}>
        <div style={{ maxWidth: '960px', margin: '0 auto' }}>

          {/* Header */}
          <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '16px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '14px' }}>
              <Image
                src="/logo.png"
                alt="Livite"
                width={44}
                height={44}
                style={{ borderRadius: '10px', objectFit: 'cover' }}
              />
              <div>
                <div style={{
                  fontFamily: "'Lora', Georgia, serif",
                  fontSize: '28px',
                  fontWeight: 700,
                  color: '#2d2a24',
                  letterSpacing: '-0.02em',
                  lineHeight: 1,
                }}>
                  Livite
                </div>
                <div style={{
                  fontSize: '11px',
                  fontWeight: 500,
                  letterSpacing: '0.1em',
                  textTransform: 'uppercase',
                  color: '#8b7355',
                  marginTop: '4px',
                }}>
                  Operations Center
                </div>
              </div>
            </div>
            <Clock />
          </div>

          <div className="divider" />

          {/* Tagline */}
          <div style={{ marginBottom: '32px' }}>
            <p style={{
              fontFamily: "'Lora', Georgia, serif",
              fontSize: '17px',
              fontStyle: 'italic',
              color: '#6b5a3e',
              lineHeight: 1.6,
              maxWidth: '480px',
            }}>
              Everything you need to run the restaurant, in one place.
            </p>
          </div>

          {/* Tool Grid */}
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
            gap: '16px',
          }}>
            {tools.map((tool) => (
              <a
                key={tool.name}
                href={tool.href}
                className="tool-card"
                target={tool.href.startsWith('http') ? '_blank' : undefined}
                rel={tool.href.startsWith('http') ? 'noopener noreferrer' : undefined}
              >
                {/* Badge */}
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                  <span style={{
                    fontSize: '20px',
                    color: '#475417',
                    lineHeight: 1,
                  }}>{tool.symbol}</span>
                  <span style={{
                    fontSize: '10px',
                    fontWeight: 600,
                    letterSpacing: '0.06em',
                    textTransform: 'uppercase',
                    color: tool.badgeColor,
                    background: `${tool.badgeColor}18`,
                    padding: '3px 8px',
                    borderRadius: '20px',
                  }}>{tool.badge}</span>
                </div>

                {/* Name */}
                <div style={{
                  fontFamily: "'Lora', Georgia, serif",
                  fontSize: '20px',
                  fontWeight: 600,
                  color: '#2d2a24',
                  letterSpacing: '-0.01em',
                }}>
                  {tool.name}
                </div>

                {/* Description */}
                <p style={{
                  fontSize: '13px',
                  color: '#7a7265',
                  lineHeight: 1.6,
                  margin: 0,
                  flex: 1,
                }}>
                  {tool.description}
                </p>

                {/* CTA */}
                <div style={{
                  fontSize: '13px',
                  fontWeight: 600,
                  color: '#475417',
                  marginTop: '4px',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '4px',
                }}>
                  Open <span style={{ fontSize: '16px', lineHeight: 1 }}>→</span>
                </div>
              </a>
            ))}
          </div>

          {/* Footer */}
          <div style={{
            marginTop: '48px',
            paddingTop: '24px',
            borderTop: '1px solid rgba(71,84,23,0.1)',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            flexWrap: 'wrap',
            gap: '8px',
          }}>
            <span style={{ fontSize: '12px', color: '#b0a090' }}>
              Livite · Washington Square, Brookline MA
            </span>
            <a href="/logout" style={{ fontSize: '12px', color: '#b0a090', textDecoration: 'none' }}>
              Sign out
            </a>
          </div>

        </div>
      </main>
    </>
  );
}

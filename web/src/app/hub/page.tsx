'use client';

import { useEffect, useState, useCallback, useRef } from 'react';

// ── Types ────────────────────────────────────────────────

interface ServiceStatus {
  status: string;
  color: 'green' | 'yellow' | 'red' | 'gray';
  detail?: string;
  alerts?: string[];
  pending?: number;
}

interface HealthData {
  email_service: ServiceStatus;
  sales_dashboard: ServiceStatus;
  notion_api: ServiceStatus;
  pi_cron: ServiceStatus;
  cache_age?: number;
}

interface SalesData {
  error?: string;
  'Toast Total'?: number;
  toast_total?: number;
  Labor?: number;
  labor?: number;
  'Food Cost'?: number;
  food_cost?: number;
  Profit?: number;
  profit?: number;
}

interface PipelineData {
  error?: string;
  games?: Record<string, number>;
  queue?: Record<string, number>;
}

interface Delivery {
  name: string;
  date: string;
  status: string;
  amount: number | null;
}

// ── Helpers ──────────────────────────────────────────────

function fmt(n: number | null | undefined) {
  if (n == null) return '—';
  return '$' + Number(n).toLocaleString('en-US', { maximumFractionDigits: 0 });
}

function pct(n: number | null | undefined, total: number | null | undefined) {
  if (!total || !n) return '';
  return '(' + ((n / total) * 100).toFixed(1) + '%)';
}

function greeting() {
  const h = new Date().getHours();
  if (h < 12) return 'Good morning';
  if (h < 17) return 'Good afternoon';
  return 'Good evening';
}

function dateStr() {
  return new Date().toLocaleDateString('en-US', {
    weekday: 'long',
    month: 'long',
    day: 'numeric',
    year: 'numeric',
  });
}

// ── Sub-components ────────────────────────────────────────

function Dot({ color }: { color: string }) {
  const map: Record<string, string> = {
    green: 'bg-green-500',
    yellow: 'bg-yellow-400',
    red: 'bg-red-500',
    gray: 'bg-gray-300',
  };
  return <span className={`inline-block w-2.5 h-2.5 rounded-full flex-shrink-0 ${map[color] ?? 'bg-gray-300'}`} />;
}

function Skeleton({ w = '80%' }: { w?: string }) {
  return (
    <div
      className="h-4 rounded my-1 animate-pulse bg-gradient-to-r from-stone-100 via-stone-200 to-stone-100"
      style={{ width: w }}
    />
  );
}

function Card({ title, children, count }: { title: string; children: React.ReactNode; count?: number }) {
  return (
    <div className="bg-white rounded-xl p-4 mb-3 shadow-sm">
      <p className="text-[11px] font-bold tracking-wide uppercase text-stone-400 mb-3">
        {title}{count != null ? ` (${count})` : ''}
      </p>
      {children}
    </div>
  );
}

// ── Health Card ──────────────────────────────────────────

function HealthCard({ data }: { data: HealthData | null }) {
  const systems: [keyof HealthData, string][] = [
    ['email_service', 'Email Service'],
    ['sales_dashboard', 'Sales Dashboard'],
    ['notion_api', 'Notion API'],
    ['pi_cron', 'Pi Cron'],
  ];
  return (
    <Card title="System Health">
      {data == null ? (
        <>
          <Skeleton w="75%" />
          <Skeleton w="65%" />
          <Skeleton w="70%" />
          <Skeleton w="60%" />
        </>
      ) : (
        systems.map(([key, label]) => {
          const info = data[key] as ServiceStatus | undefined;
          return (
            <div key={key} className="flex items-center gap-2.5 py-1.5 text-sm">
              <Dot color={info?.color ?? 'gray'} />
              <span className="flex-1 text-stone-700">{label}</span>
              <span className="text-stone-400 text-xs">{info?.detail ?? info?.status ?? ''}</span>
            </div>
          );
        })
      )}
    </Card>
  );
}

// ── Sales Card ───────────────────────────────────────────

function SalesCard({ data }: { data: SalesData | null | 'loading' }) {
  if (data === 'loading') {
    return (
      <Card title="Yesterday's Numbers">
        <Skeleton w="55%" />
        <Skeleton w="45%" />
      </Card>
    );
  }
  if (!data || data.error) {
    return (
      <Card title="Yesterday's Numbers">
        <p className="text-sm text-stone-400 text-center py-3">Sales data unavailable</p>
      </Card>
    );
  }
  const revenue = data['Toast Total'] ?? data['toast_total'] ?? 0;
  const labor = data['Labor'] ?? data['labor'] ?? 0;
  const food = data['Food Cost'] ?? data['food_cost'] ?? 0;
  const profit = data['Profit'] ?? data['profit'] ?? 0;
  const profitPct = revenue > 0 ? ((profit / revenue) * 100).toFixed(1) : '0';
  const profitColor = profit >= 0 ? 'text-green-600' : 'text-red-600';

  return (
    <Card title="Yesterday's Numbers">
      <div className="grid grid-cols-2 gap-2">
        {[
          { label: 'Revenue', value: fmt(revenue), sub: '' },
          { label: 'Labor', value: fmt(labor), sub: pct(labor, revenue) },
          { label: 'Food Cost', value: fmt(food), sub: pct(food, revenue) },
          { label: 'Profit', value: fmt(profit), sub: `${profitPct}%`, color: profitColor },
        ].map(({ label, value, sub, color }) => (
          <div key={label} className="py-1">
            <p className="text-xs text-stone-400">{label}</p>
            <p className={`text-xl font-bold ${color ?? 'text-stone-800'}`}>{value}</p>
            {sub && <p className={`text-xs ${color ?? 'text-stone-400'}`}>{sub}</p>}
          </div>
        ))}
      </div>
      <a
        href="/dashboard"
        className="block text-center text-sm font-semibold text-[#475417] pt-2 mt-2 border-t border-stone-100 hover:opacity-70 transition-opacity"
      >
        View Full Dashboard →
      </a>
    </Card>
  );
}

// ── Pipeline Card ────────────────────────────────────────

function PipelineCard({ data }: { data: PipelineData | null | 'loading' }) {
  if (data === 'loading') {
    return (
      <Card title="Outreach Pipeline">
        <Skeleton w="60%" />
        <Skeleton w="55%" />
        <Skeleton w="50%" />
      </Card>
    );
  }
  if (!data || data.error) {
    return (
      <Card title="Outreach Pipeline">
        <p className="text-sm text-stone-400 text-center py-3">Pipeline unavailable</p>
      </Card>
    );
  }
  const g = data.games ?? {};
  const q = data.queue ?? {};
  const stages = [
    ['Not Contacted', 'Not Contacted'],
    ['Emailed', 'Email Sent'],
    ['Responded', 'Responded'],
    ['Booked', 'Booked'],
  ] as const;

  return (
    <Card title="Outreach Pipeline">
      {stages.map(([label, key]) => (
        <div key={key} className="flex items-center py-1 text-sm">
          <span className="w-9 text-right font-bold text-[#475417] mr-3">{g[key] ?? 0}</span>
          <span className="text-stone-700">{label}</span>
        </div>
      ))}
      <p className="text-xs text-stone-400 mt-2 pt-2 border-t border-stone-100">
        Queue: {q['Draft'] ?? 0} draft, {q['Approved'] ?? 0} approved
      </p>
    </Card>
  );
}

// ── Deliveries Card ──────────────────────────────────────

function DeliveriesCard({ data }: { data: Delivery[] | null | 'loading' }) {
  if (data === 'loading') {
    return (
      <Card title="This Week's Deliveries">
        <Skeleton w="90%" />
        <Skeleton w="85%" />
      </Card>
    );
  }
  if (!data || data.length === 0) {
    return (
      <Card title="This Week's Deliveries">
        <p className="text-sm text-stone-400 text-center py-3">No deliveries this week</p>
      </Card>
    );
  }
  return (
    <Card title="This Week's Deliveries" count={data.length}>
      {data.map((d, i) => (
        <div
          key={i}
          className="flex justify-between items-center py-2 border-b border-stone-100 last:border-0"
        >
          <div>
            <p className="text-xs text-stone-400">{d.date}</p>
            <p className="text-sm font-semibold text-stone-800">{d.name}</p>
          </div>
          <div className="text-right">
            {d.amount != null && (
              <p className="text-sm font-bold text-[#475417]">{fmt(d.amount)}</p>
            )}
            {d.status && (
              <span className="text-[11px] bg-stone-100 text-stone-500 rounded px-2 py-0.5">{d.status}</span>
            )}
          </div>
        </div>
      ))}
    </Card>
  );
}

// ── Alerts Card ──────────────────────────────────────────

function AlertsCard({ alerts }: { alerts: string[] }) {
  return (
    <Card title="Alerts">
      {alerts.length === 0 ? (
        <p className="text-sm text-stone-400 text-center py-2">All clear</p>
      ) : (
        alerts.map((a, i) => (
          <div key={i} className="flex items-start gap-2 py-2 text-sm text-stone-700">
            <span>⚠️</span>
            <span>{a}</span>
          </div>
        ))
      )}
    </Card>
  );
}

// ── Main Hub Page ─────────────────────────────────────────

export default function HubPage() {
  const [health, setHealth] = useState<HealthData | null>(null);
  const [sales, setSales] = useState<SalesData | null | 'loading'>('loading');
  const [pipeline, setPipeline] = useState<PipelineData | null | 'loading'>('loading');
  const [deliveries, setDeliveries] = useState<Delivery[] | null | 'loading'>('loading');
  const [alerts, setAlerts] = useState<string[]>([]);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [updatedLabel, setUpdatedLabel] = useState('Loading...');
  const [refreshing, setRefreshing] = useState(false);
  const [headerGreeting] = useState(greeting());
  const [headerDate] = useState(dateStr());

  const loadHealth = useCallback(async () => {
    try {
      const res = await fetch('/hub/api/status');
      if (!res.ok) return;
      const data: HealthData = await res.json();
      setHealth(data);
      // Derive alerts
      const newAlerts: string[] = [];
      const systems: [keyof HealthData, string][] = [
        ['email_service', 'Email Service'],
        ['sales_dashboard', 'Sales Dashboard'],
        ['notion_api', 'Notion API'],
        ['pi_cron', 'Pi Cron'],
      ];
      for (const [key, label] of systems) {
        const s = data[key] as ServiceStatus | undefined;
        if (s?.color === 'red') newAlerts.push(`${label}: ${s.status}`);
      }
      const pi = data.pi_cron as ServiceStatus | undefined;
      if (pi?.alerts) newAlerts.push(...pi.alerts);
      setAlerts(newAlerts);
    } catch {
      setHealth(null);
    }
  }, []);

  const loadSales = useCallback(async () => {
    try {
      const res = await fetch('/hub/api/sales');
      setSales(res.ok ? await res.json() : null);
    } catch {
      setSales(null);
    }
  }, []);

  const loadPipeline = useCallback(async () => {
    try {
      const res = await fetch('/hub/api/pipeline');
      setPipeline(res.ok ? await res.json() : null);
    } catch {
      setPipeline(null);
    }
  }, []);

  const loadDeliveries = useCallback(async () => {
    try {
      const res = await fetch('/hub/api/deliveries');
      setDeliveries(res.ok ? await res.json() : null);
    } catch {
      setDeliveries(null);
    }
  }, []);

  const refreshAll = useCallback(async () => {
    if (refreshing) return;
    setRefreshing(true);
    await Promise.all([loadHealth(), loadSales(), loadPipeline(), loadDeliveries()]);
    setLastUpdated(new Date());
    setRefreshing(false);
  }, [refreshing, loadHealth, loadSales, loadPipeline, loadDeliveries]);

  const lastUpdatedRef = useRef<Date | null>(null);

  function computeLabel(d: Date): string {
    const diff = Math.floor((Date.now() - d.getTime()) / 1000);
    if (diff < 10) return 'Just now';
    if (diff < 60) return `${diff}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    return d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
  }

  useEffect(() => {
    setTimeout(refreshAll, 0);
    const dataInterval = setInterval(refreshAll, 5 * 60 * 1000);
    const labelInterval = setInterval(() => {
      if (lastUpdatedRef.current) {
        setUpdatedLabel(computeLabel(lastUpdatedRef.current));
      }
    }, 15000);
    return () => { clearInterval(dataInterval); clearInterval(labelInterval); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    lastUpdatedRef.current = lastUpdated;
    if (lastUpdated) {
      setTimeout(() => setUpdatedLabel(computeLabel(lastUpdated)), 0);
    }
  }, [lastUpdated]);

  return (
    <div className="min-h-screen bg-[#F5EDDC]" style={{ fontFamily: "'DM Sans', -apple-system, system-ui, sans-serif" }}>
      {/* Header */}
      <div className="bg-[#475417] text-white px-5 pt-5 pb-6">
        <div className="flex items-center gap-2 mb-2">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/logo.png" alt="Livite" className="w-8 h-8 rounded-md object-cover" />
          <span className="text-lg font-bold">Livite Hub</span>
        </div>
        <p className="text-xl font-semibold">{headerGreeting}</p>
        <p className="text-sm opacity-80 mt-0.5">{headerDate}</p>
      </div>

      {/* Pull-to-refresh indicator */}
      {refreshing && (
        <div className="flex items-center justify-center gap-2 py-2 text-xs text-stone-500 bg-stone-50">
          <span className="inline-block w-4 h-4 border-2 border-stone-200 border-t-[#475417] rounded-full animate-spin" />
          Refreshing...
        </div>
      )}

      {/* Content */}
      <div className="px-4 py-4 max-w-lg mx-auto">
        <HealthCard data={health} />
        <SalesCard data={sales} />
        <PipelineCard data={pipeline} />
        <DeliveriesCard data={deliveries} />
        <AlertsCard alerts={alerts} />
      </div>

      {/* Footer */}
      <div className="text-center text-xs text-stone-400 pb-8 px-4">
        <div className="flex items-center justify-center gap-2">
          <span>Updated {updatedLabel}</span>
          <button
            onClick={refreshAll}
            className="text-[#475417] font-semibold px-2 py-1 active:opacity-50"
          >
            Refresh
          </button>
        </div>
        <div className="mt-1">
          <a href="/logout" className="text-[#475417]">
            Sign Out
          </a>
        </div>
      </div>
    </div>
  );
}

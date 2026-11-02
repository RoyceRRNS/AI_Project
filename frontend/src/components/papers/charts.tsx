/** Small, dependency-free charts. Each is one series in the accent colour with its value printed,
 *  so nothing is read from colour or from a tooltip alone. */

export const pct = (x: number) => `${Math.round(x * 100)}%`;
export const marks = (n: number | null | undefined) => (n === null || n === undefined ? "–" : `${Number(n.toFixed(1))}`);

export function StatTile({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="stat-tile">
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
      {hint && <span className="muted small">{hint}</span>}
    </div>
  );
}

export interface Bar { key: string; label: string; value: number; display: string; title?: string }

/** Horizontal bars for categories, each with its label and value beside the mark. */
export function BarList({ bars, label }: { bars: Bar[]; label: string }) {
  const top = Math.max(...bars.map((b) => b.value), 0);
  return (
    <ul className="bar-list" aria-label={label}>
      {bars.map((b) => (
        <li key={b.key} className="bar-row" title={b.title ?? `${b.label}: ${b.display}`}>
          <span className="bar-label">{b.label}</span>
          <span className="bar-track">
            <span className="bar-fill" style={{ width: top ? `${(b.value / top) * 100}%` : 0 }} />
          </span>
          <span className="bar-value">{b.display}</span>
        </li>
      ))}
    </ul>
  );
}

/** Upright columns for an ordered scale, such as how many marks a question carries. */
export function ColumnChart({ columns, label }: { columns: { key: string; value: number; caption: string }[]; label: string }) {
  const top = Math.max(...columns.map((c) => c.value), 1);
  return (
    <ul className="columns" aria-label={label}>
      {columns.map((c) => (
        <li key={c.key} className="column" title={`${c.caption}: ${c.value}`}>
          <span className="column-value">{c.value}</span>
          <span className="column-bar" style={{ height: `${(c.value / top) * 100}%` }} />
          <span className="column-caption">{c.caption}</span>
        </li>
      ))}
    </ul>
  );
}

export function YearChips({ years }: { years: number[] }) {
  if (!years.length) return null;
  return (
    <span className="year-chips">
      {years.map((y) => <span key={y} className="year-chip">{y}</span>)}
    </span>
  );
}

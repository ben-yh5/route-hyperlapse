import type { LngLat, Settings } from "./api";

export type Waypoint = LngLat & { label?: string };

export function fmtBytes(n: number | undefined | null): string {
  if (n == null || !Number.isFinite(n)) return "–";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = n;
  let u = 0;
  while (v >= 1024 && u < units.length - 1) {
    v /= 1024;
    u++;
  }
  return `${v.toFixed(v >= 100 || u === 0 ? 0 : 1)} ${units[u]}`;
}

export function fmtKm(m: number, digits = 1): string {
  return `${(m / 1000).toFixed(digits)} km`;
}

export function fmtDate(ms: number | null | undefined): string {
  if (ms == null) return "–";
  const d = new Date(ms);
  return Number.isNaN(d.getTime()) ? "–" : d.toISOString().slice(0, 10);
}

export function fmtCoord(w: LngLat): string {
  return `${w.lat.toFixed(5)}, ${w.lon.toFixed(5)}`;
}

export function waypointLabel(w: Waypoint | null): string {
  if (!w) return "";
  return w.label ?? fmtCoord(w);
}

/** Parse a "lat,lon" literal. */
export function parseLatLon(text: string): LngLat | null {
  const m = text.match(/^\s*(-?\d+(?:\.\d+)?)\s*[,\s]\s*(-?\d+(?:\.\d+)?)\s*$/);
  if (!m) return null;
  const lat = parseFloat(m[1]);
  const lon = parseFloat(m[2]);
  if (Math.abs(lat) > 90 || Math.abs(lon) > 180) return null;
  return { lat, lon };
}

/** Great-circle distance in metres. */
export function haversine(a: LngLat, b: LngLat): number {
  const R = 6371008.8;
  const rad = Math.PI / 180;
  const dLat = (b.lat - a.lat) * rad;
  const dLon = (b.lon - a.lon) * rad;
  const s =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(s)));
}

export function settingsEqual(a: Settings, b: Settings): boolean {
  const keys = Object.keys(a) as (keyof Settings)[];
  return keys.every((k) => (a[k] ?? null) === (b[k] ?? null));
}

export function effectiveMaxFrames(s: Settings): number {
  return s.max_frames ?? 12 * s.duration_s;
}

/** Client-side frame budget estimate (SPEC "Frame budget"). */
export function estimateBudget(distance_m: number, s: Settings) {
  const spacing = Math.max(s.min_spacing_m, distance_m / effectiveMaxFrames(s));
  const planned = Math.floor(distance_m / spacing);
  return { spacing, planned, source_fps: planned / s.duration_s };
}

import { useRef, useState, type ReactNode } from "react";
import type { CacheInfo, Config, PlanResponse, RouteResponse, Settings } from "../api";
import { estimateBudget, fmtBytes, fmtKm, type Waypoint } from "../format";
import WaypointInput from "./WaypointInput";

const FPS_OPTIONS = [24, 25, 30, 60];
const SIZES: [number, number][] = [
  [854, 480],
  [1280, 720],
  [1920, 1080],
];
const MIN_RENDER_FRAMES = 150;

type Props = {
  config: Config;
  settings: Settings;
  onSettings: (patch: Partial<Settings>) => void;

  start: Waypoint | null;
  end: Waypoint | null;
  vias: Waypoint[];
  onStart: (w: Waypoint | null) => void;
  onEnd: (w: Waypoint | null) => void;
  onVias: (v: Waypoint[]) => void;

  gpxActive: boolean;
  gpxPending: boolean;
  gpxError: string | null;
  onUploadGpx: (f: File) => void;
  onClearGpx: () => void;

  route: RouteResponse | null;
  routeLoading: boolean;
  routeError: string | null;

  plan: PlanResponse | null;
  planLoading: boolean;
  stale: string | null;
  planning: boolean;
  planError: string | null;
  canPlan: boolean;
  onPlan: () => void;

  rendering: boolean;
  renderStarting: boolean;
  renderError: string | null;
  onRender: () => void;

  cache: CacheInfo | null;
  clearingCache: boolean;
  clearCacheError: string | null;
  onClearCache: () => void;
  clearOnQuit: boolean;
  onClearOnQuit: (v: boolean) => void;

  showCoverage: boolean;
  onShowCoverage: (v: boolean) => void;
  onQuit: () => void;
};

export default function Sidebar(p: Props) {
  const { settings: s, onSettings } = p;
  const fileRef = useRef<HTMLInputElement>(null);
  const [addingVia, setAddingVia] = useState(false);
  const stats = p.plan?.stats;

  const renderBlock: string | null = !p.plan
    ? "Plan the route first."
    : p.stale
      ? "Re-plan before rendering."
      : stats && !stats.can_render
        ? `Only ${stats.kept} frames kept — at least ${MIN_RENDER_FRAMES} are needed to render.`
        : p.rendering
          ? "A render is running."
          : null;

  const moveVia = (k: number, d: -1 | 1) => {
    const j = k + d;
    if (j < 0 || j >= p.vias.length) return;
    const v = [...p.vias];
    [v[k], v[j]] = [v[j], v[k]];
    p.onVias(v);
  };

  return (
    <aside className="sidebar">
      <header className="sb-header">
        <h1>Route Hyperlapse</h1>
        <button className="btn-quiet" onClick={p.onQuit} data-testid="quit-button">
          Quit
        </button>
      </header>

      {!p.config.has_mapillary_token && (
        <div className="banner banner-warn" data-testid="setup-banner">
          <strong>Setup needed.</strong> No Mapillary token is configured. Add{" "}
          <code>MAPILLARY_TOKEN=…</code> to the <code>.env</code> file in the project folder and
          restart the app (<code>uv run hyperlapse</code>). Coverage and planning won't work
          until then.
        </div>
      )}

      {/* ------------------------------------------------ route */}
      <Section title="Route">
        <fieldset disabled={p.gpxActive} className="wp-list">
          <WaypointInput
            label="Start"
            testId="start-input"
            value={p.start}
            onChange={p.onStart}
            placeholder="Start — search, lat,lon or click map"
            marker={<span className="dot dot-start" />}
          />
          {p.vias.map((v, k) => (
            <WaypointInput
              key={k}
              label={`Via ${k + 1}`}
              testId={`via-input-${k}`}
              value={v}
              onChange={(w) =>
                p.onVias(w ? p.vias.map((x, j) => (j === k ? w : x)) : p.vias.filter((_, j) => j !== k))
              }
              marker={<span className="dot dot-via">{k + 1}</span>}
              actions={
                <div className="wp-actions">
                  <button title="Move up" onClick={() => moveVia(k, -1)} disabled={k === 0}>
                    ↑
                  </button>
                  <button
                    title="Move down"
                    onClick={() => moveVia(k, 1)}
                    disabled={k === p.vias.length - 1}
                  >
                    ↓
                  </button>
                  <button
                    title="Remove via"
                    onClick={() => p.onVias(p.vias.filter((_, j) => j !== k))}
                  >
                    ×
                  </button>
                </div>
              }
            />
          ))}
          <WaypointInput
            label="End"
            testId="end-input"
            value={p.end}
            onChange={p.onEnd}
            placeholder="End — search, lat,lon or click map"
            marker={<span className="dot dot-end" />}
          />
          {addingVia && (
            <WaypointInput
              label={`Via ${p.vias.length + 1}`}
              testId={`via-input-${p.vias.length}`}
              value={null}
              onChange={(w) => {
                if (w) p.onVias([...p.vias, w]);
                setAddingVia(false);
              }}
              placeholder="Via — search or lat,lon"
              marker={<span className="dot dot-via">{p.vias.length + 1}</span>}
              actions={
                <div className="wp-actions">
                  <button title="Cancel" onClick={() => setAddingVia(false)}>
                    ×
                  </button>
                </div>
              }
            />
          )}
          <div className="row gap">
            <button
              className="btn-small"
              data-testid="add-via-button"
              onClick={() => setAddingVia(true)}
              disabled={addingVia}
              title="Add a via point (or drag the route line on the map)"
            >
              + Via
            </button>
            <button
              className="btn-small"
              onClick={() => {
                p.onStart(null);
                p.onEnd(null);
                p.onVias([]);
              }}
              disabled={!p.start && !p.end && p.vias.length === 0}
            >
              Clear
            </button>
          </div>
        </fieldset>

        <div className="gpx-row">
          <input
            ref={fileRef}
            type="file"
            accept=".gpx,application/gpx+xml"
            data-testid="gpx-input"
            className="visually-hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) p.onUploadGpx(f);
              e.target.value = "";
            }}
          />
          <button className="btn-small" onClick={() => fileRef.current?.click()} disabled={p.gpxPending}>
            {p.gpxPending ? "Uploading…" : "Upload GPX"}
          </button>
          {p.gpxActive && (
            <>
              <span className="pill">GPX track in use</span>
              <button className="btn-small" onClick={p.onClearGpx} data-testid="clear-gpx-button">
                Clear GPX
              </button>
            </>
          )}
        </div>
        {p.gpxError && <div className="error">{p.gpxError}</div>}
        <div className="route-status">
          {p.routeLoading && (
            <span>
              <span className="spinner" /> Routing…
            </span>
          )}
          {!p.routeLoading && p.route && <span>Route: {fmtKm(p.route.distance_m)}</span>}
          {!p.routeLoading && !p.route && !p.routeError && (
            <span className="muted">Click the map to set start and end.</span>
          )}
          {p.routeError && <div className="error">Routing failed: {p.routeError}</div>}
        </div>
        <label className="check">
          <input
            type="checkbox"
            checked={p.showCoverage}
            onChange={(e) => p.onShowCoverage(e.target.checked)}
            data-testid="coverage-toggle"
          />
          Show Mapillary coverage
        </label>
      </Section>

      {/* ------------------------------------------------ settings */}
      <Section title="Settings">
        <div className="form-grid">
          <label htmlFor="set-duration">Duration</label>
          <div className="range-row">
            <input
              id="set-duration"
              type="range"
              min={30}
              max={180}
              step={5}
              value={s.duration_s}
              data-testid="duration-input"
              onChange={(e) => onSettings({ duration_s: Number(e.target.value) })}
            />
            <span className="num">{s.duration_s} s</span>
          </div>

          <label htmlFor="set-maxframes">Max frames</label>
          <input
            id="set-maxframes"
            type="number"
            min={1}
            max={2160}
            placeholder={`${12 * s.duration_s} (12 × duration)`}
            value={s.max_frames ?? ""}
            data-testid="max-frames-input"
            onChange={(e) =>
              onSettings({ max_frames: e.target.value === "" ? null : Math.max(1, Number(e.target.value)) })
            }
          />

          <label htmlFor="set-spacing">Min spacing</label>
          <div className="unit-input">
            <input
              id="set-spacing"
              type="number"
              min={1}
              step={1}
              value={s.min_spacing_m}
              data-testid="min-spacing-input"
              onChange={(e) => onSettings({ min_spacing_m: Math.max(1, Number(e.target.value) || 1) })}
            />
            <span>m</span>
          </div>

          <label htmlFor="set-fps">Output fps</label>
          <select
            id="set-fps"
            value={s.output_fps}
            data-testid="fps-select"
            onChange={(e) => onSettings({ output_fps: Number(e.target.value) })}
          >
            {(FPS_OPTIONS.includes(s.output_fps) ? FPS_OPTIONS : [...FPS_OPTIONS, s.output_fps]).map(
              (f) => (
                <option key={f} value={f}>
                  {f}
                </option>
              ),
            )}
          </select>

          <label htmlFor="set-size">Size</label>
          <select
            id="set-size"
            value={`${s.width}x${s.height}`}
            data-testid="size-select"
            onChange={(e) => {
              const [width, height] = e.target.value.split("x").map(Number);
              onSettings({ width, height });
            }}
          >
            {(SIZES.some(([w, h]) => w === s.width && h === s.height)
              ? SIZES
              : [...SIZES, [s.width, s.height] as [number, number]]
            ).map(([w, h]) => (
              <option key={`${w}x${h}`} value={`${w}x${h}`}>
                {w}×{h}
              </option>
            ))}
          </select>

          <label htmlFor="set-since">Captured since</label>
          <input
            id="set-since"
            type="date"
            value={s.captured_since ?? ""}
            data-testid="captured-since-input"
            onChange={(e) => onSettings({ captured_since: e.target.value || null })}
          />
        </div>

        <div className="checks">
          <Check
            label="Include 360°"
            checked={s.include_pano}
            onChange={(v) => onSettings({ include_pano: v })}
            testId="include-pano"
          />
          <Check
            label="Blend frames"
            checked={s.blend}
            onChange={(v) => onSettings({ blend: v })}
            testId="blend"
          />
          <Check
            label="Normalize colour"
            checked={s.normalize_colour}
            onChange={(v) => onSettings({ normalize_colour: v })}
            testId="normalize-colour"
          />
          <Check
            label="Drive on left"
            checked={s.drive_on_left}
            onChange={(v) => onSettings({ drive_on_left: v })}
            testId="drive-on-left"
          />
          <Check
            label="Clear cache on quit"
            checked={p.clearOnQuit}
            onChange={p.onClearOnQuit}
            testId="clear-cache-on-quit"
            hint="session setting, applies immediately"
          />
        </div>
      </Section>

      {/* ------------------------------------------------ stats */}
      <Section title="Stats">
        <StatsBlock {...p} />
      </Section>

      {/* ------------------------------------------------ actions */}
      <div className="actions">
        {p.stale && (
          <div className="banner banner-info" data-testid="stale-warning">
            {p.stale}
          </div>
        )}
        <button
          className="btn-primary"
          data-testid="plan-button"
          disabled={!p.canPlan}
          onClick={p.onPlan}
        >
          {p.planning ? (
            <>
              <span className="spinner" /> Planning…
            </>
          ) : p.plan ? (
            "Re-plan"
          ) : (
            "Plan"
          )}
        </button>
        {p.planError && <div className="error">Plan failed: {p.planError}</div>}
        <button
          className="btn-accent"
          data-testid="render-button"
          disabled={!!renderBlock || p.renderStarting || p.planning}
          onClick={p.onRender}
          title={renderBlock ?? "Render the MP4"}
        >
          {p.rendering ? "Rendering…" : "Render"}
        </button>
        {renderBlock && p.plan && <div className="hint">{renderBlock}</div>}
        {p.renderError && <div className="error">Render failed: {p.renderError}</div>}
      </div>

      {/* ------------------------------------------------ cache */}
      <Section title="Cache">
        <div className="row between">
          <span data-testid="cache-size" title={cacheTitle(p.cache)}>
            {fmtBytes(p.cache?.total)}
          </span>
          <button
            className="btn-small"
            data-testid="clear-cache-button"
            disabled={p.rendering || p.clearingCache}
            title={p.rendering ? "Disabled while a render is running" : "Delete cached tiles, images, routes and renders"}
            onClick={p.onClearCache}
          >
            {p.clearingCache ? "Clearing…" : "Clear cache"}
          </button>
        </div>
        {p.clearCacheError && <div className="error">{p.clearCacheError}</div>}
      </Section>
    </aside>
  );
}

function StatsBlock(p: Props) {
  const s = p.plan?.stats;
  const est = !s && p.route ? estimateBudget(p.route.distance_m, p.settings) : null;
  const dash = "–";

  return (
    <div data-testid="stats">
      <dl className="stats">
        <dt>Distance</dt>
        <dd data-testid="stat-distance">
          {s ? fmtKm(s.distance_m) : p.route ? fmtKm(p.route.distance_m) : dash}
        </dd>
        <dt>Spacing</dt>
        <dd data-testid="stat-spacing">
          {s ? `${s.spacing_m.toFixed(0)} m` : est ? `~${est.spacing.toFixed(0)} m` : dash}
        </dd>
        <dt>Planned</dt>
        <dd data-testid="stat-planned">{s ? s.planned : est ? `~${est.planned}` : dash}</dd>
        <dt>Kept</dt>
        <dd data-testid="stat-kept">{s ? s.kept : dash}</dd>
        <dt>Gaps</dt>
        <dd data-testid="stat-gaps">{s ? s.gaps : dash}</dd>
        <dt>Longest gap</dt>
        <dd data-testid="stat-longest-gap">
          {s ? (s.longest_gap_m >= 1000 ? fmtKm(s.longest_gap_m, 2) : `${s.longest_gap_m.toFixed(0)} m`) : dash}
        </dd>
        <dt>Source fps</dt>
        <dd data-testid="stat-source-fps">
          {s ? s.source_fps.toFixed(1) : est ? `~${est.source_fps.toFixed(1)}` : dash}
        </dd>
      </dl>
      {p.planLoading && <div className="muted">Loading plan…</div>}
      {!s && est && <div className="muted small">Estimates — press Plan for real coverage.</div>}
      {s?.low_coverage_warning && (
        <div className="banner banner-warn" data-testid="low-coverage-warning">
          Low coverage: only {s.kept} of {s.planned} frames have imagery (&lt; 50%).
        </div>
      )}
      {s && s.source_fps < 8 && !p.settings.blend && (
        <div className="banner banner-info" data-testid="blend-suggestion">
          Source rate is {s.source_fps.toFixed(1)} fps — turn on <em>Blend frames</em> so it
          doesn't read as a slideshow.
        </div>
      )}
      {s && !s.can_render && (
        <div className="banner banner-warn" data-testid="cannot-render">
          Fewer than {MIN_RENDER_FRAMES} frames kept — rendering is disabled.
        </div>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="sb-section">
      <h2>{title}</h2>
      {children}
    </section>
  );
}

function Check(props: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  testId: string;
  hint?: string;
}) {
  return (
    <label className="check" title={props.hint}>
      <input
        type="checkbox"
        checked={props.checked}
        data-testid={props.testId}
        onChange={(e) => props.onChange(e.target.checked)}
      />
      {props.label}
    </label>
  );
}

function cacheTitle(c: CacheInfo | null): string {
  if (!c) return "";
  return Object.entries(c.categories)
    .map(([k, v]) => `${k}: ${fmtBytes(v)}`)
    .join("\n");
}

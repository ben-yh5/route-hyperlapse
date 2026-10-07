import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, type Config, type LngLat, type RouteResponse, type Settings } from "./api";
import { settingsEqual, type Waypoint } from "./format";
import Sidebar from "./components/Sidebar";
import MapView from "./components/MapView";
import Filmstrip from "./components/Filmstrip";
import Inspector from "./components/Inspector";
import RenderPanel from "./components/RenderPanel";

const LS_PLAN = "hyperlapse.plan_id";
const LS_ROUTE = "hyperlapse.route_id";
const LS_WAYPOINTS = "hyperlapse.waypoints";

const ls = {
  get(k: string): string | null {
    try {
      return localStorage.getItem(k);
    } catch {
      return null;
    }
  },
  set(k: string, v: string | null) {
    try {
      if (v == null) localStorage.removeItem(k);
      else localStorage.setItem(k, v);
    } catch {
      /* storage unavailable */
    }
  },
};

const plain = (w: LngLat): LngLat => ({ lat: w.lat, lon: w.lon });
const routeKey = (wps: LngLat[]) => [
  "route",
  wps.map((w) => `${w.lat.toFixed(6)},${w.lon.toFixed(6)}`).join(";"),
];

export type HoverState = { i: number; source: "map" | "strip" } | null;

export default function App() {
  const configQ = useQuery({ queryKey: ["config"], queryFn: api.config, staleTime: Infinity });
  const [stopped, setStopped] = useState(false);

  if (stopped) {
    return (
      <div className="fullscreen-message">
        <h1>Server stopped</h1>
        <p>Server stopped, you can close this tab.</p>
      </div>
    );
  }
  if (configQ.isPending) return <div className="fullscreen-message">Loading…</div>;
  if (configQ.isError) {
    return (
      <div className="fullscreen-message">
        <h1>Cannot reach the server</h1>
        <p>{String(configQ.error.message)}</p>
        <button onClick={() => configQ.refetch()}>Retry</button>
      </div>
    );
  }
  return <Workspace config={configQ.data} onStopped={() => setStopped(true)} />;
}

function Workspace({ config, onStopped }: { config: Config; onStopped: () => void }) {
  const qc = useQueryClient();

  // ------------------------------------------------------------ core state
  const [settings, setSettings] = useState<Settings>(config.defaults);
  const [start, setStart] = useState<Waypoint | null>(null);
  const [end, setEnd] = useState<Waypoint | null>(null);
  const [vias, setVias] = useState<Waypoint[]>([]);
  const [gpxRoute, setGpxRoute] = useState<RouteResponse | null>(null);
  const [planId, setPlanId] = useState<string | null>(null);
  const [hover, setHover] = useState<HoverState>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [showCoverage, setShowCoverage] = useState(true);
  const [jobId, setJobId] = useState<string | null>(null);
  const [renderRunning, setRenderRunning] = useState(false);
  const [renderPanelOpen, setRenderPanelOpen] = useState(false);
  const [restoring, setRestoring] = useState(true);

  const waypoints = useMemo<Waypoint[] | null>(
    () => (start && end ? [start, ...vias, end] : null),
    [start, vias, end],
  );

  // ------------------------------------------------------------ restore after reload
  const restoredOnce = useRef(false);
  useEffect(() => {
    if (restoredOnce.current) return;
    restoredOnce.current = true;

    const storedWps = parseStoredWaypoints(ls.get(LS_WAYPOINTS));
    const applyWaypoints = (wps: LngLat[]) => {
      const same = (a: LngLat, b: LngLat) =>
        Math.abs(a.lat - b.lat) < 1e-7 && Math.abs(a.lon - b.lon) < 1e-7;
      const labelled: Waypoint[] = wps.map((w, k) => {
        const byIndex = storedWps?.length === wps.length ? storedWps[k] : undefined;
        const match = byIndex && same(byIndex, w) ? byIndex : storedWps?.find((s) => same(s, w));
        return match?.label ? { ...plain(w), label: match.label } : plain(w);
      });
      setStart(labelled[0]);
      setEnd(labelled[labelled.length - 1]);
      setVias(labelled.slice(1, -1));
    };

    (async () => {
      const pid = ls.get(LS_PLAN);
      const rid = ls.get(LS_ROUTE);
      try {
        if (pid) {
          try {
            const p = await api.getPlan(pid);
            qc.setQueryData(["plan", pid], p);
            const s = { ...config.defaults, ...p.settings };
            setSettings(s);
            const r = await api.getRoute(p.route_id).catch(() => null);
            if (p.waypoints && p.waypoints.length >= 2) {
              // Seed the route cache so restoring doesn't re-route (and change route_id).
              if (r) qc.setQueryData(routeKey(p.waypoints.map(plain)), r);
              applyWaypoints(p.waypoints);
            } else if (r) {
              setGpxRoute(r);
            }
            setPlanId(pid);
            return;
          } catch (e) {
            if (e instanceof ApiError && e.status === 404) {
              ls.set(LS_PLAN, null);
              ls.set(LS_ROUTE, null);
            } else {
              console.warn("plan restore failed", e);
            }
          }
        }
        if (storedWps && storedWps.length >= 2) {
          applyWaypoints(storedWps);
        } else if (rid && ls.get(LS_ROUTE)) {
          try {
            setGpxRoute(await api.getRoute(rid));
          } catch (e) {
            if (e instanceof ApiError && e.status === 404) ls.set(LS_ROUTE, null);
          }
        }
      } finally {
        setRestoring(false);
      }
    })();
  }, [qc, config.defaults]);

  // ------------------------------------------------------------ routing
  const routeQ = useQuery({
    queryKey: routeKey((waypoints ?? []).map(plain)),
    queryFn: () => api.route(waypoints!.map(plain)),
    enabled: !restoring && !gpxRoute && !!waypoints,
    staleTime: Infinity,
    retry: false,
    placeholderData: keepPreviousData,
  });
  const route: RouteResponse | null = gpxRoute ?? (waypoints ? routeQ.data ?? null : null);

  const gpxMut = useMutation({
    mutationFn: (file: File) => api.routeGpx(file),
    onSuccess: (r) => setGpxRoute(r),
  });

  // ------------------------------------------------------------ plan + frames
  const planQ = useQuery({
    queryKey: ["plan", planId],
    queryFn: () => api.getPlan(planId!),
    enabled: !!planId,
    staleTime: Infinity,
    retry: false,
  });
  const plan = planId ? planQ.data : undefined;

  useEffect(() => {
    if (planQ.error instanceof ApiError && planQ.error.status === 404) {
      setPlanId(null);
      ls.set(LS_PLAN, null);
    }
  }, [planQ.error]);

  const framesQ = useQuery({
    queryKey: ["frames", planId],
    queryFn: () => api.allFrames(planId!),
    enabled: !!planId && !!plan,
    staleTime: Infinity,
  });
  const frames = planId ? framesQ.data : undefined;

  const planMut = useMutation({
    mutationFn: () => {
      if (!route) throw new Error("No route");
      // Re-plan in place (keeping overrides) only when the route is unchanged.
      const reuse = plan && plan.route_id === route.route_id ? plan.plan_id : undefined;
      return api.plan(route.route_id, settings, reuse);
    },
    onSuccess: (p) => {
      qc.setQueryData(["plan", p.plan_id], p);
      qc.invalidateQueries({ queryKey: ["frames", p.plan_id] });
      qc.invalidateQueries({ queryKey: ["candidates", p.plan_id] });
      qc.invalidateQueries({ queryKey: ["cache"] });
      setPlanId(p.plan_id);
      setSelected((s) => (s != null && s < p.stats.planned ? s : null));
    },
  });

  const stale = useMemo(() => {
    if (!plan) return null;
    if (route && plan.route_id !== route.route_id) return "route changed — re-plan";
    if (!settingsEqual(settings, { ...config.defaults, ...plan.settings }))
      return "settings changed — re-plan";
    return null;
  }, [plan, route, settings, config.defaults]);

  // ------------------------------------------------------------ persistence
  useEffect(() => {
    if (restoring) return;
    ls.set(LS_PLAN, planId);
  }, [planId, restoring]);
  useEffect(() => {
    if (restoring || !route) return;
    ls.set(LS_ROUTE, route.route_id);
  }, [route, restoring]);
  useEffect(() => {
    if (restoring) return;
    ls.set(LS_WAYPOINTS, JSON.stringify([start, ...vias, end].filter(Boolean)));
  }, [start, vias, end, restoring]);

  // ------------------------------------------------------------ cache / status / render
  // Status only matters while a render runs (Quit fetches it fresh). Cache size is refreshed after
  // plan / swap / render / clear, plus a slow refresh to catch thumbnails being cached while browsing.
  const statusQ = useQuery({
    queryKey: ["status"],
    queryFn: api.status,
    refetchInterval: (q) => (renderRunning || q.state.data?.render_running ? 5000 : false),
  });
  const cacheQ = useQuery({ queryKey: ["cache"], queryFn: api.cache, refetchInterval: 30_000 });
  const clearCacheMut = useMutation({
    mutationFn: api.clearCache,
    onSuccess: (c) => qc.setQueryData(["cache"], c),
  });
  const [clearOnQuit, setClearOnQuit] = useState(config.clear_cache_on_quit);
  const sessionMut = useMutation({
    mutationFn: api.setSession,
    onSuccess: (r) => setClearOnQuit(r.clear_cache_on_quit),
  });

  const renderMut = useMutation({
    mutationFn: () => api.render(planId!),
    onSuccess: ({ job_id }) => {
      setJobId(job_id);
      setRenderRunning(true);
      setRenderPanelOpen(true);
      qc.invalidateQueries({ queryKey: ["status"] });
    },
  });
  const onRenderFinished = useCallback(() => {
    setRenderRunning(false);
    qc.invalidateQueries({ queryKey: ["status"] });
    qc.invalidateQueries({ queryKey: ["cache"] });
  }, [qc]);

  const rendering = renderRunning || !!statusQ.data?.render_running;

  const quit = useCallback(async () => {
    let st = null;
    try {
      st = await api.status();
    } catch {
      /* ignore */
    }
    if (
      st?.undownloaded_render &&
      !window.confirm(
        "The finished render has not been downloaded. Quitting will delete it. Quit anyway?",
      )
    )
      return;
    if (st?.render_running && !window.confirm("A render is still running. Quit anyway?")) return;
    try {
      await api.quit();
    } catch {
      /* the server may drop the connection while shutting down */
    }
    onStopped();
  }, [onStopped]);

  // ------------------------------------------------------------ map / waypoint callbacks
  const onMapClick = useCallback(
    (ll: LngLat) => {
      if (gpxRoute) return;
      if (!start) setStart(plain(ll));
      else setEnd(plain(ll));
    },
    [gpxRoute, start],
  );
  const viaCount = useRef(0);
  viaCount.current = vias.length;
  const onWaypointMove = useCallback((idx: number, ll: LngLat) => {
    // idx is the position in [start, ...vias, end]
    if (idx === 0) setStart(plain(ll));
    else if (idx === viaCount.current + 1) setEnd(plain(ll));
    else setVias((v) => v.map((w, k) => (k === idx - 1 ? plain(ll) : w)));
  }, []);
  const onRemoveVia = useCallback((k: number) => setVias((v) => v.filter((_, j) => j !== k)), []);
  const onInsertVia = useCallback(
    (pos: number, ll: LngLat) =>
      setVias((v) => {
        const p = Math.max(0, Math.min(pos, v.length));
        return [...v.slice(0, p), plain(ll), ...v.slice(p)];
      }),
    [],
  );
  const onMapHover = useCallback(
    (i: number | null) => setHover(i == null ? null : { i, source: "map" }),
    [],
  );
  const onStripHover = useCallback(
    (i: number | null) => setHover(i == null ? null : { i, source: "strip" }),
    [],
  );
  const onSelect = useCallback((i: number | null) => setSelected(i), []);

  const selectedFrame =
    selected != null && frames ? frames.find((f) => f.i === selected) ?? frames[selected] : undefined;

  return (
    <div className="app">
      <Sidebar
        config={config}
        settings={settings}
        onSettings={(patch) => setSettings((s) => ({ ...s, ...patch }))}
        start={start}
        end={end}
        vias={vias}
        onStart={setStart}
        onEnd={setEnd}
        onVias={setVias}
        gpxActive={!!gpxRoute}
        gpxPending={gpxMut.isPending}
        gpxError={gpxMut.error?.message ?? null}
        onUploadGpx={(f) => gpxMut.mutate(f)}
        onClearGpx={() => {
          setGpxRoute(null);
          gpxMut.reset();
        }}
        route={route}
        routeLoading={routeQ.isFetching && !gpxRoute}
        routeError={!gpxRoute && waypoints && routeQ.error ? routeQ.error.message : null}
        plan={plan ?? null}
        planLoading={planQ.isLoading}
        stale={stale}
        planning={planMut.isPending}
        planError={planMut.error?.message ?? null}
        canPlan={!!route && !restoring && !planMut.isPending}
        onPlan={() => planMut.mutate()}
        rendering={rendering}
        renderStarting={renderMut.isPending}
        renderError={renderMut.error?.message ?? null}
        onRender={() => renderMut.mutate()}
        cache={cacheQ.data ?? null}
        clearingCache={clearCacheMut.isPending}
        clearCacheError={clearCacheMut.error?.message ?? null}
        onClearCache={() => clearCacheMut.mutate()}
        clearOnQuit={clearOnQuit}
        onClearOnQuit={(v) => sessionMut.mutate(v)}
        showCoverage={showCoverage}
        onShowCoverage={setShowCoverage}
        onQuit={quit}
      />
      <main className="main">
        <div className="map-area">
          <MapView
            styleUrl={config.style_url}
            route={route}
            start={start}
            vias={vias}
            end={end}
            editable={!gpxRoute}
            samples={plan?.samples ?? null}
            hovered={hover?.i ?? null}
            selected={selected}
            showCoverage={showCoverage}
            onMapClick={onMapClick}
            onWaypointMove={onWaypointMove}
            onRemoveVia={onRemoveVia}
            onInsertVia={onInsertVia}
            onHover={onMapHover}
            onSelect={onSelect}
          />
          {planMut.isPending && (
            <div className="map-busy">
              <span className="spinner" /> Planning… this can take a while on a cold cache
            </div>
          )}
          {plan && selectedFrame && (
            <Inspector
              planId={plan.plan_id}
              frame={selectedFrame}
              total={frames?.length ?? 0}
              onClose={() => setSelected(null)}
              onNavigate={setSelected}
            />
          )}
          {jobId && (
            <RenderPanel
              key={jobId}
              hidden={!renderPanelOpen}
              jobId={jobId}
              onFinished={onRenderFinished}
              onClose={() => setRenderPanelOpen(false)}
            />
          )}
          {!renderPanelOpen && jobId && (
            <button className="render-reopen" onClick={() => setRenderPanelOpen(true)}>
              Show render
            </button>
          )}
        </div>
        <Filmstrip
          frames={frames}
          loading={!!planId && (framesQ.isLoading || planQ.isLoading)}
          hasPlan={!!plan}
          hover={hover}
          selected={selected}
          onHover={onStripHover}
          onSelect={onSelect}
        />
      </main>
    </div>
  );
}

function parseStoredWaypoints(raw: string | null): Waypoint[] | null {
  if (!raw) return null;
  try {
    const v = JSON.parse(raw);
    if (!Array.isArray(v)) return null;
    return v.filter(
      (w): w is Waypoint => w && typeof w.lat === "number" && typeof w.lon === "number",
    );
  } catch {
    return null;
  }
}

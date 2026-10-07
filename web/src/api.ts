// Typed client for the FastAPI backend (all endpoints under /api).
import type { Feature, FeatureCollection, LineString, Point } from "geojson";

// ---------------------------------------------------------------- types

export type LngLat = { lat: number; lon: number };

export type Settings = {
  duration_s: number;
  max_frames: number | null;
  min_spacing_m: number;
  output_fps: number;
  width: number;
  height: number;
  captured_since: string | null;
  include_pano: boolean;
  blend: boolean;
  normalize_colour: boolean;
  drive_on_left: boolean;
};

export type Stats = {
  distance_m: number;
  spacing_m: number;
  planned: number;
  kept: number;
  gaps: number;
  longest_gap_m: number;
  source_fps: number;
  low_coverage_warning: boolean;
  can_render: boolean;
};

export type SampleStatus = "chosen" | "gap" | "overridden" | "excluded";

export type Frame = {
  i: number;
  status: SampleStatus;
  locked: boolean;
  image_id: string | null;
  lat: number;
  lon: number;
  image_lat: number | null;
  image_lon: number | null;
  distance_m: number;
  bearing: number;
  captured_at: number | null;
  is_pano: boolean | null;
  thumb_url: string | null;
};

export type Candidate = {
  image_id: string;
  lat: number;
  lon: number;
  distance_m: number;
  heading_error: number;
  score: number;
  captured_at: number;
  is_pano: boolean;
  sequence_id: string;
  thumb_url: string;
  chosen: boolean;
};

export type SampleProps = {
  i: number;
  status: SampleStatus;
  locked: boolean;
  image_id: string | null;
};

export type SamplesFC = FeatureCollection<Point, SampleProps>;

export type Config = {
  has_mapillary_token: boolean;
  style_url: string;
  defaults: Settings;
  clear_cache_on_quit: boolean;
};

export type GeocodeResult = { name: string; lat: number; lon: number };

export type RouteResponse = {
  route_id: string;
  geojson: Feature<LineString>;
  distance_m: number;
};

export type PlanResponse = {
  plan_id: string;
  route_id: string;
  settings: Settings;
  waypoints: LngLat[] | null;
  samples: SamplesFC;
  stats: Stats;
};

export type FramesPage = { total: number; frames: Frame[] };

export type SampleAction =
  | { action: "swap"; image_id: string }
  | { action: "exclude" }
  | { action: "lock" }
  | { action: "unlock" }
  | { action: "reset" };

export type PatchResponse = { frame: Frame; stats: Stats; samples: SamplesFC };

export type CacheInfo = { total: number; categories: Record<string, number> };

export type Status = { render_running: boolean; undownloaded_render: boolean };

export type ProgressEvent = {
  stage: string;
  progress: number;
  overall: number;
  message: string;
};

export type DoneEvent = {
  video_url: string;
  manifest_url: string;
  credits_url: string;
};

// ---------------------------------------------------------------- fetch helpers

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function errorMessage(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") return body.detail;
    if (body?.detail) return JSON.stringify(body.detail);
    if (typeof body?.message === "string") return body.message;
  } catch {
    /* not JSON */
  }
  return `${res.status} ${res.statusText}`;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, headers: {} };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  const res = await fetch(`/api${path}`, init);
  if (!res.ok) throw new ApiError(res.status, await errorMessage(res));
  return (await res.json()) as T;
}

const get = <T>(path: string) => request<T>("GET", path);
const post = <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {});
const patch = <T>(path: string, body: unknown) => request<T>("PATCH", path, body);

const enc = encodeURIComponent;

// ---------------------------------------------------------------- endpoints

export const api = {
  config: () => get<Config>("/config"),

  geocode: (query: string) => post<{ results: GeocodeResult[] }>("/geocode", { query }),

  route: (waypoints: LngLat[]) => post<RouteResponse>("/route", { waypoints }),

  routeGpx: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return post<RouteResponse>("/route/gpx", fd);
  },

  getRoute: (routeId: string) => get<RouteResponse>(`/route/${enc(routeId)}`),

  plan: (route_id: string, settings: Settings, plan_id?: string) =>
    post<PlanResponse>("/plan", plan_id ? { route_id, settings, plan_id } : { route_id, settings }),

  getPlan: (planId: string) => get<PlanResponse>(`/plan/${enc(planId)}`),

  framesPage: (planId: string, offset = 0, limit = 500) =>
    get<FramesPage>(`/plan/${enc(planId)}/frames?offset=${offset}&limit=${limit}`),

  /** Fetch every page of frames for a plan. */
  allFrames: async (planId: string): Promise<Frame[]> => {
    const limit = 500;
    const first = await api.framesPage(planId, 0, limit);
    const frames = [...first.frames];
    while (frames.length < first.total) {
      const page = await api.framesPage(planId, frames.length, limit);
      if (page.frames.length === 0) break;
      frames.push(...page.frames);
    }
    return frames;
  },

  candidates: (planId: string, i: number) =>
    get<{ candidates: Candidate[] }>(`/plan/${enc(planId)}/samples/${i}/candidates`),

  patchSample: (planId: string, i: number, action: SampleAction) =>
    patch<PatchResponse>(`/plan/${enc(planId)}/samples/${i}`, action),

  render: (planId: string) => post<{ job_id: string }>(`/plan/${enc(planId)}/render`),

  jobEventsUrl: (jobId: string) => `/api/jobs/${enc(jobId)}/events`,

  jobVideoUrl: (jobId: string) => `/api/jobs/${enc(jobId)}/video`,

  tilesUrl: () => `${window.location.origin}/api/tiles/{z}/{x}/{y}`,

  imageUrl: (imageId: string, size: 256 | 1024 | 2048 = 1024) =>
    `/api/image/${enc(imageId)}?size=${size}`,

  cache: () => get<CacheInfo>("/cache"),

  clearCache: () => post<CacheInfo>("/cache/clear"),

  status: () => get<Status>("/status"),

  setSession: (clear_cache_on_quit: boolean) =>
    post<{ clear_cache_on_quit: boolean }>("/settings/session", { clear_cache_on_quit }),

  quit: () => post<{ ok: boolean }>("/quit"),
};

/** Append ?download=1 (or &download=1) to a URL. */
export function downloadUrl(url: string): string {
  return url + (url.includes("?") ? "&" : "?") + "download=1";
}

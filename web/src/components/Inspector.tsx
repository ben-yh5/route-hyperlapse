import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Frame, type PatchResponse, type PlanResponse, type SampleAction } from "../api";
import { fmtDate, haversine } from "../format";

type Props = {
  planId: string;
  frame: Frame;
  total: number;
  onClose: () => void;
  onNavigate: (i: number) => void;
};

export default function Inspector({ planId, frame, total, onClose, onNavigate }: Props) {
  const qc = useQueryClient();
  const [pending, setPending] = useState<string | null>(null);

  useEffect(() => setPending(null), [frame.i]);

  const candQ = useQuery({
    queryKey: ["candidates", planId, frame.i],
    queryFn: () => api.candidates(planId, frame.i),
    staleTime: 60_000,
  });
  const candidates = candQ.data?.candidates ?? [];

  const mut = useMutation({
    mutationFn: (action: SampleAction) => api.patchSample(planId, frame.i, action),
    onSuccess: (res: PatchResponse) => {
      qc.setQueryData<Frame[]>(["frames", planId], (old) =>
        old?.map((f) => (f.i === res.frame.i ? res.frame : f)),
      );
      qc.setQueryData<PlanResponse>(["plan", planId], (old) =>
        old ? { ...old, stats: res.stats, samples: res.samples } : old,
      );
      qc.invalidateQueries({ queryKey: ["candidates", planId, frame.i] });
      qc.invalidateQueries({ queryKey: ["cache"] });
      setPending(null);
    },
  });

  // Arrow keys move the selection; Escape closes.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "SELECT" || t.tagName === "TEXTAREA" || t.isContentEditable))
        return;
      if (e.key === "ArrowLeft" && frame.i > 0) {
        e.preventDefault();
        onNavigate(frame.i - 1);
      } else if (e.key === "ArrowRight" && frame.i < total - 1) {
        e.preventDefault();
        onNavigate(frame.i + 1);
      } else if (e.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [frame.i, total, onNavigate, onClose]);

  const previewId = pending ?? frame.image_id;
  const pendingCand = pending ? candidates.find((c) => c.image_id === pending) : undefined;
  const offRoute =
    frame.image_lat != null && frame.image_lon != null
      ? haversine({ lat: frame.lat, lon: frame.lon }, { lat: frame.image_lat, lon: frame.image_lon })
      : null;
  const hasOverride = frame.status === "overridden" || frame.status === "excluded" || frame.locked;

  return (
    <aside className="inspector" data-testid="inspector" aria-label={`Frame ${frame.i}`}>
      <header className="insp-header">
        <div>
          <strong>Frame #{frame.i}</strong>
          <span className="muted">
            {" "}
            · {(frame.distance_m / 1000).toFixed(2)} km · {frame.i + 1}/{total}
          </span>
        </div>
        <div className="insp-nav">
          <button onClick={() => onNavigate(frame.i - 1)} disabled={frame.i <= 0} title="Previous (←)">
            ‹
          </button>
          <button onClick={() => onNavigate(frame.i + 1)} disabled={frame.i >= total - 1} title="Next (→)">
            ›
          </button>
          <button onClick={onClose} title="Close (Esc)" data-testid="inspector-close">
            ×
          </button>
        </div>
      </header>

      <div className="insp-image">
        {previewId ? (
          <img key={previewId} src={api.imageUrl(previewId, 1024)} alt={`Frame ${frame.i}`} />
        ) : (
          <div className="insp-placeholder">{frame.status === "excluded" ? "Excluded" : "No imagery (gap)"}</div>
        )}
        {pending && <span className="insp-preview-tag">Preview — press Swap to use</span>}
        <span className={`status-tag st-${frame.status}`}>{frame.status}</span>
      </div>

      <dl className="insp-meta">
        <dt>Captured</dt>
        <dd>{fmtDate(pendingCand?.captured_at ?? frame.captured_at)}</dd>
        <dt>360°</dt>
        <dd>{(pendingCand?.is_pano ?? frame.is_pano) == null ? "–" : (pendingCand?.is_pano ?? frame.is_pano) ? "yes" : "no"}</dd>
        <dt>From route</dt>
        <dd>
          {pendingCand
            ? `${pendingCand.distance_m.toFixed(1)} m`
            : offRoute != null
              ? `${offRoute.toFixed(1)} m`
              : "–"}
        </dd>
        <dt>Bearing</dt>
        <dd>{frame.bearing.toFixed(0)}°</dd>
        <dt>Image</dt>
        <dd className="mono">{previewId ?? "–"}</dd>
        <dt>Locked</dt>
        <dd>{frame.locked ? "yes" : "no"}</dd>
      </dl>

      <div className="insp-cands">
        <div className="insp-subtitle">
          Alternates {candQ.isFetching && <span className="spinner" />}
        </div>
        {candQ.isError && <div className="error">{candQ.error.message}</div>}
        {!candQ.isPending && candidates.length === 0 && !candQ.isError && (
          <div className="muted small">No candidates near this sample.</div>
        )}
        <div className="cand-grid">
          {candidates.map((c, k) => {
            const isCurrent = c.chosen || c.image_id === frame.image_id;
            return (
              <button
                key={c.image_id}
                className={`cand${isCurrent ? " current" : ""}${pending === c.image_id ? " pending" : ""}`}
                data-testid={`candidate-${k}`}
                onClick={() => setPending(isCurrent ? null : c.image_id)}
                title={`score ${c.score.toFixed(2)} · ${c.distance_m.toFixed(0)} m · heading ±${c.heading_error.toFixed(0)}° · ${fmtDate(c.captured_at)}${c.is_pano ? " · 360°" : ""}`}
              >
                <img src={c.thumb_url} loading="lazy" alt="" />
                <span className="cand-meta">
                  {c.distance_m.toFixed(0)} m · {c.heading_error.toFixed(0)}°
                </span>
                {isCurrent && <span className="cand-current">current</span>}
              </button>
            );
          })}
        </div>
      </div>

      <div className="insp-actions">
        <button
          className="btn-primary"
          data-testid="swap-button"
          disabled={!pending || mut.isPending}
          onClick={() => pending && mut.mutate({ action: "swap", image_id: pending })}
          title={pending ? "Use the selected alternate" : "Select an alternate first"}
        >
          Swap
        </button>
        <button
          data-testid="exclude-button"
          disabled={frame.status === "excluded" || mut.isPending}
          onClick={() => mut.mutate({ action: "exclude" })}
        >
          Exclude
        </button>
        <button
          data-testid="lock-button"
          disabled={mut.isPending || (!frame.locked && !frame.image_id)}
          onClick={() => mut.mutate({ action: frame.locked ? "unlock" : "lock" })}
          aria-pressed={frame.locked}
        >
          {frame.locked ? "Unlock" : "Lock"}
        </button>
        <button
          data-testid="reset-button"
          disabled={!hasOverride || mut.isPending}
          onClick={() => mut.mutate({ action: "reset" })}
        >
          Reset
        </button>
      </div>
      {mut.isError && <div className="error">{mut.error.message}</div>}
    </aside>
  );
}

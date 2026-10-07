import { useEffect, useRef, useState } from "react";
import { api, downloadUrl, type DoneEvent, type ProgressEvent } from "../api";

type Props = {
  jobId: string;
  onFinished: () => void;
  onClose: () => void;
  /** Hidden panels stay mounted so the SSE stream keeps running. */
  hidden: boolean;
};

type State =
  | { kind: "running"; progress: ProgressEvent | null }
  | { kind: "done"; result: DoneEvent }
  | { kind: "error"; message: string };

export default function RenderPanel({ jobId, onFinished, onClose, hidden }: Props) {
  const [state, setState] = useState<State>({ kind: "running", progress: null });
  const onFinishedRef = useRef(onFinished);
  onFinishedRef.current = onFinished;

  useEffect(() => {
    setState({ kind: "running", progress: null });
    const update = setState;
    let finished = false;
    const es = new EventSource(api.jobEventsUrl(jobId));

    es.addEventListener("progress", (e) => {
      update({ kind: "running", progress: JSON.parse((e as MessageEvent).data) as ProgressEvent });
    });
    es.addEventListener("done", (e) => {
      finished = true;
      es.close();
      update({ kind: "done", result: JSON.parse((e as MessageEvent).data) as DoneEvent });
      onFinishedRef.current();
    });
    // Named "error" events from the server carry data; bare ones are connection errors.
    es.addEventListener("error", (e) => {
      const data = (e as MessageEvent).data;
      if (typeof data === "string" && data) {
        finished = true;
        es.close();
        let message = data;
        try {
          message = (JSON.parse(data) as { message: string }).message ?? data;
        } catch {
          /* plain text */
        }
        update({ kind: "error", message });
        onFinishedRef.current();
      } else if (!finished && es.readyState === EventSource.CLOSED) {
        update({ kind: "error", message: "Lost connection to the render job." });
        onFinishedRef.current();
      }
      // Otherwise EventSource reconnects on its own.
    });
    return () => es.close();
  }, [jobId]);

  const prog = state.kind === "running" ? state.progress : null;
  const overall = state.kind === "done" ? 1 : prog?.overall ?? 0;
  const pct = Math.round(overall * 100);

  return (
    <section className="render-panel" data-testid="render-panel" hidden={hidden}>
      <header>
        <strong>Render</strong>
        <button className="btn-quiet" onClick={onClose} title="Hide">
          ×
        </button>
      </header>

      {state.kind !== "error" && (
        <div className="progress-wrap">
          <div
            className="progress"
            data-testid="render-progress"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={pct}
            data-state={state.kind}
          >
            <div className="progress-bar" style={{ width: `${pct}%` }} />
          </div>
          <div className="progress-label">
            <span>
              {state.kind === "done" ? "Done" : prog ? prog.stage : "Starting…"}
              {prog && state.kind === "running" && ` · ${Math.round(prog.progress * 100)}%`}
            </span>
            <span className="num">{pct}%</span>
          </div>
          {prog?.message && state.kind === "running" && <div className="muted small">{prog.message}</div>}
        </div>
      )}

      {state.kind === "error" && (
        <div className="error" data-testid="render-error">
          Render failed: {state.message}
        </div>
      )}

      {state.kind === "done" && (
        <div className="render-result">
          <video controls src={state.result.video_url} data-testid="render-video" preload="metadata" />
          <div className="render-links">
            <a
              className="btn-accent"
              href={downloadUrl(state.result.video_url)}
              download
              data-testid="download-link"
            >
              Download MP4
            </a>
            <a href={state.result.manifest_url} target="_blank" rel="noreferrer" data-testid="manifest-link">
              manifest.json
            </a>
            <a href={state.result.credits_url} target="_blank" rel="noreferrer" data-testid="credits-link">
              credits.txt
            </a>
          </div>
        </div>
      )}
    </section>
  );
}

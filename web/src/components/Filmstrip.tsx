import { memo, useEffect, useMemo, useRef } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import type { Frame } from "../api";
import type { HoverState } from "../App";

const ITEM_W = 160;
const MAX_ITEMS = 50;

/** Up to MAX_ITEMS frames equally spaced along the route; the selected frame replaces its nearest slot. */
function pickFrames(frames: Frame[], selected: number | null): Frame[] {
  const n = frames.length;
  if (n <= MAX_ITEMS) return frames;
  const step = (n - 1) / (MAX_ITEMS - 1);
  const idx = Array.from({ length: MAX_ITEMS }, (_, k) => Math.round(k * step));
  if (selected != null && selected < n && !idx.includes(selected)) idx[slotFor(selected, n)] = selected;
  return idx.map((i) => frames[i]);
}

/** Strip slot nearest to frame index i. */
function slotFor(i: number, n: number): number {
  return n <= MAX_ITEMS ? i : Math.round((i * (MAX_ITEMS - 1)) / (n - 1));
}

type Props = {
  frames: Frame[] | undefined;
  loading: boolean;
  hasPlan: boolean;
  hover: HoverState;
  selected: number | null;
  onHover: (i: number | null) => void;
  onSelect: (i: number) => void;
};

export default function Filmstrip({ frames, loading, hasPlan, hover, selected, onHover, onSelect }: Props) {
  const parentRef = useRef<HTMLDivElement>(null);
  const total = frames?.length ?? 0;
  const shown = useMemo(() => (frames ? pickFrames(frames, selected) : []), [frames, selected]);
  const count = shown.length;

  const virtualizer = useVirtualizer({
    horizontal: true,
    count,
    getScrollElement: () => parentRef.current,
    estimateSize: () => ITEM_W,
    overscan: 6,
  });

  // Map hover -> scroll the strip to the hovered frame.
  useEffect(() => {
    if (hover?.source === "map" && hover.i < total) {
      virtualizer.scrollToIndex(slotFor(hover.i, total), { align: "center" });
    }
  }, [hover, total, virtualizer]);

  // Selection (map click, arrow keys) -> make sure it is visible.
  useEffect(() => {
    if (selected != null && selected < total) virtualizer.scrollToIndex(slotFor(selected, total), { align: "auto" });
  }, [selected, total, virtualizer]);

  // Vertical wheel scrolls the strip horizontally.
  useEffect(() => {
    const el = parentRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) {
        el.scrollLeft += e.deltaY;
        e.preventDefault();
      }
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [count > 0]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!frames || count === 0) {
    return (
      <div className="filmstrip filmstrip-empty" data-testid="filmstrip">
        {loading ? (
          <>
            <span className="spinner" /> Loading frames…
          </>
        ) : hasPlan ? (
          "No frames in this plan."
        ) : (
          "Plan the route to see its frames here."
        )}
      </div>
    );
  }

  // A hovered map marker that isn't in the strip highlights its nearest slot.
  const hoveredSlot = hover && hover.i < total ? slotFor(hover.i, total) : null;
  return (
    <div className="filmstrip" data-testid="filmstrip">
      <div ref={parentRef} className="filmstrip-scroll" onMouseLeave={() => onHover(null)}>
        <div className="filmstrip-inner" style={{ width: virtualizer.getTotalSize() }}>
          {virtualizer.getVirtualItems().map((item) => {
            const f = shown[item.index];
            return (
              <FilmItem
                key={item.key}
                frame={f}
                left={item.start}
                isHovered={hover?.i === f.i || (hover?.source === "map" && hoveredSlot === item.index)}
                isSelected={selected === f.i}
                onHover={onHover}
                onSelect={onSelect}
              />
            );
          })}
        </div>
      </div>
    </div>
  );
}

type ItemProps = {
  frame: Frame;
  left: number;
  isHovered: boolean;
  isSelected: boolean;
  onHover: (i: number | null) => void;
  onSelect: (i: number) => void;
};

const FilmItem = memo(function FilmItem({ frame: f, left, isHovered, isSelected, onHover, onSelect }: ItemProps) {
  const hasImage = (f.status === "chosen" || f.status === "overridden") && !!f.thumb_url;
  const cls = [
    "film-item",
    `st-${f.status}`,
    isHovered && "hovered",
    isSelected && "selected",
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <div
      className={cls}
      style={{ transform: `translateX(${left}px)`, width: ITEM_W }}
      data-testid={`filmstrip-item-${f.i}`}
      data-status={f.status}
      onMouseEnter={() => onHover(f.i)}
      onClick={() => onSelect(f.i)}
      role="button"
      aria-pressed={isSelected}
      title={`Frame ${f.i} · ${(f.distance_m / 1000).toFixed(2)} km · ${f.status}${f.locked ? " · locked" : ""}`}
    >
      <div className="film-thumb">
        {hasImage ? (
          <img src={f.thumb_url!} loading="lazy" decoding="async" alt="" draggable={false} />
        ) : (
          <div className="film-placeholder">{f.status === "excluded" ? "Excluded" : "Gap"}</div>
        )}
        <div className="film-badges">
          {f.status === "overridden" && <span className="badge badge-override">swap</span>}
          {f.locked && <span className="badge badge-lock">lock</span>}
          {f.is_pano && <span className="badge">360</span>}
        </div>
      </div>
      <div className="film-label">
        <span>#{f.i}</span>
        <span>{(f.distance_m / 1000).toFixed(2)} km</span>
      </div>
    </div>
  );
});

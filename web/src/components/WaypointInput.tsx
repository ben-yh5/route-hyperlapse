import { useEffect, useId, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type GeocodeResult } from "../api";
import { parseLatLon, waypointLabel, type Waypoint } from "../format";

type Props = {
  label: string;
  testId: string;
  value: Waypoint | null;
  onChange: (w: Waypoint | null) => void;
  placeholder?: string;
  marker?: ReactNode;
  actions?: ReactNode;
};

/** Text input with debounced geocode autocomplete. Also accepts "lat,lon". */
export default function WaypointInput({
  label,
  testId,
  value,
  onChange,
  placeholder,
  marker,
  actions,
}: Props) {
  const [text, setText] = useState(waypointLabel(value));
  const [debounced, setDebounced] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const listId = useId();

  useEffect(() => setText(waypointLabel(value)), [value]);

  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(text.trim()), 400);
    return () => window.clearTimeout(t);
  }, [text]);

  const dirty = text.trim() !== "" && text !== waypointLabel(value);
  const q = useQuery({
    queryKey: ["geocode", debounced],
    queryFn: () => api.geocode(debounced),
    enabled: open && dirty && debounced.length >= 2,
    staleTime: Infinity,
    retry: false,
  });
  const results: GeocodeResult[] = dirty && debounced === text.trim() ? q.data?.results ?? [] : [];

  const pick = (r: GeocodeResult) => {
    onChange({ lat: r.lat, lon: r.lon, label: r.name });
    setOpen(false);
  };

  const commitEnter = () => {
    if (results.length > 0) {
      pick(results[Math.min(active, results.length - 1)]);
      return;
    }
    const ll = parseLatLon(text);
    if (ll) {
      onChange(ll);
      setOpen(false);
    }
  };

  return (
    <div className="wp-row">
      <div className="wp-marker-slot">{marker}</div>
      <div className="wp-field">
        <input
          type="text"
          aria-label={label}
          data-testid={testId}
          placeholder={placeholder ?? label}
          value={text}
          role="combobox"
          aria-expanded={open && results.length > 0}
          aria-controls={listId}
          autoComplete="off"
          spellCheck={false}
          onChange={(e) => {
            setText(e.target.value);
            setOpen(true);
            setActive(0);
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => {
            setOpen(false);
            if (!text.trim() && value) onChange(null);
          }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setActive((a) => Math.min(a + 1, results.length - 1));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setActive((a) => Math.max(a - 1, 0));
            } else if (e.key === "Enter") {
              e.preventDefault();
              commitEnter();
            } else if (e.key === "Escape") {
              setText(waypointLabel(value));
              setOpen(false);
            }
          }}
        />
        {open && dirty && (
          <ul className="ac-list" id={listId} role="listbox">
            {q.isFetching && results.length === 0 && <li className="ac-empty">Searching…</li>}
            {!q.isFetching && q.isError && <li className="ac-empty">Search failed</li>}
            {!q.isFetching && q.isSuccess && results.length === 0 && debounced === text.trim() && (
              <li className="ac-empty">No results</li>
            )}
            {results.map((r, k) => (
              <li
                key={`${r.lat},${r.lon},${k}`}
                role="option"
                aria-selected={k === active}
                data-testid={`${testId}-option-${k}`}
                className={k === active ? "active" : undefined}
                onMouseDown={(e) => {
                  e.preventDefault();
                  pick(r);
                }}
                onMouseEnter={() => setActive(k)}
              >
                {r.name}
              </li>
            ))}
          </ul>
        )}
      </div>
      {actions}
    </div>
  );
}

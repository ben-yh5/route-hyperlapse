import { useEffect, useRef, useState } from "react";
import maplibregl, {
  type GeoJSONSource,
  type LngLatBoundsLike,
  type Map as MLMap,
  type MapMouseEvent,
  type StyleSpecification,
  type ExpressionSpecification,
} from "maplibre-gl";
import type { FeatureCollection } from "geojson";
import { api, type LngLat, type RouteResponse, type SamplesFC } from "../api";
import { waypointLabel, type Waypoint } from "../format";

type Props = {
  styleUrl: string;
  route: RouteResponse | null;
  start: Waypoint | null;
  vias: Waypoint[];
  end: Waypoint | null;
  /** false when a GPX track is in use: no waypoint editing. */
  editable: boolean;
  samples: SamplesFC | null;
  hovered: number | null;
  selected: number | null;
  showCoverage: boolean;
  onMapClick: (ll: LngLat) => void;
  onWaypointMove: (idx: number, ll: LngLat) => void;
  onRemoveVia: (k: number) => void;
  onInsertVia: (pos: number, ll: LngLat) => void;
  onHover: (i: number | null) => void;
  onSelect: (i: number) => void;
};

const EMPTY_FC: FeatureCollection = { type: "FeatureCollection", features: [] };

const FALLBACK_STYLE: StyleSpecification = {
  version: 8,
  sources: {},
  layers: [{ id: "bg", type: "background", paint: { "background-color": "#1f2329" } }],
};

export const STATUS_COLORS = {
  chosen: "#14b8a6",
  gap: "#ef4444",
  overridden: "#f59e0b",
  excluded: "#6b7280",
} as const;

const COVERAGE_LAYERS = ["mly-seq", "mly-img"];

function addLayers(map: MLMap) {
  if (map.getSource("route")) return;

  map.addSource("mly", {
    type: "vector",
    tiles: [api.tilesUrl()],
    minzoom: 6,
    maxzoom: 14,
    attribution: "Imagery © Mapillary contributors, CC BY-SA 4.0",
  });
  map.addLayer({
    id: "mly-seq",
    type: "line",
    source: "mly",
    "source-layer": "sequence",
    minzoom: 6,
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": "#35af6d",
      "line-opacity": 0.55,
      "line-width": ["interpolate", ["linear"], ["zoom"], 6, 1, 14, 2.5, 18, 4],
    },
  });
  map.addLayer({
    id: "mly-img",
    type: "circle",
    source: "mly",
    "source-layer": "image",
    minzoom: 14,
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 14, 2, 18, 4],
      "circle-color": "#35af6d",
      "circle-opacity": 0.8,
      "circle-stroke-color": "#ffffff",
      "circle-stroke-width": 0.5,
    },
  });

  map.addSource("route", { type: "geojson", data: EMPTY_FC });
  map.addLayer({
    id: "route-casing",
    type: "line",
    source: "route",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#0b2e6b", "line-width": 7, "line-opacity": 0.6 },
  });
  map.addLayer({
    id: "route-line",
    type: "line",
    source: "route",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#3b82f6", "line-width": 4 },
  });
  map.addLayer({
    id: "route-hit",
    type: "line",
    source: "route",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#000000", "line-width": 18, "line-opacity": 0 },
  });

  map.addSource("samples", { type: "geojson", data: EMPTY_FC, promoteId: "i" });
  const sel: ExpressionSpecification = ["boolean", ["feature-state", "selected"], false];
  const hov: ExpressionSpecification = ["boolean", ["feature-state", "hover"], false];
  map.addLayer({
    id: "samples",
    type: "circle",
    source: "samples",
    paint: {
      "circle-radius": [
        "interpolate",
        ["linear"],
        ["zoom"],
        6,
        ["case", sel, 7, hov, 6, 2.5],
        12,
        ["case", sel, 8, hov, 7, 4],
        16,
        ["case", sel, 11, hov, 10, 6],
      ],
      "circle-color": [
        "match",
        ["get", "status"],
        "chosen",
        STATUS_COLORS.chosen,
        "gap",
        STATUS_COLORS.gap,
        "overridden",
        STATUS_COLORS.overridden,
        "excluded",
        STATUS_COLORS.excluded,
        STATUS_COLORS.chosen,
      ],
      "circle-stroke-color": ["case", sel, "#ffffff", hov, "#fde047", "#0b1220"],
      "circle-stroke-width": ["case", sel, 3, hov, 2, 0.5],
    },
  });
}

function nearestVertex(coords: number[][], p: LngLat): number {
  // Equirectangular approximation is plenty for picking the nearest vertex.
  const k = Math.cos((p.lat * Math.PI) / 180);
  let best = 0;
  let bestD = Infinity;
  for (let j = 0; j < coords.length; j++) {
    const dx = (coords[j][0] - p.lon) * k;
    const dy = coords[j][1] - p.lat;
    const d = dx * dx + dy * dy;
    if (d < bestD) {
      bestD = d;
      best = j;
    }
  }
  return best;
}

function markerEl(cls: string, text: string, title: string): HTMLElement {
  const el = document.createElement("div");
  el.className = `wp-marker ${cls}`;
  el.textContent = text;
  el.title = title;
  return el;
}

export default function MapView(props: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MLMap | null>(null);
  const propsRef = useRef(props);
  propsRef.current = props;
  const [styleVersion, setStyleVersion] = useState(0);

  // ------------------------------------------------------------ init
  useEffect(() => {
    const map = new maplibregl.Map({
      container: containerRef.current!,
      style: props.styleUrl,
      center: [10, 48],
      zoom: 3.5,
      attributionControl: { compact: true },
      dragRotate: false,
      pitchWithRotate: false,
    });
    mapRef.current = map;
    // Exposed for end-to-end tests (e.g. projecting route coordinates to screen points).
    (window as unknown as { __hyperlapseMap?: MLMap }).__hyperlapseMap = map;
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-left");
    map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-right");

    let styleReady = false;
    let fellBack = false;
    map.on("style.load", () => {
      styleReady = true;
      addLayers(map);
      setStyleVersion((v) => v + 1);
    });
    map.on("error", (e) => {
      // If the basemap style can't load (offline / bad URL), fall back to a blank style.
      if (!styleReady && !fellBack) {
        fellBack = true;
        console.warn("Basemap style failed to load, using fallback", e.error);
        map.setStyle(FALLBACK_STYLE);
      }
    });

    // ---------------------------------------------------- sample hover / select
    map.on("mousemove", "samples", (e) => {
      const f = e.features?.[0];
      if (!f) return;
      map.getCanvas().style.cursor = "pointer";
      propsRef.current.onHover(Number(f.properties?.i));
    });
    map.on("mouseleave", "samples", () => {
      map.getCanvas().style.cursor = "";
      propsRef.current.onHover(null);
    });
    map.on("mouseenter", "route-hit", () => {
      if (propsRef.current.editable && !map.getCanvas().style.cursor)
        map.getCanvas().style.cursor = "grab";
    });
    map.on("mouseleave", "route-hit", () => {
      if (map.getCanvas().style.cursor === "grab") map.getCanvas().style.cursor = "";
    });

    map.on("click", (e) => {
      const target = e.originalEvent.target as HTMLElement | null;
      if (target?.closest?.(".wp-marker")) return;
      if (!map.getLayer("samples")) {
        propsRef.current.onMapClick({ lat: e.lngLat.lat, lon: e.lngLat.lng });
        return;
      }
      const hits = map.queryRenderedFeatures(e.point, { layers: ["samples", "route-hit"] });
      const sample = hits.find((f) => f.layer.id === "samples");
      if (sample) {
        propsRef.current.onSelect(Number(sample.properties?.i));
        return;
      }
      if (hits.some((f) => f.layer.id === "route-hit")) return; // route clicks are for dragging
      propsRef.current.onMapClick({ lat: e.lngLat.lat, lon: e.lngLat.lng });
    });

    // ---------------------------------------------------- drag route to insert a via
    const ghost = new maplibregl.Marker({ element: markerEl("ghost-marker", "", "New via") });
    map.on("mousedown", "route-hit", (e: MapMouseEvent) => {
      const p = propsRef.current;
      if (!p.editable || !p.route || e.originalEvent.button !== 0) return;
      if (map.queryRenderedFeatures(e.point, { layers: ["samples"] }).length) return;
      e.preventDefault();
      map.dragPan.disable();
      const rect = map.getCanvas().getBoundingClientRect();
      const startPt = e.point;
      const grab = { lat: e.lngLat.lat, lon: e.lngLat.lng };
      let moved = false;
      map.getCanvas().style.cursor = "grabbing";

      const onMove = (ev: MouseEvent) => {
        const pt = new maplibregl.Point(ev.clientX - rect.left, ev.clientY - rect.top);
        if (!moved && pt.dist(startPt) < 4) return;
        if (!moved) {
          moved = true;
          ghost.setLngLat(map.unproject(pt)).addTo(map);
        }
        ghost.setLngLat(map.unproject(pt));
      };
      const onUp = (ev: MouseEvent) => {
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
        map.dragPan.enable();
        map.getCanvas().style.cursor = "";
        ghost.remove();
        if (!moved) return;
        const cur = propsRef.current;
        if (!cur.route || !cur.start || !cur.end) return;
        const ll = map.unproject(new maplibregl.Point(ev.clientX - rect.left, ev.clientY - rect.top));
        const coords = cur.route.geojson.geometry.coordinates;
        const grabIdx = nearestVertex(coords, grab);
        const wps = [cur.start, ...cur.vias, cur.end];
        // Insert after the last waypoint whose route position is <= the grab point.
        let after = 0;
        wps.forEach((w, j) => {
          if (j < wps.length - 1 && nearestVertex(coords, w) <= grabIdx) after = j;
        });
        cur.onInsertVia(after, { lat: ll.lat, lon: ll.lng });
      };
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    });

    return () => {
      map.remove();
      mapRef.current = null;
    };
    // The style URL comes from config and never changes during a session.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ------------------------------------------------------------ route data + fit
  const fittedRoute = useRef<string | null>(null);
  useEffect(() => {
    const map = mapRef.current;
    const src = map?.getSource("route") as GeoJSONSource | undefined;
    if (!map || !src) return;
    src.setData(props.route?.geojson ?? EMPTY_FC);
    const route = props.route;
    if (route && route.route_id !== fittedRoute.current) {
      const firstFit = fittedRoute.current === null;
      fittedRoute.current = route.route_id;
      const coords = route.geojson.geometry.coordinates;
      if (coords.length >= 2) {
        const b = coords.reduce(
          (acc, [x, y]) => [Math.min(acc[0], x), Math.min(acc[1], y), Math.max(acc[2], x), Math.max(acc[3], y)],
          [Infinity, Infinity, -Infinity, -Infinity],
        );
        // Fit to each new route, except edits whose route already lies inside the view
        // (keeps the map still while the user drags waypoints).
        const view = map.getBounds();
        const fits = view.contains([b[0], b[1]]) && view.contains([b[2], b[3]]);
        const tiny =
          b[2] - b[0] < (view.getEast() - view.getWest()) * 0.1 &&
          b[3] - b[1] < (view.getNorth() - view.getSouth()) * 0.1;
        if (firstFit || !fits || tiny)
          map.fitBounds(b as LngLatBoundsLike, { padding: 60, duration: 600, maxZoom: 15 });
      }
    }
  }, [props.route, styleVersion]);

  // ------------------------------------------------------------ samples
  useEffect(() => {
    const src = mapRef.current?.getSource("samples") as GeoJSONSource | undefined;
    src?.setData(props.samples ?? EMPTY_FC);
  }, [props.samples, styleVersion]);

  const prevState = useRef<{ hover: number | null; selected: number | null }>({
    hover: null,
    selected: null,
  });
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.getSource("samples")) return;
    const prev = prevState.current;
    const set = (id: number | null, state: Record<string, boolean>) => {
      if (id == null) return;
      try {
        map.setFeatureState({ source: "samples", id }, state);
      } catch {
        /* source not ready */
      }
    };
    set(prev.hover, { hover: false });
    set(prev.selected, { selected: false });
    set(props.hovered, { hover: true });
    set(props.selected, { selected: true });
    prevState.current = { hover: props.hovered, selected: props.selected };
  }, [props.hovered, props.selected, props.samples, styleVersion]);

  // Pan to the selected sample if it is off-screen.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || props.selected == null || !props.samples) return;
    const feats = props.samples.features;
    const f =
      feats[props.selected]?.properties?.i === props.selected
        ? feats[props.selected]
        : feats.find((x) => x.properties?.i === props.selected);
    if (!f) return;
    const [lon, lat] = f.geometry.coordinates;
    if (!map.getBounds().contains([lon, lat])) map.easeTo({ center: [lon, lat], duration: 400 });
  }, [props.selected, props.samples]);

  // ------------------------------------------------------------ coverage toggle
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    for (const id of COVERAGE_LAYERS) {
      if (map.getLayer(id))
        map.setLayoutProperty(id, "visibility", props.showCoverage ? "visible" : "none");
    }
  }, [props.showCoverage, styleVersion]);

  // ------------------------------------------------------------ waypoint markers
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const { start, vias, end, editable } = props;
    const markers: maplibregl.Marker[] = [];
    const add = (w: Waypoint, idx: number, el: HTMLElement) => {
      const m = new maplibregl.Marker({ element: el, draggable: editable })
        .setLngLat([w.lon, w.lat])
        .addTo(map);
      m.on("dragend", () => {
        const ll = m.getLngLat();
        propsRef.current.onWaypointMove(idx, { lat: ll.lat, lon: ll.lng });
      });
      markers.push(m);
    };
    if (start) add(start, 0, markerEl("wp-start", "", `Start: ${waypointLabel(start)}`));
    vias.forEach((v, k) => {
      const el = markerEl("wp-via", String(k + 1), `Via ${k + 1}: ${waypointLabel(v)} (right-click to remove)`);
      el.addEventListener("contextmenu", (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        if (propsRef.current.editable) propsRef.current.onRemoveVia(k);
      });
      add(v, k + 1, el);
    });
    if (end) add(end, vias.length + 1, markerEl("wp-end", "", `End: ${waypointLabel(end)}`));
    if (!editable) markers.forEach((m) => m.getElement().classList.add("wp-dim"));
    return () => markers.forEach((m) => m.remove());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.start, props.vias, props.end, props.editable]);

  return <div ref={containerRef} className="map" data-testid="map" />;
}

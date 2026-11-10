import { useEffect, useRef, useState } from "react";
import L from "leaflet";
import { STATUS_META, THREAT_META, formatDateTime } from "../lib/format";

// Тёмная подложка Esri работает без API-ключа. CARTO с 2025 года отдаёт
// водяной знак «API KEY REQUIRED» без собственного ключа.
const TILE_URL =
  "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}";
const TILE_ATTRIBUTION =
  'Tiles &copy; <a href="https://www.esri.com/">Esri</a> &mdash; ' +
  'Esri, HERE, Garmin, &copy; OpenStreetMap contributors';

const REGIONS_GEOJSON_URL = "/data/russia-regions.geojson";
const REGION_COLORS = {
  active: { fill: 0.34, weight: 1.8 },
  warning: { fill: 0.26, weight: 1.5 },
  all_clear: { fill: 0.18, weight: 1.3 },
  cleared: { fill: 0.10, weight: 1 }
};

// Радиус зоны вокруг точки назначения: чем крупнее объект, тем шире круг.
const PLACE_KINDS = {
  city: { label: "город", radius: 16000 },
  settlement: { label: "населённый пункт", radius: 11000 },
  district: { label: "район", radius: 28000 }
};

function placeKind(kind) {
  return PLACE_KINDS[kind] || PLACE_KINDS.settlement;
}

function validPlaces(alert) {
  return (alert.places || []).filter(
    (place) => place && place.lat != null && place.lon != null
  );
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => {
    switch (char) {
      case "&":
        return "&amp;";
      case "<":
        return "&lt;";
      case ">":
        return "&gt;";
      case '"':
        return "&quot;";
      default:
        return "&#39;";
    }
  });
}

function safeUrl(value) {
  if (!value) return "";
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : "";
  } catch {
    return "";
  }
}

function popupHtml(alert) {
  const status = STATUS_META[alert.status] || STATUS_META.cleared;
  const threat = THREAT_META[alert.threat_type] || THREAT_META.unknown;
  const snippet = alert.message?.length > 240 ? `${alert.message.slice(0, 240)}…` : alert.message;
  const sourceUrl = safeUrl(alert.sources?.find((item) => item.url)?.url);
  const sourceLine = sourceUrl
    ? `<a href="${escapeHtml(sourceUrl)}" target="_blank" rel="noreferrer noopener">Открыть источник</a>`
    : "";
  const places = validPlaces(alert);
  const placesLine = places.length
    ? `<p class="radar-popup__places">${places
        .map((place) => `<span>${escapeHtml(place.name)}</span>`)
        .join("")}</p>`
    : "";

  return `
    <div class="radar-popup" style="--popup-color:${status.color}">
      <div class="radar-popup__status">
        <span class="radar-popup__dot"></span>${escapeHtml(status.label)}
      </div>
      <h3>${escapeHtml(alert.region?.name || "Регион не определён")}</h3>
      ${placesLine}
      <p class="radar-popup__meta">${escapeHtml(threat.label)} · ${escapeHtml(
        formatDateTime(alert.event_at)
      )} · местное время</p>
      <p class="radar-popup__text">${escapeHtml(snippet)}</p>
      <div class="radar-popup__footer">
        <span>Источников: ${Number(alert.merged_count || 1)}</span>
        ${sourceLine}
      </div>
    </div>
  `;
}
function placeTooltipHtml(alert, place) {
  const status = STATUS_META[alert.status] || STATUS_META.cleared;
  const meta = placeKind(place.kind);
  return `<div class="place-tooltip__inner" style="--marker:${status.color}">
            <span class="place-tooltip__name">${escapeHtml(place.name)}</span>
            <span class="place-tooltip__meta">${escapeHtml(meta.label)} · ${escapeHtml(
              status.label
            )}</span>
          </div>`;
}



function alertsByRegion(alerts) {
  const map = new Map();
  for (const alert of alerts) {
    const code = alert.region?.code;
    if (!code) continue;
    const current = map.get(code);
    if (!current || new Date(alert.event_at) > new Date(current.event_at)) {
      map.set(code, alert);
    }
  }
  return map;
}

function regionStyle(feature, alert) {
  if (!alert) {
    return {
      color: "#36506a",
      weight: 0.8,
      opacity: 0.6,
      fillColor: "#16212e",
      fillOpacity: 0.1
    };
  }
  const status = STATUS_META[alert.status] || STATUS_META.cleared;
  const visual = REGION_COLORS[alert.status] || REGION_COLORS.cleared;
  return {
    color: status.color,
    weight: visual.weight,
    opacity: 0.95,
    fillColor: status.color,
    fillOpacity: visual.fill
  };
}

function regionTooltipHtml(properties, alert) {
  const name = escapeHtml(properties.name || "Регион");
  if (!alert) {
    return `<div class="region-tooltip__name">${name}</div>
            <div class="region-tooltip__meta">Нет активных сигналов</div>`;
  }
  const status = STATUS_META[alert.status] || STATUS_META.cleared;
  const threat = THREAT_META[alert.threat_type] || THREAT_META.unknown;
  return `<div class="region-tooltip__name">${name}</div>
          <div class="region-tooltip__meta" style="color:${status.color}">
            ${escapeHtml(status.label)} · ${escapeHtml(threat.label)}
          </div>`;
}

export default function RadarMap({ alerts, focus, onSelect }) {
  const containerRef = useRef(null);
  const mapRef = useRef(null);
  const layerRef = useRef(null);
  const geoLayerRef = useRef(null);
  const markersRef = useRef(new Map());
  const fittedRef = useRef(false);
  const alertsRef = useRef(alerts);
  const alertsByCodeRef = useRef(new Map());
  const onSelectRef = useRef(onSelect);
  const focusTimerRef = useRef(null);
  const [regionsGeo, setRegionsGeo] = useState(null);

  alertsRef.current = alerts;
  alertsByCodeRef.current = alertsByRegion(alerts);
  onSelectRef.current = onSelect;

  const signature = alerts
    .map(
      (alert) =>
        `${alert.id}:${alert.status}:${alert.event_at}:${alert.merged_count}:` +
        validPlaces(alert).map((place) => place.name).join(",")
    )
    .join("|");

  // Инициализация карты — один раз.
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return undefined;

    const map = L.map(containerRef.current, {
      zoomControl: false,
      minZoom: 3,
      maxZoom: 13,
      worldCopyJump: true,
      preferCanvas: true
    }).setView([61.0, 90.0], 3);

    L.tileLayer(TILE_URL, {
      attribution: TILE_ATTRIBUTION,
      maxNativeZoom: 16,
      maxZoom: 19
    }).addTo(map);

    L.control.zoom({ position: "bottomleft" }).addTo(map);
    layerRef.current = L.layerGroup().addTo(map);
    mapRef.current = map;

    const invalidate = () => map.invalidateSize();
    window.addEventListener("resize", invalidate);
    const timer = window.setTimeout(invalidate, 60);

    return () => {
      window.removeEventListener("resize", invalidate);
      window.clearTimeout(timer);
      if (focusTimerRef.current) window.clearTimeout(focusTimerRef.current);
      map.remove();
      mapRef.current = null;
      layerRef.current = null;
      geoLayerRef.current = null;
      markersRef.current.clear();
    };
  }, []);

  // Границы субъектов РФ: статический GeoJSON, загружаем один раз.
  useEffect(() => {
    let alive = true;
    fetch(REGIONS_GEOJSON_URL)
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      })
      .then((data) => {
        if (alive) setRegionsGeo(data);
      })
      .catch(() => {
        // Карта продолжит работать с точками, даже если границы не загрузились.
      });
    return () => {
      alive = false;
    };
  }, []);

  // Заливка областей по последнему статусу региона.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !regionsGeo) return;

    const styleFor = (feature) =>
      regionStyle(feature, alertsByCodeRef.current.get(feature.properties.code));

    if (!geoLayerRef.current) {
      const layer = L.geoJSON(regionsGeo, {
        style: styleFor,
        onEachFeature: (feature, path) => {
          const code = feature.properties.code;
          path.bindTooltip(
            () => regionTooltipHtml(feature.properties, alertsByCodeRef.current.get(code)),
            { sticky: true, direction: "top", opacity: 1, className: "region-tooltip" }
          );
          path.on("click", () => {
            // Убираем подсказку, чтобы она не зависала после перемещения карты.
            path.closeTooltip();
            const alert = alertsByCodeRef.current.get(code);
            if (alert) {
              onSelectRef.current?.(alert);
              return;
            }
            map.fitBounds(path.getBounds(), {
              padding: [40, 40],
              maxZoom: 6,
              animate: true
            });
          });
          path.on("mouseover", () => {
            const alert = alertsByCodeRef.current.get(code);
            const base = regionStyle(feature, alert);
            path.setStyle({
              weight: 2.4,
              fillOpacity: Math.min(base.fillOpacity + 0.12, 0.5)
            });
          });
          path.on("mouseout", () => {
            path.setStyle(regionStyle(feature, alertsByCodeRef.current.get(code)));
          });
        }
      }).addTo(map);
      layer.bringToBack();
      geoLayerRef.current = layer;
    } else {
      geoLayerRef.current.setStyle(styleFor);
    }
  }, [regionsGeo, signature]);

  // Перерисовка маркеров только при реальном изменении данных.
  useEffect(() => {
    const map = mapRef.current;
    const layer = layerRef.current;
    if (!map || !layer) return;

    layer.clearLayers();
    markersRef.current.clear();

    for (const alert of alertsRef.current) {
      const status = STATUS_META[alert.status] || STATUS_META.cleared;
      const places = validPlaces(alert);

      // Точки назначения: конкретные города/районы из сообщения.
      // Круг — зона вокруг точки, подпись — название населённого пункта.
      if (places.length > 0) {
        for (const [index, place] of places.entries()) {
          const meta = placeKind(place.kind);
          // Подписи соседних точек разводим влево/вправо, чтобы не слипались.
          const sideClass = index % 2 === 1 ? " place-marker--left" : "";

          L.circle([place.lat, place.lon], {
            radius: meta.radius,
            color: status.color,
            weight: 1,
            opacity: 0.5,
            fillColor: status.color,
            fillOpacity: 0.08,
            dashArray: "3 7",
            interactive: false
          }).addTo(layer);

          const icon = L.divIcon({
            className: "marker-wrap place-marker-wrap",
            html: `<div class="place-marker${sideClass}" style="--marker:${status.color}">
                     <span class="place-marker__pulse"></span>
                     <span class="place-marker__core"></span>
                     <span class="place-marker__label">${escapeHtml(place.name)}</span>
                   </div>`,
            iconSize: [18, 18],
            iconAnchor: [9, 9]
          });

          const marker = L.marker([place.lat, place.lon], {
            icon,
            title: `${place.name} — ${status.label}`,
            riseOnHover: true,
            zIndexOffset: 600
          });

          marker.bindPopup(popupHtml(alert), {
            className: "radar-popup-wrap",
            maxWidth: 330,
            minWidth: 240,
            autoPanPadding: [28, 28]
          });
          marker.on("click", () => onSelect?.(alert));
          marker.addTo(layer);
          markersRef.current.set(
            `${alert.region?.code || alert.id}:${place.name}`,
            marker
          );
        }
        continue;
      }

      if (alert.lat == null || alert.lon == null) continue;

      L.circle([alert.lat, alert.lon], {
        radius: 26000,
        color: status.color,
        weight: 1,
        opacity: 0.45,
        fillColor: status.color,
        fillOpacity: 0.07,
        dashArray: "3 8",
        interactive: false
      }).addTo(layer);

      const icon = L.divIcon({
        className: "marker-wrap",
        html: `<div class="radar-marker" style="--marker:${status.color}">
                 <span class="radar-marker__ring"></span>
                 <span class="radar-marker__core"></span>
               </div>`,
        iconSize: [30, 30],
        iconAnchor: [15, 15]
      });

      const marker = L.marker([alert.lat, alert.lon], {
        icon,
        title: `${alert.region?.name || "Регион"} — ${status.label}`,
        riseOnHover: true
      });

      marker.bindPopup(popupHtml(alert), {
        className: "radar-popup-wrap",
        maxWidth: 330,
        minWidth: 240,
        autoPanPadding: [28, 28]
      });
      marker.on("click", () => onSelect?.(alert));
      marker.addTo(layer);
      markersRef.current.set(alert.region?.code || String(alert.id), marker);
    }
  }, [signature, onSelect]);

  // Первичное кадрирование под данные.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || fittedRef.current) return;
    const points = [];
    for (const alert of alerts) {
      const places = validPlaces(alert);
      if (places.length > 0) {
        for (const place of places) points.push([place.lat, place.lon]);
      } else if (alert.lat != null && alert.lon != null) {
        points.push([alert.lat, alert.lon]);
      }
    }
    if (points.length === 0) return;

    map.fitBounds(L.latLngBounds(points), {
      padding: [50, 50],
      maxZoom: 7,
      animate: false
    });
    fittedRef.current = true;
  }, [signature, alerts]);

  // Фокус по клику в таймлайне.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !focus || focus.lat == null || focus.lon == null) return;

    const hasPlace = Boolean(focus.place);
    map.flyTo([focus.lat, focus.lon], Math.max(map.getZoom(), hasPlace ? 9 : 8), {
      duration: 0.8
    });
    const key = hasPlace
      ? `${focus.code}:${focus.place}`
      : focus.code;
    const marker = key ? markersRef.current.get(key) : null;
    if (marker) {
      if (focusTimerRef.current) window.clearTimeout(focusTimerRef.current);
      focusTimerRef.current = window.setTimeout(() => marker.openPopup(), 850);
    }
  }, [focus]);

  return (
    <div className="map-shell">
      <div ref={containerRef} className="map-canvas" aria-label="Карта тревог" />

      <div className="map-overlay map-overlay--top">
        <span className="map-overlay__dot" />
        <span>Сканирование секторов</span>
        <b>{alerts.length}</b>
      </div>

      {alerts.length === 0 && (
        <div className="map-empty">
          <span className="map-empty__ring" />
          <strong>Нет активных сигналов</strong>
          <p>Измените период или регион. Новые события появятся автоматически.</p>
        </div>
      )}

      <div className="map-scanline" aria-hidden="true" />
    </div>
  );
}

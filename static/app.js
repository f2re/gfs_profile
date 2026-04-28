const runDateEl = document.getElementById('runDate');
const cycleEl = document.getElementById('cycle');
const leadEl = document.getElementById('lead');
const loadBtn = document.getElementById('loadBtn');
const coordText = document.getElementById('coordText');
const csvOut = document.getElementById('csvOut');
const metaEl = document.getElementById('meta');
const cacheInfoEl = document.getElementById('cacheInfo');
const copyCsvBtn = document.getElementById('copyCsv');
const toastContainer = document.getElementById('toastContainer');
const loadingOverlay = document.getElementById('loadingOverlay');

const now = new Date();
runDateEl.value = now.toISOString().slice(0, 10);
let selected = { lat: 55.75, lon: 37.62 };

const map = L.map('map', { zoomControl: true }).setView([selected.lat, selected.lon], 4);

L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', {
  maxZoom: 19,
  subdomains: 'abcd',
  attribution: '&copy; OpenStreetMap, &copy; CARTO'
}).addTo(map);

let marker = L.marker([selected.lat, selected.lon]).addTo(map);

const majorCities = [
  { name: 'Москва', lat: 55.7558, lon: 37.6176 },
  { name: 'Санкт-Петербург', lat: 59.9343, lon: 30.3351 },
  { name: 'Екатеринбург', lat: 56.8389, lon: 60.6057 },
  { name: 'Новосибирск', lat: 55.0084, lon: 82.9357 },
  { name: 'Казань', lat: 55.7963, lon: 49.1088 },
  { name: 'Минск', lat: 53.9, lon: 27.5667 },
  { name: 'Киев', lat: 50.4501, lon: 30.5234 }
];

const cityLayer = L.layerGroup();
majorCities.forEach((city) => {
  const cityMarker = L.circleMarker([city.lat, city.lon], {
    radius: 4,
    color: '#9bd4ff',
    fillColor: '#9bd4ff',
    fillOpacity: 0.7,
    weight: 1
  }).bindTooltip(city.name, { direction: 'top', offset: [0, -2] });
  cityLayer.addLayer(cityMarker);
});
cityLayer.addTo(map);

map.on('click', (e) => {
  selected = { lat: e.latlng.lat, lon: e.latlng.lng };
  marker.setLatLng([selected.lat, selected.lon]);
  coordText.textContent = `lat: ${selected.lat.toFixed(3)}, lon: ${selected.lon.toFixed(3)}`;
  showToast('Точка на карте обновлена.', 'ok');
});

function toYmd(s) {
  return s.replaceAll('-', '');
}

function toggleLoading(isLoading) {
  loadingOverlay.classList.toggle('hidden', !isLoading);
}

function showToast(message, type = 'ok') {
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = message;
  toastContainer.appendChild(el);
  setTimeout(() => el.remove(), 3200);
}

async function fetchJson(url) {
  const resp = await fetch(url);
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const err = await resp.json();
      detail = err.detail || detail;
    } catch (_) {
      // ignore parse error
    }
    throw new Error(detail);
  }
  return resp.json();
}

async function loadCacheInfo() {
  try {
    const data = await fetchJson('/api/cache-info');
    cacheInfoEl.textContent = `Кэш: hits ${data.hits}, misses ${data.misses}, active ${data.currsize}/${data.maxsize}`;
  } catch (_) {
    cacheInfoEl.textContent = 'Кэш: недоступно';
  }
}

async function loadCycles() {
  toggleLoading(true);
  try {
    const date = toYmd(runDateEl.value);
    const data = await fetchJson(`/api/available-cycles?date=${date}`);
    cycleEl.innerHTML = '';

    if (!data.cycles || data.cycles.length === 0) {
      showToast('Для выбранной даты на сервере нет доступных сроков.', 'err');
      leadEl.innerHTML = '';
      return;
    }

    data.cycles.forEach((c) => {
      const opt = document.createElement('option');
      opt.value = c.cycle;
      opt.textContent = `${c.cycle}z (${c.forecast_steps} шагов)`;
      cycleEl.appendChild(opt);
    });

    await loadLeads();
    showToast('Сроки модели загружены.', 'ok');
  } catch (err) {
    showToast(`Ошибка загрузки сроков: ${err.message}`, 'err');
  } finally {
    toggleLoading(false);
    await loadCacheInfo();
  }
}

async function loadLeads() {
  const date = toYmd(runDateEl.value);
  const cycle = cycleEl.value;
  if (!cycle) return;

  const data = await fetchJson(`/api/available-leads?date=${date}&cycle=${cycle}`);
  leadEl.innerHTML = '';
  data.leads.forEach((l) => {
    const opt = document.createElement('option');
    opt.value = l.index;
    opt.textContent = `+${l.lead_hours} ч (действует ${l.valid_time_utc} UTC)`;
    leadEl.appendChild(opt);
  });
}

function toCsv(rows, columns) {
  const lines = [columns.join(',')];
  rows.forEach((r) => lines.push(columns.map((c) => r[c]).join(',')));
  return lines.join('\n');
}

function drawProfile(rows) {
  const hKm = rows.map((r) => r.geopotential_height_m / 1000);

  const traces = [
    { x: rows.map((r) => r.temperature_c), y: hKm, name: 'Температура, °C', xaxis: 'x1', type: 'scatter', line: { color: '#ffb86b' } },
    { x: rows.map((r) => r.relative_humidity_pct), y: hKm, name: 'Влажность, %', xaxis: 'x2', type: 'scatter', line: { color: '#7ef0ff' } },
    { x: rows.map((r) => r.wind_speed_ms), y: hKm, name: 'Скорость ветра, м/с', xaxis: 'x3', type: 'scatter', line: { color: '#b58dff' } }
  ];

  const layout = {
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: 'rgba(2,10,20,0.45)',
    font: { color: '#ddebff' },
    title: 'Вертикальные профили параметров',
    grid: { rows: 1, columns: 3, pattern: 'independent' },
    yaxis: { title: 'Высота, км', gridcolor: 'rgba(173,209,255,0.15)' },
    xaxis: { title: 'Температура, °C' },
    xaxis2: { title: 'Влажность, %' },
    xaxis3: { title: 'Скорость ветра, м/с' },
    margin: { t: 42, r: 20, b: 40, l: 60 }
  };

  Plotly.newPlot('plot', traces, layout, { responsive: true, displaylogo: false });
}

function makeWindBarbPath(speedKnots) {
  let knots = Math.max(0, Math.round(speedKnots / 5) * 5);
  const flags50 = Math.floor(knots / 50);
  knots -= flags50 * 50;
  const barbs10 = Math.floor(knots / 10);
  knots -= barbs10 * 10;
  const half5 = Math.floor(knots / 5);

  return { flags50, barbs10, half5 };
}

function drawWindBarbs(rows) {
  const host = document.getElementById('windBarb');
  const w = host.clientWidth || 900;
  const h = host.clientHeight || 320;

  const sampled = rows.filter((_, i) => i % 2 === 0);
  const heights = sampled.map((r) => r.geopotential_height_m / 1000);
  const maxH = Math.max(...heights, 1);

  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', `0 0 ${w} ${h}`);

  sampled.forEach((r, i) => {
    const x = 30 + (i * (w - 60)) / Math.max(sampled.length - 1, 1);
    const y = h - 20 - ((r.geopotential_height_m / 1000) / maxH) * (h - 40);

    const dirFrom = r.wind_dir_deg;
    const dirTo = (dirFrom + 180) % 360;
    const speedKnots = r.wind_speed_ms * 1.94384;

    const len = 24;
    const angle = (dirTo - 90) * (Math.PI / 180);
    const x2 = x + len * Math.cos(angle);
    const y2 = y + len * Math.sin(angle);

    const shaft = document.createElementNS(ns, 'line');
    shaft.setAttribute('x1', x);
    shaft.setAttribute('y1', y);
    shaft.setAttribute('x2', x2);
    shaft.setAttribute('y2', y2);
    shaft.setAttribute('stroke', '#c9e8ff');
    shaft.setAttribute('stroke-width', '1.8');
    svg.appendChild(shaft);

    const parts = makeWindBarbPath(speedKnots);
    let pos = 0;

    const drawTick = (size, full = true) => {
      const t = (pos + 4) / len;
      const bx = x + t * (x2 - x);
      const by = y + t * (y2 - y);
      const perp = angle - Math.PI / 3;
      const tx = bx + size * Math.cos(perp);
      const ty = by + size * Math.sin(perp);

      const tick = document.createElementNS(ns, 'line');
      tick.setAttribute('x1', bx);
      tick.setAttribute('y1', by);
      tick.setAttribute('x2', tx);
      tick.setAttribute('y2', ty);
      tick.setAttribute('stroke', '#c9e8ff');
      tick.setAttribute('stroke-width', full ? '1.5' : '1.2');
      svg.appendChild(tick);
      pos += 5;
    };

    for (let f = 0; f < parts.flags50; f += 1) {
      const t1 = (pos + 4) / len;
      const t2 = (pos + 9) / len;
      const p1x = x + t1 * (x2 - x);
      const p1y = y + t1 * (y2 - y);
      const p2x = x + t2 * (x2 - x);
      const p2y = y + t2 * (y2 - y);
      const perp = angle - Math.PI / 3;
      const p3x = p1x + 9 * Math.cos(perp);
      const p3y = p1y + 9 * Math.sin(perp);

      const poly = document.createElementNS(ns, 'polygon');
      poly.setAttribute('points', `${p1x},${p1y} ${p2x},${p2y} ${p3x},${p3y}`);
      poly.setAttribute('fill', '#c9e8ff');
      svg.appendChild(poly);
      pos += 8;
    }

    for (let b = 0; b < parts.barbs10; b += 1) drawTick(9, true);
    for (let hb = 0; hb < parts.half5; hb += 1) drawTick(5, false);

    if (i % 3 === 0) {
      const lbl = document.createElementNS(ns, 'text');
      lbl.setAttribute('x', x - 8);
      lbl.setAttribute('y', h - 4);
      lbl.setAttribute('fill', '#9fb8d6');
      lbl.setAttribute('font-size', '10');
      lbl.textContent = `${Math.round(r.geopotential_height_m / 1000)}км`;
      svg.appendChild(lbl);
    }
  });

  host.innerHTML = '';
  host.appendChild(svg);
}

async function loadProfile() {
  const date = toYmd(runDateEl.value);
  const cycle = cycleEl.value;
  const lead = leadEl.value;
  if (!cycle || lead === '') {
    showToast('Сначала выберите срок и заблаговременность.', 'err');
    return;
  }

  toggleLoading(true);
  try {
    const q = new URLSearchParams({
      date,
      cycle,
      lead_index: lead,
      lat: selected.lat,
      lon: selected.lon
    });

    const data = await fetchJson(`/api/profile?${q.toString()}`);
    metaEl.textContent = `Действует на: ${data.meta.valid_time_utc} UTC | Ближайшая точка сетки: ${data.meta.nearest_grid_point.lat.toFixed(3)}, ${data.meta.nearest_grid_point.lon.toFixed(3)} | Верх профиля: ${(data.meta.max_height_m / 1000).toFixed(1)} км`;

    csvOut.value = toCsv(data.rows, data.columns);
    drawProfile(data.rows);
    drawWindBarbs(data.rows);
    showToast('Профиль успешно построен.', 'ok');
  } catch (err) {
    showToast(`Ошибка построения профиля: ${err.message}`, 'err');
  } finally {
    toggleLoading(false);
    await loadCacheInfo();
  }
}

copyCsvBtn.addEventListener('click', async () => {
  if (!csvOut.value.trim()) {
    showToast('Таблица пока пустая.', 'err');
    return;
  }
  await navigator.clipboard.writeText(csvOut.value);
  showToast('CSV скопирован в буфер обмена.', 'ok');
});

runDateEl.addEventListener('change', loadCycles);
cycleEl.addEventListener('change', loadLeads);
loadBtn.addEventListener('click', loadProfile);
window.addEventListener('resize', () => {
  const txt = csvOut.value.trim();
  if (txt) {
    const rows = txt.split('\n').slice(1).map((line) => {
      const c = line.split(',');
      return {
        geopotential_height_m: Number(c[5]),
        wind_speed_ms: Number(c[7]),
        wind_dir_deg: Number(c[8])
      };
    });
    drawWindBarbs(rows);
  }
});

loadCycles();
loadCacheInfo();

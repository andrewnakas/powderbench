/* PowderBench site: league-aware leaderboard, charts, rounds, station map. */

const fmt = (v, digits = 2) => (v === null || v === undefined ? "—" : Number(v).toFixed(digits));

const TRUTH_NOTES = {
  snotel:
    "Ground truth comes from NRCS SNOTEL telemetry — automated snow pillows and depth " +
    "sensors high in the mountains, QC’d and voided (for everyone) when sensors glitch.",
  era5:
    "Truth is ERA5 model analysis at each resort’s coordinates (no public " +
    "southern-hemisphere station API exists — real feeds get promoted as they become " +
    "available). Scores resolve about a week after each round.",
  resort:
    "Truth is each resort’s own published snow report, archived twice daily from the " +
    "resort’s site. Resort numbers run generous — but every team is scored against the " +
    "same report, so it’s a fair fight; cross-check the ERA5 league for the reanalysis view.",
};

/* Plain-English names for the automated forecasters. */
const FRIENDLY = {
  "baseline-gfs": "GFS (US model)", "baseline-ecmwf": "ECMWF (European model)",
  "baseline-aifs": "ECMWF AI model (AIFS)", "baseline-icon": "ICON (German model)",
  "baseline-gem": "GEM (Canadian model)", "baseline-ukmo": "UK Met Office model",
  "baseline-nbm": "NOAA National Blend", "baseline-hrrr": "HRRR (US high-res)",
  "baseline-wpc": "NOAA WPC snow forecast", "baseline-ndfd": "National Weather Service forecast",
  "baseline-arome": "AROME (French high-res)", "baseline-openmeteo": "Open-Meteo best match",
  "baseline-consensus": "Model consensus (median)", "baseline-climatology": "Typical for this date",
  "baseline-zeros": "Always zero snow", "baseline-persistence": "Same as yesterday",
  "house-blend": "PowderBench blend", "house-analog": "PowderBench analogs", "house-gbm": "PowderBench ML",
};
const prettyTeam = (t) => FRIENDLY[t] || t;
function kindTag(t) {
  if (["baseline-climatology", "baseline-zeros", "baseline-persistence"].includes(t))
    return '<span class="tag tag-climo">simple reference</span>';
  if (t.startsWith("baseline-")) return '<span class="tag tag-model">weather model</span>';
  if (t.startsWith("house-")) return '<span class="tag tag-house">house</span>';
  return '<span class="tag tag-team">competitor</span>';
}

const PALETTE = ["#7cc4ff", "#7ee2a8", "#ffd28a", "#ff9e9e", "#c9a7ff", "#8ef0e4", "#f0b6d8", "#b3e5ff"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

let map = null;
let markerLayer = null;
let markers = {};
const charts = {};
let state = { league: null, leagues: [], lb: null, stations: [], snowfall: {}, history: [], climo: {} };

if (window.Chart) {
  Chart.defaults.color = "#93a4c3";
  Chart.defaults.borderColor = "#23324f";
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.plugins.tooltip.backgroundColor = "#182a44";
  Chart.defaults.plugins.tooltip.borderColor = "#23324f";
  Chart.defaults.plugins.tooltip.borderWidth = 1;
}

async function loadJSON(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

function drawChart(id, config) {
  if (charts[id]) charts[id].destroy();
  charts[id] = new Chart(document.getElementById(id), config);
  return charts[id];
}

function leagueActive(lg) {
  // live = an open round whose cutoff hasn't long passed, or a recent resolve
  if (lg.open_round && new Date(lg.open_round.cutoff_utc) > Date.now() - 2 * 86400e3) return true;
  if (lg.last_resolved && Date.now() - new Date(lg.last_resolved) < 21 * 86400e3) return true;
  return false;
}

function renderLeagueBar() {
  const bar = document.getElementById("league-bar");
  bar.innerHTML = "";
  for (const lg of state.leagues) {
    const btn = document.createElement("button");
    btn.className = "league-tab" + (lg.name === state.league ? " active" : "");
    btn.innerHTML =
      lg.label +
      (lg.status === "trial" ? ' <span class="badge">TRIAL</span>' : "") +
      (!leagueActive(lg) ? ' <span class="badge off">OFF-SEASON</span>' : "");
    btn.addEventListener("click", () => selectLeague(lg.name));
    bar.appendChild(btn);
  }
}

function renderBoard(entries, rounds) {
  const tbody = document.querySelector("#board tbody");
  tbody.innerHTML = "";
  document.getElementById("rounds-note").textContent =
    rounds ? `${rounds} rounds resolved` : "";
  if (!entries || !entries.length) {
    tbody.innerHTML = `<tr><td colspan="8" style="text-align:center;color:var(--muted)">
      No resolved rounds yet in this league — first scores land after the next rounds mature.</td></tr>`;
    return;
  }
  for (const t of entries) {
    const tr = document.createElement("tr");
    if (t.is_baseline) tr.classList.add("baseline");
    if (t.is_house) tr.classList.add("house");
    if (!t.eligible) tr.classList.add("unranked");
    const ps = t.powder_score;
    const psClass = ps === null ? "" : ps > 0 ? "score-pos" : "score-neg";
    tr.innerHTML = `
      <td>${t.rank ?? "–"}</td>
      <td class="team"><a href="team.html?t=${encodeURIComponent(t.team)}" title="${t.team}">${prettyTeam(t.team)}</a> ${kindTag(t.team)}</td>
      <td class="${psClass}">${fmt(ps, 1)}</td>
      <td>${fmt(t.mae)}</td>
      <td>${fmt(t.pinball)}</td>
      <td>${fmt(t.brier6, 3)}</td>
      <td>${t.rounds}</td>
      <td>${t.avg_coverage === null ? "—" : Math.round(t.avg_coverage * 100) + "%"}</td>`;
    tbody.appendChild(tr);
  }
}

function renderRounds(rounds) {
  const strip = document.getElementById("rounds-strip");
  strip.innerHTML = "";
  for (const r of [...rounds].reverse()) {
    const teams = Object.entries(r.teams).filter(([, m]) => !m.invalid && m.mae !== null && m.mae !== undefined);
    const best = teams.filter(([, m]) => m.powder_score !== null && !m.late)
      .sort((a, b) => b[1].powder_score - a[1].powder_score)[0];
    const card = document.createElement("div");
    card.className = "round-card";
    const dump = r.biggest_24h
      ? `${r.biggest_24h.inches}&Prime; <span class="who">@ ${r.biggest_24h.resort}</span>`
      : "quiet day";
    card.innerHTML = `
      <div class="date">${r.round_id}</div>
      <div class="snow">${dump}</div>
      <div class="who">${best ? `best: ${best[0]} (${fmt(best[1].powder_score, 1)})` : "no ranked teams"}</div>
      <div class="who">${r.qc.station_horizons_valid} scored · ${r.qc.station_horizons_voided} voided</div>`;
    strip.appendChild(card);
  }
  if (!rounds.length) strip.innerHTML = `<p class="fine">No rounds resolved yet.</p>`;
}

/* ---- The race: season-to-date mean Powder Accuracy per team ---- */
function renderRace(history) {
  const section = document.getElementById("charts");
  const empty = document.getElementById("race-empty");
  const panel = section.querySelector(".chart-panel");
  const scored = history.filter((r) => Object.keys(r.teams).length);
  if (scored.length < 2) {
    panel.style.display = "none";
    empty.style.display = "";
    if (charts["race-chart"]) { charts["race-chart"].destroy(); delete charts["race-chart"]; }
    return;
  }
  panel.style.display = "";
  empty.style.display = "none";
  const teams = [...new Set(scored.flatMap((r) => Object.keys(r.teams)))];
  const labels = scored.map((r) => r.round_id.slice(5));
  const datasets = teams.map((team, i) => {
    let sum = 0, n = 0;
    const data = scored.map((r) => {
      if (team in r.teams) { sum += r.teams[team]; n += 1; }
      return n ? +(sum / n).toFixed(1) : null;
    });
    const baseline = team.startsWith("baseline-");
    return {
      label: team, data,
      borderColor: PALETTE[i % PALETTE.length],
      backgroundColor: PALETTE[i % PALETTE.length],
      borderWidth: baseline ? 1.5 : 2.5,
      borderDash: baseline ? [6, 4] : [],
      pointRadius: 2, spanGaps: true, tension: 0.25,
    };
  });
  drawChart("race-chart", {
    type: "line",
    data: { labels, datasets },
    options: {
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      scales: {
        y: { title: { display: true, text: "Powder Accuracy, season-to-date" } },
        x: { grid: { display: false } },
      },
      plugins: { legend: { labels: { boxWidth: 18, boxHeight: 3 } } },
    },
  });
}

/* ---- This season's snow: per-station daily truth ---- */
function renderSnowfall() {
  const section = document.getElementById("snow-history");
  const sel = document.getElementById("snow-station");
  const entries = Object.entries(state.snowfall).filter(([, s]) => s.dates.length);
  if (!entries.length) { section.style.display = "none"; return; }
  section.style.display = "";
  const byId = Object.fromEntries(state.stations.map((s) => [s.station_id, s]));
  entries.sort((a, b) => b[1].in.reduce((x, y) => x + y, 0) - a[1].in.reduce((x, y) => x + y, 0));
  sel.innerHTML = "";
  for (const [sid, s] of entries) {
    const total = s.in.reduce((x, y) => x + y, 0);
    const opt = document.createElement("option");
    opt.value = sid;
    opt.textContent = `${byId[sid]?.resort ?? sid} — ${total.toFixed(1)}″ recorded`;
    sel.appendChild(opt);
  }
  const draw = () => {
    const s = state.snowfall[sel.value];
    drawChart("snow-chart", {
      type: "bar",
      data: {
        labels: s.dates.map((d) => d.slice(5)),
        datasets: [{
          label: "fresh snow (in)",
          data: s.in,
          backgroundColor: "#7cc4ff",
          borderRadius: 3,
        }],
      },
      options: {
        maintainAspectRatio: false,
        scales: {
          y: { title: { display: true, text: "inches / day" }, beginAtZero: true },
          x: { grid: { display: false } },
        },
        plugins: { legend: { display: false } },
      },
    });
  };
  sel.onchange = draw;
  draw();
}

/* ---- Season explorer: overlay any station-season cumulative curves ---- */
let szn = { league: null, index: null, cache: {}, series: [] };
let sznArchive = null; // southern snow-station archive (INA, Snowy Hydro, Data Vic, ...)

function seasonSpanLabel(startYear, ssm) {
  if (ssm === 1) return String(startYear);
  const a = String(startYear).slice(2), b = String(+startYear + 1).slice(2);
  return `’${a}–’${b}`;
}

function seasonDayLabels(ssm) {
  const days = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  const ticks = [], names = [];
  let idx = 0;
  for (let m = 0; m < 12 && idx < 365; m++) {
    const month = (ssm - 1 + m) % 12;
    for (let d = 1; d <= days[month] && idx < 365; d++, idx++) {
      names.push(`${MONTHS[month]} ${d}`);
      ticks.push(d === 1 ? MONTHS[month] : "");
    }
  }
  return { ticks, names };
}

function drawSeasonChart() {
  const ssm = szn.index.season_start_month;
  const { ticks, names } = seasonDayLabels(ssm);
  drawChart("szn-chart", {
    type: "line",
    data: {
      labels: names,
      datasets: szn.series.map((s, i) => ({
        label: s.label,
        data: s.data,
        borderColor: PALETTE[i % PALETTE.length],
        backgroundColor: PALETTE[i % PALETTE.length],
        borderWidth: 2, pointRadius: 0, tension: 0.15, spanGaps: false,
      })),
    },
    options: {
      maintainAspectRatio: false,
      interaction: { mode: "nearest", axis: "x", intersect: false },
      scales: {
        y: { title: { display: true, text: "cumulative inches" }, beginAtZero: true },
        x: {
          grid: { display: false },
          ticks: {
            autoSkip: false, maxRotation: 0,
            callback: (v) => ticks[v] || null,
          },
        },
      },
      plugins: { legend: { labels: { boxWidth: 18, boxHeight: 3 } } },
    },
  });
}

async function sznStationData(dir, slug) {
  const key = `${dir}/${slug}`;
  if (!szn.cache[key]) szn.cache[key] = await loadJSON(`data/seasons/${dir}/${slug}.json`);
  return szn.cache[key];
}

async function sznAdd(dir, slug, year) {
  const data = await sznStationData(dir, slug);
  if (!data.seasons[year]) return;
  const label = `${data.resort} ${seasonSpanLabel(year, data.season_start_month)}`;
  if (szn.series.some((s) => s.label === label)) return;
  szn.series.push({ label, data: data.seasons[year] });
  if (szn.series.length > 8) szn.series.shift();
  drawSeasonChart();
}

async function initSeasons(name) {
  const section = document.getElementById("seasons");
  szn = { league: name, index: null, cache: {}, series: [] };
  try { szn.index = await loadJSON(`data/seasons/${name}/index.json`); } catch (e) {
    section.style.display = "none";
    if (charts["szn-chart"]) { charts["szn-chart"].destroy(); delete charts["szn-chart"]; }
    return;
  }
  section.style.display = "";
  // southern leagues also get the public snow-station archive (same season axis)
  let entries = szn.index.stations.map((st) => ({ ...st, dir: name }));
  if (sznArchive && sznArchive.season_start_month === szn.index.season_start_month) {
    entries = entries.concat(sznArchive.stations.map((st) => ({ ...st, dir: "southern-stations" })));
  }
  const ssel = document.getElementById("szn-station");
  const ysel = document.getElementById("szn-year");
  ssel.innerHTML = "";
  for (const st of entries) {
    const opt = document.createElement("option");
    opt.value = `${st.dir}|${st.slug}`;
    opt.textContent = st.resort;
    ssel.appendChild(opt);
  }
  const selected = () => {
    const [dir, slug] = ssel.value.split("|");
    return entries.find((s) => s.dir === dir && s.slug === slug);
  };
  const fillYears = () => {
    const st = selected();
    ysel.innerHTML = "";
    for (const y of [...st.seasons].reverse()) {
      const opt = document.createElement("option");
      opt.value = y;
      opt.textContent = seasonSpanLabel(y, szn.index.season_start_month);
      ysel.appendChild(opt);
    }
  };
  ssel.onchange = fillYears;
  fillYears();
  document.getElementById("szn-add").onclick = () => {
    const st = selected();
    sznAdd(st.dir, st.slug, ysel.value);
  };
  document.getElementById("szn-clear").onclick = () => { szn.series = []; drawSeasonChart(); };
  // seed the chart: snowiest station's current + previous season
  const top = entries[0];
  const years = [...top.seasons].reverse();
  for (const y of years.slice(0, 2)) await sznAdd(top.dir, top.slug, y);
}

/* ---- Resorts vs reanalysis (league-independent, drawn once) ---- */
function initComparison(cmp) {
  const section = document.getElementById("receipts");
  const sel = document.getElementById("cmp-resort");
  const slugs = Object.keys(cmp).filter(
    (k) => cmp[k].resort.dates.length || cmp[k].era5.dates.length
  );
  if (!slugs.length) { section.style.display = "none"; return; }
  sel.innerHTML = "";
  for (const slug of slugs) {
    const opt = document.createElement("option");
    opt.value = slug;
    opt.textContent = cmp[slug].name;
    sel.appendChild(opt);
  }
  const draw = () => {
    const c = cmp[sel.value];
    const dates = [...new Set([...c.resort.dates, ...c.era5.dates])].sort();
    const series = (side) => {
      const m = Object.fromEntries(side.dates.map((d, i) => [d, side.in[i]]));
      return dates.map((d) => (d in m ? m[d] : null));
    };
    drawChart("cmp-chart", {
      type: "bar",
      data: {
        labels: dates.map((d) => d.slice(5)),
        datasets: [
          { label: "resort report", data: series(c.resort), backgroundColor: "#ffd28a", borderRadius: 3 },
          { label: "ERA5 reanalysis", data: series(c.era5), backgroundColor: "#7cc4ff", borderRadius: 3 },
        ],
      },
      options: {
        maintainAspectRatio: false,
        scales: {
          y: { title: { display: true, text: "inches / day" }, beginAtZero: true },
          x: { grid: { display: false } },
        },
      },
    });
  };
  sel.onchange = draw;
  draw();
}

/* ---- When it snows: monthly climatology heatmap ---- */
function renderClimo() {
  const section = document.getElementById("climo");
  const grid = document.getElementById("climo-grid");
  const rows = Object.entries(state.climo).filter(([, m]) => m.some((v) => v));
  if (!rows.length) { section.style.display = "none"; return; }
  section.style.display = "";
  const byId = Object.fromEntries(state.stations.map((s) => [s.station_id, s]));
  rows.sort(
    (a, b) => b[1].reduce((x, y) => x + (y || 0), 0) - a[1].reduce((x, y) => x + (y || 0), 0)
  );
  const max = Math.max(...rows.flatMap(([, m]) => m.map((v) => v || 0)));
  grid.innerHTML = "";
  grid.appendChild(Object.assign(document.createElement("div"), { className: "hm-head" }));
  for (const m of MONTHS) {
    grid.appendChild(Object.assign(document.createElement("div"), { className: "hm-head", textContent: m[0] }));
  }
  for (const [sid, months] of rows) {
    const name = byId[sid]?.resort ?? sid;
    const label = document.createElement("div");
    label.className = "hm-name";
    label.textContent = name;
    label.title = name;
    grid.appendChild(label);
    months.forEach((v, i) => {
      const cell = document.createElement("div");
      cell.className = "hm-cell";
      const alpha = v && max ? Math.pow(v / max, 0.6) * 0.95 : 0;
      cell.style.background = `rgba(124, 196, 255, ${alpha.toFixed(3)})`;
      cell.title = `${name} · ${MONTHS[i]}: ${v ? v.toFixed(2) : "0"}″/day average`;
      grid.appendChild(cell);
    });
  }
}

/* ---- Round archive table ---- */
function renderArchive(history) {
  const tbody = document.querySelector("#archive-table tbody");
  tbody.innerHTML = "";
  if (!history.length) {
    tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;color:var(--muted)">
      No resolved rounds yet.</td></tr>`;
    return;
  }
  for (const r of [...history].reverse()) {
    const best = Object.entries(r.teams).sort((a, b) => b[1] - a[1])[0];
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td style="text-align:left">${r.round_id}</td>
      <td style="text-align:left">${r.biggest_24h ? `${r.biggest_24h.inches}″ @ ${r.biggest_24h.resort}` : "quiet day"}</td>
      <td style="text-align:left">${best ? best[0] : "—"}</td>
      <td>${best ? fmt(best[1], 1) : "—"}</td>
      <td>${r.qc ? `${r.qc.station_horizons_valid} / ${r.qc.station_horizons_voided} voided` : "—"}</td>`;
    tbody.appendChild(tr);
  }
}

/* ---- Snow forecast map ---- */
const SNOW_BINS = [
  [0.05, "#40516e", "none"], [1, "#4f86c6", "< 1″"], [3, "#55b4f0", "1–3″"],
  [6, "#9fd8ff", "3–6″"], [12, "#c9a7ff", "6–12″"], [Infinity, "#ffffff", "12″+"],
];
const snowColor = (v) => (v === null || v === undefined ? "#2a3a57" : SNOW_BINS.find(([max]) => v < max)[1]);
const inch = (v, d = 1) => (v === null || v === undefined ? "—" : `${Number(v).toFixed(d)}″`);
const dayName = (iso) => new Date(iso + "T12:00:00Z").toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" });

function teamOrder() {
  return (state.fc?.teams || []).map((t) => t.team);
}

/* The best available forecast for a station: the most accurate forecaster
   (by season Powder Accuracy) that forecast it. Climatology is a reference, not a pick. */
function bestFor(sid) {
  const rows = state.fc?.current?.rows?.[sid];
  if (!rows) return null;
  for (const t of state.fc.teams) {
    if (t.team === "baseline-climatology") continue;
    const r = rows[t.team];
    if (r && (r[2] !== null || r[0] !== null)) return { ...t, r };
  }
  return null;
}

function totalOf(r) { return r ? (r[2] ?? r[1] ?? r[0]) : null; }

/* New-snow density (water / snow depth) in skier's words. */
function snowType(d) {
  if (d < 0.05) return "blower, ultra-light";
  if (d < 0.08) return "light powder";
  if (d < 0.11) return "classic powder";
  if (d < 0.15) return "dense powder";
  if (d < 0.20) return "heavy, wet snow";
  return "very wet, ‘Sierra cement’";
}

function renderLegend() {
  document.getElementById("fc-legend").innerHTML =
    '<span class="fine">3-day snow (best forecast):</span>' +
    SNOW_BINS.map(([, c, l]) => `<span class="lg"><i style="background:${c}"></i>${l}</span>`).join("") +
    '<span class="lg"><i style="background:#2a3a57"></i>no forecast</span>';
}

function renderMap(stations) {
  if (!map) {
    // canvas renderer: thousands of network stations stay smooth
    map = L.map("map", { scrollWheelZoom: false, preferCanvas: true });
    // keyless dark basemap (CARTO's now answers every tile with "API KEY REQUIRED")
    L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}", {
      attribution: "Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ",
      maxZoom: 16,
    }).addTo(map);
  }
  if (markerLayer) markerLayer.remove();
  markerLayer = L.layerGroup();
  const pts = [];
  const featured = (s) => (s.tier || "featured") === "featured";
  markers = {};
  const sorted = [...stations].sort((a, b) => (totalOf(bestFor(a.station_id)?.r) ?? -1) - (totalOf(bestFor(b.station_id)?.r) ?? -1));
  for (const s of sorted) {
    const f = featured(s);
    if (f) pts.push([s.latitude, s.longitude]);
    const tot = totalOf(bestFor(s.station_id)?.r);
    const col = snowColor(tot);
    const m = L.circleMarker([s.latitude, s.longitude], {
      radius: f ? 7 : 4, color: f ? "#0b1220" : col, weight: f ? 1.5 : 0,
      fillColor: col, fillOpacity: tot === null ? 0.5 : 0.9,
    })
      .bindTooltip(`${s.resort}${tot !== null ? ` · ${inch(tot)} in 3 days` : ""}`)
      .on("click", () => showStation(s.station_id))
      .addTo(markerLayer);
    markers[s.station_id] = m;
  }
  markerLayer.addTo(map);
  if (!pts.length) pts.push(...stations.map((s) => [s.latitude, s.longitude]));
  if (pts.length) map.fitBounds(L.latLngBounds(pts).pad(0.15));

  const nFeat = stations.filter(featured).length;
  document.getElementById("station-count").textContent = nFeat < stations.length
    ? `${stations.length.toLocaleString()} stations · bigger dots are the ${nFeat} headline stations`
    : `${stations.length.toLocaleString()} stations`;
  const list = document.getElementById("station-list");
  list.innerHTML = "";
  for (const s of stations) {
    const o = document.createElement("option");
    o.value = `${s.resort} · ${s.name} · ${s.region} · ${s.station_id}`;
    list.appendChild(o);
  }
  renderLegend();
  renderForecastStatus();
  // open the snowiest headline station so the panel is never empty
  const top = [...stations].filter(featured).sort((a, b) => (totalOf(bestFor(b.station_id)?.r) ?? -1) - (totalOf(bestFor(a.station_id)?.r) ?? -1))[0];
  if (top) showStation(top.station_id, false);
}

function renderForecastStatus() {
  const el = document.getElementById("fc-status");
  const c = state.fc?.current;
  if (!c) { el.textContent = "No forecasts in this league right now (it's off-season or the next round hasn't opened)."; return; }
  const days = `${dayName(c.target_days[0])} – ${dayName(c.target_days[2])}`;
  const cut = new Date(c.cutoff_utc).toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit", timeZoneName: "short" });
  el.textContent = c.status === "open"
    ? `Forecasts for ${days}. Forecasters can still submit until ${cut}.`
    : `Forecasts for ${days}, locked at ${cut}.`;
}

function scoreText(t) {
  if (t.score === null || t.score === undefined) return "not scored yet";
  return `Powder Accuracy ${fmt(t.score, 1)}`;
}

function showStation(sid, pan = true) {
  const s = state.stations.find((x) => x.station_id === sid);
  if (!s) return;
  const panel = document.getElementById("fc-panel");
  const c = state.fc?.current;
  const best = bestFor(sid);
  if (pan && markers[sid]) map.setView(markers[sid].getLatLng(), Math.max(map.getZoom(), 8));
  let html = `<h3 class="fc-name">${s.resort}</h3>
    <p class="fc-sub">${s.name} · ${s.region} · ${Math.round(s.elevation_ft).toLocaleString()} ft${s.network ? ` · ${s.network}` : ""}</p>`;
  if (!c || !best) {
    html += `<p class="fc-hint">No forecasts for this station in the current round${(s.tier || "featured") !== "featured" ? " (only some forecasters cover the full network)" : ""}.</p>`;
  } else {
    const r = best.r;
    const d1 = r[0], d2 = r[1] !== null && r[0] !== null ? Math.max(0, r[1] - r[0]) : null;
    const d3 = r[2] !== null && r[1] !== null ? Math.max(0, r[2] - r[1]) : null;
    const tot = totalOf(r);
    const prob = (() => {  // chance of 6"+ on day 1: best-ranked forecaster that gives one
      for (const t of state.fc.teams) {
        const x = c.rows[sid]?.[t.team];
        if (t.team !== "baseline-climatology" && x && x[3] !== null) return { p: x[3], team: t.team };
      }
      return null;
    })();
    html += `<div class="fc-big"><span class="fc-total">${inch(tot)}</span>
        <span class="fc-total-l">new snow expected over the next 3 days</span></div>
      <div class="fc-days">
        ${[d1, d2, d3].map((v, i) => `<div class="fc-day"><span class="fc-dn">${dayName(c.target_days[i])}</span>
          <span class="fc-dv" style="color:${snowColor(v)}">${inch(v)}</span></div>`).join("")}
      </div>
      ${r[4] !== null && r[5] !== null ? `<p class="fc-range">Likely range for the 3 days: <b>${inch(r[4])} – ${inch(r[5])}</b> (80% chance it lands in here)</p>` : ""}
      ${prob ? `<p class="fc-range">Chance of 6″ or more on ${dayName(c.target_days[0])}: <b>${Math.round(prob.p * 100)}%</b></p>` : ""}
      ${(() => {  // water content + density, from the most accurate forecaster that gives SWE
        if (!tot || tot < 0.5) return "";
        for (const t of state.fc.teams) {
          const x = c.rows[sid]?.[t.team];
          if (x && x[7] !== null && x[7] !== undefined && totalOf(x)) {
            const dens = x[7] / totalOf(x);
            return `<p class="fc-range">Water in that snow: <b>${inch(x[7], 2)}</b> · density <b>${(dens * 100).toFixed(0)}%</b>
              (${snowType(dens)}) <span class="fine">from ${prettyTeam(t.team)}</span></p>`;
          }
        }
        return "";
      })()}
      <p class="fc-src">Forecast from <b>${prettyTeam(best.team)}</b> ${kindTag(best.team)}, ${best.score !== null
        ? `the most accurate forecaster in this league so far (${scoreText(best)}).`
        : "the best available (this league has no scores yet)."}</p>`;
    // every forecaster, best first
    const rows = state.fc.teams.filter((t) => c.rows[sid]?.[t.team]);
    html += `<details class="fc-all" open><summary>All ${rows.length} forecasts for this station</summary>
      <table class="fc-table"><thead><tr><th>Forecaster</th><th title="Season Powder Accuracy in this league (higher = more accurate)">Accuracy</th>
      <th>${dayName(c.target_days[0]).split(",")[0]}</th><th>3 days</th></tr></thead><tbody>
      ${rows.map((t) => {
        const x = c.rows[sid][t.team];
        const cls = t.team === best.team ? "fc-best" : t.team === "baseline-climatology" ? "fc-climo" : "";
        return `<tr class="${cls}"><td>${prettyTeam(t.team)}</td><td>${t.score === null || t.score === undefined ? "–" : fmt(t.score, 0)}</td>
          <td>${inch(x[0])}</td><td>${inch(totalOf(x))}</td></tr>`;
      }).join("")}</tbody></table></details>`;
  }
  // how the last scored round went here
  const lastR = state.fc?.last;
  const truth = lastR?.truth?.[sid];
  const recent = lastR && Date.now() - new Date(lastR.target_days[2] + "T00:00:00Z") < 30 * 86400e3;
  if (lastR && truth && recent) {
    const fell = truth[2] ?? truth[1] ?? truth[0];
    const lrows = lastR.rows[sid] || {};
    const top = state.fc.teams.filter((t) => lrows[t.team] && t.team !== "baseline-climatology").slice(0, 4);
    const fellSwe = truth[3];
    html += `<details class="fc-all"><summary>Last time: ${inch(fell)} actually fell${fellSwe !== null && fellSwe !== undefined && fell > 0.5 ? ` (${inch(fellSwe, 2)} of water, ${Math.round(100 * fellSwe / fell)}% dense)` : ""} (${dayName(lastR.target_days[0])} – ${dayName(lastR.target_days[2])})</summary>
      <table class="fc-table"><thead><tr><th>Forecaster</th><th>Predicted</th><th>Missed by</th></tr></thead><tbody>
      ${top.map((t) => { const p = lrows[t.team][2] ?? lrows[t.team][1] ?? lrows[t.team][0];
        return `<tr><td>${prettyTeam(t.team)}</td><td>${inch(p)}</td><td>${p === null || fell === null ? "—" : inch(Math.abs(p - fell))}</td></tr>`; }).join("")}
      </tbody></table></details>`;
  }
  if (state.snowfall?.[sid]?.dates?.length) {
    html += `<p><a href="#snow-history" class="fc-link" data-sid="${sid}">See this season's snow at ${s.resort} →</a></p>`;
  }
  panel.innerHTML = html;
  panel.querySelector(".fc-link")?.addEventListener("click", () => {
    const sel = document.getElementById("snow-station");
    sel.value = sid; sel.dispatchEvent(new Event("change"));
  });
}

function findStation(q) {
  const sid = q.split(" · ").pop();
  if (markers[sid]) showStation(sid);
}

function renderTrackOptions() {
  const sel = document.getElementById("board-track");
  const keep = sel.value;
  sel.innerHTML = '<option value="">Headline (featured stations)</option>';
  const tracks = Object.keys(state.lb.tracks || {});
  const label = (t) => (t === "full" ? "Full network" : t.startsWith("region:") ? `Region · ${t.slice(7)}` : t);
  for (const t of tracks.sort((a, b) => (a === "full" ? -1 : b === "full" ? 1 : a.localeCompare(b)))) {
    sel.appendChild(Object.assign(document.createElement("option"), { value: t, textContent: label(t) }));
  }
  sel.value = tracks.includes(keep) ? keep : "";
  sel.style.display = tracks.length ? "" : "none";
}

function renderHall(months) {
  const box = document.getElementById("hall");
  const rows = (months || []).filter((m) => m.champion || m.best_model);
  box.style.display = rows.length ? "" : "none";
  const now = new Date().toISOString().slice(0, 7);
  const link = (e) => e ? `<a href="team.html?t=${encodeURIComponent(e.team)}">${e.team}</a>` : "—";
  document.querySelector("#hall-table tbody").innerHTML = rows.map((m) => `
    <tr${m.champion?.team?.startsWith("house-") ? ' class="house"' : ""}>
      <td>${m.month}${m.month === now ? ' <span class="badge">SO FAR</span>' : ""}</td>
      <td class="team">${m.champion ? "🏆 " + link(m.champion) : "—"}</td>
      <td>${m.champion ? fmt(m.champion.powder_score, 1) : "—"}</td>
      <td class="team">${link(m.best_model)}${m.best_model ? ` (${fmt(m.best_model.powder_score, 1)})` : ""}</td>
      <td>${m.rounds}</td>
    </tr>`).join("");
}

const METRIC = {
  swe: { key: "swe_accuracy", label: "SWE Accuracy", days: "swe_rounds",
    note: "Ranked by <strong>SWE Accuracy</strong>: how much smaller the misses in the snow's <em>water</em> are than guessing the usual amount (at 10:1). Scored where stations weigh the snow (SNOTEL), for forecasters that send water amounts. Higher is better." },
  density: { key: "density_accuracy", label: "Density Accuracy", days: "swe_rounds",
    note: "Ranked by <strong>Density Accuracy</strong>: how well the forecast called light vs heavy snow (water ÷ depth), compared with always assuming the classic 10:1 ratio. Scored on days with 2″+ of new snow. Higher is better." },
};
let activeMetric = "depth";

function renderMetricBoard(entries) {
  const m = METRIC[activeMetric];
  const thead = document.querySelector("#board thead tr");
  thead.innerHTML = `<th>#</th><th>Forecaster</th><th>${m.label} ⓘ</th><th title="Rounds scored on this track">Days</th>`;
  const rows = (entries || []).filter((t) => t[m.key] !== null && t[m.key] !== undefined)
    .sort((a, b) => b[m.key] - a[m.key]);
  const tbody = document.querySelector("#board tbody");
  tbody.innerHTML = rows.length ? rows.map((t, i) => `<tr class="${t.is_baseline ? "baseline" : ""}">
      <td>${i + 1}</td><td class="team">${prettyTeam(t.team)} ${kindTag(t.team)}</td>
      <td class="${t[m.key] > 0 ? "score-pos" : "score-neg"}">${fmt(t[m.key], 1)}</td><td>${t[m.days] ?? "—"}</td></tr>`).join("")
    : `<tr><td colspan="4" style="text-align:center;color:var(--muted)">No forecaster has been scored on this yet.
        Forecasts that include water amounts (<code>swe_in</code>) are scored once their rounds resolve.</td></tr>`;
}

let depthHead = null;
function renderCurrentBoard() {
  const thead = document.querySelector("#board thead tr");
  if (!depthHead) depthHead = thead.innerHTML;
  const note = document.getElementById("metric-note");
  if (activeMetric !== "depth") {
    note.innerHTML = METRIC[activeMetric].note; note.style.display = "";
    renderMetricBoard(state.lb[activeWindow()] || state.lb.season);
    return;
  }
  note.style.display = "none";
  thead.innerHTML = depthHead;
  const track = document.getElementById("board-track").value;
  if (track) renderBoard((state.lb.tracks || {})[track], state.lb.generated_rounds);
  else renderBoard(state.lb[activeWindow()], state.lb.generated_rounds);
}

function activeWindow() {
  return document.querySelector(".tab.active[data-window]")?.dataset.window || "season";
}

async function selectLeague(name) {
  state.league = name;
  if (location.hash.startsWith("#league=")) history.replaceState(null, "", `#league=${name}`);
  renderLeagueBar();
  const lg = state.leagues.find((l) => l.name === name);
  document.getElementById("truth-note").textContent = lg.truth_note || TRUTH_NOTES[lg.truth_source] || "";
  renderHealth(lg);

  state.fc = null;
  try { state.fc = await loadJSON(`data/forecasts-${name}.json`); } catch (e) { /* none yet */ }
  try { state.snowfall = await loadJSON(`data/snowfall-${name}.json`); } catch (e) { state.snowfall = {}; }
  try {
    state.stations = await loadJSON(`data/stations-${name}.json`);
    renderMap(state.stations);
  } catch (e) { state.stations = []; }

  state.lb = { season: [], last30: [], generated_rounds: 0 };
  try { state.lb = await loadJSON(`data/leaderboard-${name}.json`); } catch (e) { /* pre-season */ }
  renderHall(state.lb.months);
  renderTrackOptions();
  renderCurrentBoard();

  // "Nobody is beating the models. Yet." — flip the line if someone actually is.
  const ranked = (state.lb.season || []).filter((t) => t.eligible && !t.is_baseline);
  const bestModel = (state.lb.season || []).filter((t) => t.team.startsWith("baseline-"))
    .reduce((a, b) => (a && a.powder_score > b.powder_score ? a : b), null);
  document.getElementById("challenge-line").textContent =
    ranked.length && bestModel && ranked[0].powder_score > bestModel.powder_score
      ? `${ranked[0].team} is beating the weather models. Can you?`
      : "Nobody is beating the weather models. Yet.";

  try { renderRounds(await loadJSON(`data/recent_rounds-${name}.json`)); } catch (e) { renderRounds([]); }

  state.history = [];
  try { state.history = await loadJSON(`data/history-${name}.json`); } catch (e) { /* none yet */ }
  state.snowfall = {};
  try { state.snowfall = await loadJSON(`data/snowfall-${name}.json`); } catch (e) { /* none yet */ }
  state.climo = {};
  try { state.climo = await loadJSON(`data/climo-${name}.json`); } catch (e) { /* none yet */ }
  renderRace(state.history);
  renderSnowfall();
  renderClimo();
  renderArchive(state.history);
  initSeasons(name);
}

function renderStorms(events) {
  const section = document.getElementById("storms");
  if (!events || !events.length) { section.style.display = "none"; return; }
  section.style.display = "";
  const list = document.getElementById("storm-list");
  list.innerHTML = "";
  for (const e of events.slice(0, 6)) {
    const div = document.createElement("div");
    div.className = "storm-card";
    const when = e.start === e.end ? e.start : `${e.start} → ${e.end}`;
    let body = e.status === "open"
      ? `<p>Open · cutoff ${e.cutoff_utc.replace("T", " ").slice(0, 16)} UTC · ${e.n_stations} stations ·
           submit <code>data/submissions/events/${e.event_id}/&lt;team&gt;.csv</code> (station_id, snowfall_in)</p>`
      : `<p>${e.biggest_total ? `Biggest: ${e.biggest_total.inches}″ at ${e.biggest_total.station_id}` : "Scored"}</p>`;
    if (e.board) {
      body += "<table><thead><tr><th>Team</th><th>Powder Accuracy</th><th>MAE</th></tr></thead><tbody>" +
        e.board.slice(0, 8).map((b) => `<tr><td class="team">${b.team}</td><td>${fmt(b.powder_score, 1)}</td><td>${fmt(b.mae)}</td></tr>`).join("") +
        "</tbody></table>";
    }
    div.innerHTML = `<h3>${e.title} <span class="badge">${e.league}</span></h3><p class="fine">${when}</p>${body}`;
    list.appendChild(div);
  }
}

function renderHealth(lg) {
  const el = document.getElementById("data-health");
  if (!el) return;
  const h = lg.health || {};
  const bits = [];
  if (h.last_round) bits.push(`Last scored round ${h.last_round}: ${Math.round(h.qc_valid_share * 100)}% of station-horizons passed QC`);
  if (h.latest_scrape) {
    const st = h.latest_scrape.status;
    const parts = Object.entries(st).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${v} ${k.replace("_", " ")}`);
    bits.push(`Latest scrape ${h.latest_scrape.at.slice(0, 16).replace("T", " ")} UTC: ${parts.join(" · ")}`);
  }
  el.textContent = bits.length ? "Data health — " + bits.join(" · ") : "";
}

function renderHeroStats(leagues) {
  const sum = (k) => leagues.reduce((a, l) => a + (l[k] || 0), 0);
  const set = (id, v) => { const el = document.getElementById(id); if (el && v) el.textContent = v.toLocaleString(); };
  set("stat-stations", sum("n_stations"));
  set("stat-leagues", leagues.length);
  set("stat-rounds", sum("n_rounds_resolved"));
}

(async () => {
  try { renderStorms(await loadJSON("data/events.json")); } catch (e) { /* no events yet */ }
  try {
    state.leagues = await loadJSON("data/leagues.json");
    renderHeroStats(state.leagues);
  } catch (e) {
    state.leagues = [{ name: "stations", label: "Stations", status: "live", truth_source: "snotel" }];
  }
  document.querySelectorAll(".tab").forEach((btn) =>
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById("board-track").value = "";
      renderCurrentBoard();
    })
  );
  document.getElementById("board-track").addEventListener("change", renderCurrentBoard);
  document.querySelectorAll(".mtab").forEach((btn) =>
    btn.addEventListener("click", () => {
      document.querySelectorAll(".mtab").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      activeMetric = btn.dataset.metric;
      renderCurrentBoard();
    })
  );
  document.getElementById("station-search").addEventListener("change", (e) => findStation(e.target.value));
  try { sznArchive = await loadJSON("data/seasons/southern-stations/index.json"); } catch (e) { /* optional */ }
  const fromHash = location.hash.match(/^#league=(\w+)$/)?.[1];
  const initial =
    state.leagues.find((l) => l.name === fromHash) ||
    state.leagues.find(leagueActive) ||
    state.leagues[0];
  await selectLeague(initial.name);
  try { initComparison(await loadJSON("data/resort-vs-era5.json")); } catch (e) {
    document.getElementById("receipts").style.display = "none";
  }
})();

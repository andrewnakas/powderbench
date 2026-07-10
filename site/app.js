/* PowderBench site: league-aware leaderboard, charts, rounds, station map. */

const fmt = (v, digits = 2) => (v === null || v === undefined ? "—" : Number(v).toFixed(digits));

const TRUTH_NOTES = {
  snotel:
    "Ground truth comes from NRCS SNOTEL telemetry — automated snow pillows and depth " +
    "sensors high in the mountains, QC’d and voided (for everyone) when sensors glitch. " +
    "Hover a marker for the station behind each zone.",
  era5:
    "Truth is ERA5 model analysis at each resort’s coordinates (no public " +
    "southern-hemisphere station API exists — real feeds get promoted as they become " +
    "available). Scores resolve about a week after each round.",
  resort:
    "Truth is each resort’s own published snow report, archived twice daily from the " +
    "resort’s site. Resort numbers run generous — but every team is scored against the " +
    "same report, so it’s a fair fight; cross-check the ERA5 league for the reanalysis view.",
};

const PALETTE = ["#7cc4ff", "#7ee2a8", "#ffd28a", "#ff9e9e", "#c9a7ff", "#8ef0e4", "#f0b6d8", "#b3e5ff"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

let map = null;
let markerLayer = null;
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

function renderLeagueBar() {
  const bar = document.getElementById("league-bar");
  bar.innerHTML = "";
  for (const lg of state.leagues) {
    const btn = document.createElement("button");
    btn.className = "league-tab" + (lg.name === state.league ? " active" : "");
    btn.innerHTML =
      lg.label + (lg.status === "trial" ? ' <span class="badge">TRIAL</span>' : "");
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
    if (!t.eligible) tr.classList.add("unranked");
    const ps = t.powder_score;
    const psClass = ps === null ? "" : ps > 0 ? "score-pos" : "score-neg";
    tr.innerHTML = `
      <td>${t.rank ?? "–"}</td>
      <td class="team">${t.team}</td>
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

/* ---- The race: season-to-date mean Powder Score per team ---- */
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
        y: { title: { display: true, text: "Powder Score, season-to-date" } },
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

function renderMap(stations) {
  if (!map) {
    map = L.map("map", { scrollWheelZoom: false });
    L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>',
      maxZoom: 12,
    }).addTo(map);
  }
  if (markerLayer) markerLayer.remove();
  markerLayer = L.layerGroup();
  const pts = [];
  for (const s of stations) {
    pts.push([s.latitude, s.longitude]);
    L.circleMarker([s.latitude, s.longitude], {
      radius: 6, color: "#7cc4ff", weight: 1.5, fillColor: "#7cc4ff", fillOpacity: 0.5,
    })
      .bindPopup(
        `<strong>${s.resort}</strong><br>${s.name} · ${s.station_id}<br>` +
        `${s.region} · ${Math.round(s.elevation_ft).toLocaleString()} ft`
      )
      .addTo(markerLayer);
  }
  markerLayer.addTo(map);
  if (pts.length) map.fitBounds(L.latLngBounds(pts).pad(0.15));
}

function activeWindow() {
  return document.querySelector(".tab.active")?.dataset.window || "season";
}

async function selectLeague(name) {
  state.league = name;
  if (location.hash.startsWith("#league=")) history.replaceState(null, "", `#league=${name}`);
  renderLeagueBar();
  const lg = state.leagues.find((l) => l.name === name);
  document.getElementById("truth-note").textContent = TRUTH_NOTES[lg.truth_source] || "";

  try {
    state.stations = await loadJSON(`data/stations-${name}.json`);
    renderMap(state.stations);
  } catch (e) { state.stations = []; }

  state.lb = { season: [], last30: [], generated_rounds: 0 };
  try { state.lb = await loadJSON(`data/leaderboard-${name}.json`); } catch (e) { /* pre-season */ }
  renderBoard(state.lb[activeWindow()], state.lb.generated_rounds);

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
}

(async () => {
  try {
    state.leagues = await loadJSON("data/leagues.json");
  } catch (e) {
    state.leagues = [{ name: "stations", label: "Stations", status: "live", truth_source: "snotel" }];
  }
  document.querySelectorAll(".tab").forEach((btn) =>
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      renderBoard(state.lb[btn.dataset.window], state.lb.generated_rounds);
    })
  );
  const fromHash = location.hash.match(/^#league=(\w+)$/)?.[1];
  const initial = state.leagues.find((l) => l.name === fromHash) || state.leagues[0];
  await selectLeague(initial.name);
  try { initComparison(await loadJSON("data/resort-vs-era5.json")); } catch (e) {
    document.getElementById("receipts").style.display = "none";
  }
})();

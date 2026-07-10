/* PowderBench site: league-aware leaderboard, recent rounds, station map. */

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

let map = null;
let markerLayer = null;
let state = { league: null, leagues: [], lb: null };

async function loadJSON(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
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

  try { renderMap(await loadJSON(`data/stations-${name}.json`)); } catch (e) { /* keep old */ }

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
})();

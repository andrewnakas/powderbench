/* PowderBench site: render leaderboard, recent rounds, station map from static JSON. */

const fmt = (v, digits = 2) => (v === null || v === undefined ? "—" : Number(v).toFixed(digits));

async function loadJSON(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

function renderBoard(entries, note, rounds) {
  const tbody = document.querySelector("#board tbody");
  tbody.innerHTML = "";
  document.getElementById("rounds-note").textContent =
    rounds ? `${rounds} rounds resolved` : "";
  if (!entries || !entries.length) {
    tbody.innerHTML = `<tr><td colspan="8" style="text-align:center;color:var(--muted)">
      No resolved rounds yet — the season is coming. Run the training camp meanwhile.</td></tr>`;
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
    card.innerHTML = `
      <div class="date">${r.round_id}</div>
      <div class="snow">${r.qc.station_horizons_voided} voided / ${r.qc.station_horizons_valid} scored</div>
      <div class="who">${best ? `best: ${best[0]} (${fmt(best[1].powder_score, 1)})` : "no ranked teams"}</div>`;
    strip.appendChild(card);
  }
  if (!rounds.length) strip.innerHTML = `<p class="fine">No rounds resolved yet.</p>`;
}

function renderMap(stations) {
  const map = L.map("map", { scrollWheelZoom: false }).setView([42.5, -113], 5);
  L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>',
    maxZoom: 12,
  }).addTo(map);
  for (const s of stations) {
    L.circleMarker([s.latitude, s.longitude], {
      radius: 6, color: "#7cc4ff", weight: 1.5, fillColor: "#7cc4ff", fillOpacity: 0.5,
    })
      .addTo(map)
      .bindPopup(
        `<strong>${s.resort}</strong><br>${s.name} · ${s.station_id}<br>` +
        `${s.region} · ${Math.round(s.elevation_ft).toLocaleString()} ft`
      );
  }
}

(async () => {
  try {
    const stations = await loadJSON("data/stations.json");
    renderMap(stations);
  } catch (e) {
    document.getElementById("map").outerHTML = "<p class='fine'>Map data unavailable.</p>";
  }

  let lb = { season: [], last30: [], generated_rounds: 0 };
  try { lb = await loadJSON("data/leaderboard.json"); } catch (e) { /* pre-season */ }
  renderBoard(lb.season, "", lb.generated_rounds);
  document.querySelectorAll(".tab").forEach((btn) =>
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      renderBoard(lb[btn.dataset.window], "", lb.generated_rounds);
    })
  );

  // "Nobody is beating the models. Yet." — flip the line if someone actually is.
  const ranked = (lb.season || []).filter((t) => t.eligible && !t.is_baseline);
  const bestModel = (lb.season || []).filter((t) => t.team.startsWith("baseline-"))
    .reduce((a, b) => (a && a.powder_score > b.powder_score ? a : b), null);
  if (ranked.length && bestModel && ranked[0].powder_score > bestModel.powder_score) {
    document.getElementById("challenge-line").textContent =
      `${ranked[0].team} is beating the weather models. Can you?`;
  }

  try { renderRounds(await loadJSON("data/recent_rounds.json")); } catch (e) { /* ok */ }
})();

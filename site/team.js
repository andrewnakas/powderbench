/* PowderBench team page: team.html?t=<team> */

const fmt = (v, d = 1) => (v === null || v === undefined ? "—" : Number(v).toFixed(d));
const signed = (v) => (v === null || v === undefined ? "—" : (v > 0 ? "+" : "") + Number(v).toFixed(1));
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const KIND = {
  baseline: "Reference baseline: a weather model or no-skill forecast, run automatically by PowderBench.",
  house: "House agent: a forecasting strategy run by the maintainers, using only pre-cutoff information.",
  player: "Competitor.",
};

if (window.Chart) {
  Chart.defaults.color = "#93a4c3";
  Chart.defaults.borderColor = "#23324f";
}

function leagueSection(name, lg, team) {
  const s = lg.season, l30 = lg.last30;
  const rank = s.eligible && s.rank ? `#${s.rank} of ${lg.n_teams}` : "unranked";
  const h2h = Object.entries(lg.h2h)
    .sort((a, b) => b[1].of - a[1].of)
    .map(([opp, r]) => `<tr><td>${esc(opp)}</td><td>${r.won} of ${r.of}</td><td>${Math.round((100 * r.won) / r.of)}%</td></tr>`)
    .join("");
  const badge = `badges/${encodeURIComponent(team)}-${name}.svg`;
  const md = `[![PowderBench](https://powderbench.com/${badge})](https://powderbench.com/team.html?t=${encodeURIComponent(team)})`;
  const sec = document.createElement("section");
  sec.innerHTML = `
    <h2>${esc(lg.label)}</h2>
    <div class="stats">
      <div class="stat"><span class="stat-n">${signed(s.powder_score)}</span><span class="stat-l">Powder Score, season</span></div>
      <div class="stat"><span class="stat-n">${rank}</span><span class="stat-l">${s.eligible ? "season rank" : "needs ≥5 rounds at ≥70% coverage"}</span></div>
      <div class="stat"><span class="stat-n">${signed(l30?.powder_score)}</span><span class="stat-l">last 30 rounds</span></div>
      <div class="stat"><span class="stat-n">${s.rounds}</span><span class="stat-l">${s.rounds === 1 ? "round" : "rounds"} · MAE ${fmt(s.mae, 2)}″</span></div>
    </div>
    <div class="chart-box"><canvas id="c-${name}" height="110"></canvas></div>
    ${h2h ? `<h3>Head to head</h3><p class="fine">Rounds this team scored a higher Powder Score than each reference, on rounds both played.</p>
      <table><thead><tr><th>Against</th><th>Won</th><th>Share</th></tr></thead><tbody>${h2h}</tbody></table>` : ""}
    <h3>Badge</h3>
    <p><img src="${badge}" alt="PowderBench badge"></p>
    <pre><code>${esc(md)}</code></pre>`;
  document.getElementById("team-main").appendChild(sec);
  if (window.Chart && lg.rounds.length) {
    const xs = lg.rounds.map((r) => r[0]);
    const ys = lg.rounds.map((r) => r[1]);
    const roll = ys.map((_, i) => {
      const w = ys.slice(Math.max(0, i - 9), i + 1);
      return w.reduce((a, b) => a + b, 0) / w.length;
    });
    new Chart(document.getElementById(`c-${name}`), {
      type: "line",
      data: {
        labels: xs,
        datasets: [
          { label: "Round Powder Score", data: ys, showLine: false, pointRadius: 2, borderColor: "#5b7bb0" },
          { label: "10-round mean", data: roll, pointRadius: 0, borderWidth: 2, borderColor: "#7cc4ff" },
        ],
      },
      options: { scales: { y: { suggestedMin: -50, suggestedMax: 60 } }, plugins: { legend: { position: "bottom" } } },
    });
  }
}

async function main() {
  const team = new URLSearchParams(location.search).get("t");
  const status = document.getElementById("team-status");
  if (!team) {
    status.textContent = "No team given. Open a team from the leaderboard.";
    return;
  }
  document.getElementById("team-name").textContent = team;
  document.title = `${team} — PowderBench`;
  let teams;
  try {
    teams = await (await fetch("data/teams.json", { cache: "no-cache" })).json();
  } catch {
    status.textContent = "Could not load team data.";
    return;
  }
  const prof = teams[team];
  if (!prof) {
    status.textContent = "This team has no scored rounds yet. Scores appear after its first round resolves.";
    return;
  }
  status.remove();
  document.getElementById("team-kind").textContent = KIND[prof.kind] || "";
  const order = Object.entries(prof.leagues).sort((a, b) => b[1].season.rounds - a[1].season.rounds);
  for (const [name, lg] of order) leagueSection(name, lg, team);
}

main();

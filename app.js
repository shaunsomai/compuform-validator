const state = {
  manifest: null,
  data: null,
  sourceId: "",
  section: "overview",
  raceNumber: "all",
  runnerKey: "all",
  search: "",
  jsonScope: "current",
};

const els = {};

document.addEventListener("DOMContentLoaded", init);

async function init() {
  cacheElements();
  bindEvents();
  await loadManifest();
}

function cacheElements() {
  els.sourceSelect = document.querySelector("#sourceSelect");
  els.reloadButton = document.querySelector("#reloadButton");
  els.raceSelect = document.querySelector("#raceSelect");
  els.runnerSelect = document.querySelector("#runnerSelect");
  els.searchInput = document.querySelector("#searchInput");
  els.jsonScopeSelect = document.querySelector("#jsonScopeSelect");
  els.status = document.querySelector("#status");
  els.stats = document.querySelector("#stats");
  els.content = document.querySelector("#content");
  els.navItems = [...document.querySelectorAll(".nav-item")];
}

function bindEvents() {
  els.sourceSelect.addEventListener("change", async (event) => {
    state.sourceId = event.target.value;
    state.raceNumber = "all";
    state.runnerKey = "all";
    await loadSource();
  });

  els.reloadButton.addEventListener("click", async () => {
    await loadSource();
  });

  els.raceSelect.addEventListener("change", (event) => {
    state.raceNumber = event.target.value;
    state.runnerKey = "all";
    populateRunnerSelect();
    render();
  });

  els.runnerSelect.addEventListener("change", (event) => {
    state.runnerKey = event.target.value;
    render();
  });

  els.searchInput.addEventListener("input", (event) => {
    state.search = event.target.value.trim().toLowerCase();
    render();
  });

  els.jsonScopeSelect.addEventListener("change", (event) => {
    state.jsonScope = event.target.value;
    render();
  });

  els.navItems.forEach((button) => {
    button.addEventListener("click", () => {
      state.section = button.dataset.section;
      els.navItems.forEach((item) => item.classList.toggle("is-active", item === button));
      render();
    });
  });
}

async function loadManifest() {
  try {
    const response = await fetch("site_manifest.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`Manifest request failed: ${response.status}`);
    state.manifest = await response.json();
    populateSourceSelect();
    state.sourceId = state.manifest.sources[0]?.id || "";
    await loadSource();
  } catch (error) {
    setStatus(`Could not load site_manifest.json. Serve this folder with a local web server. ${error.message}`, "danger");
  }
}

function populateSourceSelect() {
  els.sourceSelect.innerHTML = state.manifest.sources
    .map((source) => `<option value="${escapeHtml(source.id)}">${escapeHtml(source.label)}</option>`)
    .join("");
}

async function loadSource() {
  const source = currentSource();
  if (!source) return;

  setStatus(`Loading ${source.label}...`);
  try {
    const response = await fetch(source.json_path, { cache: "no-store" });
    if (!response.ok) throw new Error(`JSON request failed: ${response.status}`);
    state.data = await response.json();
    els.sourceSelect.value = source.id;
    populateRaceSelect();
    populateRunnerSelect();
    setStatus(`Loaded ${source.label} from ${source.json_path}`);
    render();
  } catch (error) {
    state.data = null;
    setStatus(`Could not load ${source.json_path}. ${error.message}`, "danger");
    els.stats.innerHTML = "";
    els.content.innerHTML = "";
  }
}

function currentSource() {
  return state.manifest?.sources.find((source) => source.id === state.sourceId);
}

function setStatus(message, level = "") {
  els.status.className = `status ${level}`;
  els.status.textContent = message;
}

function populateRaceSelect() {
  const races = state.data?.races || [];
  els.raceSelect.innerHTML = [
    '<option value="all">All races</option>',
    ...races.map((race) => `<option value="${escapeHtml(race.race_number)}">Race ${escapeHtml(race.race_number)} - ${escapeHtml(race.race_time)} ${escapeHtml(race.distance)}</option>`),
  ].join("");
  els.raceSelect.value = state.raceNumber;
}

function populateRunnerSelect() {
  const runners = filteredByRace(state.data?.runners || []);
  els.runnerSelect.innerHTML = [
    '<option value="all">All runners</option>',
    ...runners.map((runner) => {
      const key = runnerKey(runner);
      return `<option value="${escapeHtml(key)}">R${escapeHtml(runner.race_number)} #${escapeHtml(runner.horse_number)} ${escapeHtml(runner.horse_name)}</option>`;
    }),
  ].join("");
  els.runnerSelect.value = runners.some((runner) => runnerKey(runner) === state.runnerKey) ? state.runnerKey : "all";
  state.runnerKey = els.runnerSelect.value;
}

function render() {
  if (!state.data) return;
  renderStats();
  const renderers = {
    overview: renderOverview,
    races: renderRaces,
    runners: renderRunners,
    ratings: renderRatings,
    betting: renderBetting,
    past: renderPastRuns,
    collateral: renderCollateral,
    validation: renderValidation,
    raw: renderRawJson,
  };
  els.content.innerHTML = renderers[state.section]();
  bindDynamicActions();
}

function renderStats() {
  const data = state.data;
  const runnerCount = data.runners.length;
  const pastRows = data.runners.reduce((total, runner) => total + runner.past_runs.length, 0);
  const collateralRows = data.runners.reduce((total, runner) => total + runner.collateral_formlines.length, 0);
  const metrics = [
    ["Racecourse", data.meeting.racecourse],
    ["Date", data.meeting.date],
    ["Races", data.validation.races_found],
    ["Runners", runnerCount],
    ["Past runs", pastRows],
    ["Unclear", data.validation.unclear_fields.length],
    ["Collateral", collateralRows],
    ["Missing", data.validation.missing_fields.length],
  ];
  els.stats.innerHTML = metrics.map(([label, value]) => metric(label, value)).join("");
}

function renderOverview() {
  const data = state.data;
  const runnerCounts = data.validation.runners_found_by_race || [];
  return panel(
    "Meeting overview",
    "Use the filters above, then switch sections to inspect race, runner, ratings, betting, and raw extraction records.",
    `${detailGrid([
      ["Racecourse", data.meeting.racecourse],
      ["Date", `${data.meeting.day} ${data.meeting.date}`],
      ["Surface", data.meeting.surface],
      ["Track", data.meeting.track],
      ["First race", data.meeting.first_race_time],
      ["Pages", data.validation.pages_processed],
      ["Source PDF", data.meeting.source_pdf],
      ["Draw notes", data.meeting.draw_bias_notes],
    ])}
    <h3>Runner Counts By Race</h3>
    ${table(runnerCounts, [
      ["race_number", "Race"],
      ["runner_count", "Runners"],
    ])}
    <h3>Warnings</h3>
    ${list(data.validation.warnings)}`
  );
}

function renderRaces() {
  const races = applySearch(filteredByRace(state.data.races));
  const selected = currentRace();
  const raceDetail = selected ? `
    <h3>Selected Race Detail</h3>
    ${detailGrid([
      ["Race", selected.race_number],
      ["Time", selected.race_time],
      ["Name", selected.race_name],
      ["Distance", selected.distance],
      ["Distance category", selected.distance_category],
      ["Turn", selected.turn],
      ["TAB bet types", selected.tab_bet_types],
      ["Stake", selected.stake],
      ["Prize breakdown", selected.prize_breakdown],
      ["RCIS", selected.rcis],
      ["Ref", selected.race_ref],
      ["Class", selected.race_class],
      ["Avg MR", selected.average_merit_rating],
      ["Class avg per metre", selected.class_average_per_metre],
      ["WFA", selected.wfa],
      ["Same trainer", selected.same_trainer_notes || "None"],
    ])}
    <h3>Betting Legs</h3>
    ${keyValueTable(selected.betting_legs || {})}
    <h3>Tipster Selections</h3>
    ${list(selected.tipster_selections)}
    <h3>Preview</h3>
    <p>${escapeHtml(selected.preview || "No preview extracted.")}</p>
    <h3>Draw Stats</h3>
    ${list(selected.draw_stats)}
  ` : "";
  return panel(
    "Race-level data",
    `${races.length} race rows match the current filters.`,
    `${table(races, [
      ["race_number", "Race"],
      ["race_time", "Time"],
      ["race_name", "Race name"],
      ["race_type", "Type"],
      ["race_class", "Class"],
      ["distance", "Distance"],
      ["distance_category", "DC"],
      ["turn", "Turn"],
      ["surface", "Surface"],
      ["stake", "Stake"],
      ["rcis", "RCIS"],
      ["race_ref", "Ref"],
      ["average_merit_rating", "Avg MR"],
      ["average_first3_percentage", "Avg first 3"],
      ["class_average_time", "Class avg"],
      ["class_average_per_metre", "Class avg/m"],
      ["wfa", "WFA"],
    ])}${raceDetail}`
  );
}

function renderRunners() {
  const runners = applySearch(filteredByRunner(filteredByRace(state.data.runners)));
  const selected = currentRunner();
  return panel(
    "Runner-level data",
    `${runners.length} runner rows match the current filters. Select a horse to inspect the joined profile fields.`,
    `${table(runners, [
      ["horse_name", "Horse", runnerLink],
      ["race_number", "Race"],
      ["horse_number", "No"],
      ["draw", "Draw"],
      ["last_3_runs", "L3"],
      ["runs_wins_places", "Runs"],
      ["first3_percentage", "F3"],
      ["age_colour_sex", "ACS"],
      ["weight", "Wgt"],
      ["allowance", "Allow"],
      ["shoes", "Shoes"],
      ["trainer", "Trainer"],
      ["trainer_win_percentage", "T%"],
      ["jockey", "Jockey"],
      ["jockey_win_percentage", "J%"],
      ["hmerit_rating", "HMR"],
      ["cmerit_rating", "CMR"],
      ["computaform_rating", "CF"],
      ["speed_rating", "Speed"],
      ["race_rating", "RR"],
      ["headgear_change", "Headgear"],
      ["forecast_price", "Fcst"],
      ["form_comment", "Comment"],
    ])}
    ${selected ? renderRunnerDetail(selected) : ""}`
  );
}

function renderRunnerDetail(runner) {
  return `
    <h3>Selected Runner Detail</h3>
    ${detailGrid([
      ["Horse", runner.horse_name],
      ["Race/No", `Race ${runner.race_number} / ${runner.horse_number}`],
      ["Age/colour/sex", runner.age_colour_sex],
      ["Draw", runner.draw],
      ["Weight", runner.weight],
      ["Allowance", runner.allowance],
      ["Forecast", runner.forecast_price],
      ["Shoes", runner.shoes],
      ["CF rating", runner.computaform_rating],
      ["Speed rating", runner.speed_rating],
      ["Race rating", runner.race_rating],
      ["Best weighted", runner.best_weighted_rating],
      ["Best vs avg", runner.best_vs_average],
      ["Days since run", runner.days_since_last_race],
      ["Days since win", runner.days_since_last_win],
      ["Equipment", runner.equipment],
      ["Headgear", runner.headgear_change],
      ["Owner", runner.owner],
    ])}
    <div class="split-grid">
      <section>
        <h3>Career Record</h3>
        ${keyValueTable(runner.career_record)}
      </section>
      <section>
        <h3>Breeding</h3>
        ${keyValueTable(runner.breeding)}
      </section>
    </div>
    ${runner.derived_exposure ? renderDerivedExposure(runner.derived_exposure) : ""}
    <h3>Past Runs</h3>
    ${table(runner.past_runs, pastRunColumns())}
    <h3>Collateral Formlines</h3>
    ${table(runner.collateral_formlines, [
      ["date", "Date"],
      ["course", "Course"],
      ["raw_text", "Raw row"],
    ])}
  `;
}

function renderDerivedExposure(exposure) {
  const summaryRows = [
    ["Method", exposure.method],
    ["Greyville poly starts", exposure.greyville_poly_starts_grp],
    ["Greyville turf starts", exposure.greyville_turf_starts_gry],
    ["Greyville total starts", exposure.greyville_total_starts],
    ["All poly starts", exposure.poly_starts_all_known_codes],
    ["All turf starts", exposure.turf_starts_all_known_codes],
    ["Known date/course pairs", exposure.known_date_course_pairs],
    ["Current race distance", exposure.current_race_distance],
    ["Distance-token matches", exposure.current_distance_token_matches],
    ["C&D alignment", exposure.course_and_distance_alignment],
  ].map(([label, value]) => ({ label, value }));
  const courseRows = Object.entries(exposure.course_code_counts || {}).map(([course, starts]) => ({ course, starts }));
  const surfaceRows = Object.entries(exposure.surface_counts || {}).map(([surface, starts]) => ({ surface, starts }));
  return `
    <h3>Derived Course/Surface Exposure</h3>
    ${table(summaryRows, [["label", "Field"], ["value", "Value"]])}
    <div class="split-grid">
      <section>
        <h3>Course Codes</h3>
        ${table(courseRows, [["course", "Code"], ["starts", "Starts"]])}
      </section>
      <section>
        <h3>Surface Counts</h3>
        ${table(surfaceRows, [["surface", "Surface"], ["starts", "Starts"]])}
      </section>
    </div>
  `;
}

function renderRatings() {
  const topRows = flattenRatings();
  const runnerMatrix = applySearch(filteredByRace(state.data.runners)).map((runner) => ({
    race_number: runner.race_number,
    horse_number: runner.horse_number,
    horse_name: runner.horse_name,
    official_merit_rating: runner.hmerit_rating,
    current_merit_rating: runner.cmerit_rating,
    computaform_rating: runner.computaform_rating,
    speed_rating: runner.speed_rating,
    race_rating: runner.race_rating,
    best_weighted_rating: runner.best_weighted_rating,
    top_first3_distance_category: runner.top_first3_distance_category,
    top_first3_track: runner.top_first3_track,
    best_vs_average: runner.best_vs_average,
  }));
  return panel(
    "Ratings tables",
    "Top-list ratings are shown first; the runner matrix shows every runner with profile-card ratings.",
    `<h3>Top Ratings Lists</h3>
    ${table(applySearch(filteredByRace(topRows)), [
      ["race_number", "Race"],
      ["source", "Source"],
      ["rating_type", "Type"],
      ["horse_number", "No"],
      ["horse_name", "Horse"],
      ["value", "Value"],
    ])}
    <h3>Runner Rating Matrix</h3>
    ${table(runnerMatrix, [
      ["race_number", "Race"],
      ["horse_number", "No"],
      ["horse_name", "Horse"],
      ["official_merit_rating", "HMR"],
      ["current_merit_rating", "CMR"],
      ["computaform_rating", "CF"],
      ["speed_rating", "Speed"],
      ["race_rating", "RR"],
      ["best_weighted_rating", "Best weighted"],
      ["top_first3_distance_category", "Top F3 DC"],
      ["top_first3_track", "Top F3 track"],
      ["best_vs_average", "Best vs avg"],
    ])}`
  );
}

function renderBetting() {
  const bestRows = [
    ["Today best bet", state.data.betting.today_best_bet],
    ["Today top value", state.data.betting.today_top_value],
    ["Best swinger", state.data.betting.best_swinger],
    ["Best exacta", state.data.betting.best_exacta],
    ["Best trifecta", state.data.betting.best_trifecta],
    ["Best quartet", state.data.betting.best_quartet],
  ].map(([label, value]) => ({ label, value }));
  const permRows = ["bipot", "place_accumulator", "pick6", "jackpot1", "jackpot2"].flatMap((bet) =>
    state.data.betting[bet].map((leg) => ({
      bet,
      leg: leg.leg,
      race_number: leg.race_number,
      selections: leg.selections.join(" "),
    }))
  );
  return panel(
    "Tipster and betting-permutation data",
    "Perm rows are mapped to the race sequence printed in the PDF.",
    `<div class="split-grid">
      <section>
        <h3>Best Bets</h3>
        ${table(bestRows, [["label", "Type"], ["value", "Selection"]])}
      </section>
      <section>
        <h3>Permutations</h3>
        ${table(filteredByRace(permRows), [
          ["bet", "Bet"],
          ["leg", "Leg"],
          ["race_number", "Race"],
          ["selections", "Selections"],
        ])}
      </section>
    </div>`
  );
}

function renderPastRuns() {
  const rows = applySearch(filteredByRunner(filteredByRace(state.data.runners)).flatMap((runner) => runner.past_runs));
  return panel(
    "Past-performance data",
    `${rows.length} past-performance rows match the current filters.`,
    table(rows, pastRunColumns())
  );
}

function pastRunColumns() {
  return [
    ["race_number", "Race"],
    ["horse_number", "No"],
    ["horse_name", "Horse"],
    ["date_marker", "Mark"],
    ["date", "Date"],
    ["course", "Course"],
    ["going", "Going"],
    ["ref", "Ref"],
    ["race_class_stake", "Race/Class/St"],
    ["average_merit_rating", "AR"],
    ["distance", "Dist"],
    ["straight_or_turn", "S/T"],
    ["shoes_headgear", "SH"],
    ["official_merit_rating", "MR"],
    ["jockey", "Jockey"],
    ["weight_allowance", "Wgt/Ad"],
    ["draw_runners", "Dr/Rn"],
    ["opening_betting", "OB"],
    ["starting_price", "SP"],
    ["position_800m", "800m Pos"],
    ["lengths_800m", "800m Lths"],
    ["position_400m", "400m Pos"],
    ["lengths_400m", "400m Lths"],
    ["finish_position", "Fin"],
    ["finish_length", "Len"],
    ["winner_or_second", "Winner/2nd"],
    ["winner_weight", "Wgt"],
    ["winner_time", "WTime"],
    ["final_400", "400F"],
    ["finish_rank", "R"],
    ["horse_adjusted_vs_average", "H/Av"],
    ["adjusted_time_per_metre", "Adj/tpm"],
    ["speed_rating", "SR"],
    ["next_start_winners", "W/R"],
    ["comment", "Comment"],
    ["raw_text", "Raw row"],
  ];
}

function renderCollateral() {
  const rows = applySearch(filteredByRunner(filteredByRace(state.data.runners)).flatMap((runner) => runner.collateral_formlines));
  return panel(
    "Collateral formlines",
    `${rows.length} collateral rows match the current filters.`,
    table(rows, [
      ["race_number", "Race"],
      ["horse_name", "Horse"],
      ["date", "Date"],
      ["course", "Course"],
      ["raw_text", "Raw row"],
    ])
  );
}

function renderValidation() {
  const validation = state.data.validation;
  return panel(
    "Validation checks",
    "Warnings and unclear fields are intentionally kept visible for manual review.",
    `<div class="split-grid">
      <section>
        <h3>Runner Counts</h3>
        ${table(validation.runners_found_by_race, [
          ["race_number", "Race"],
          ["runner_count", "Runners"],
        ])}
      </section>
      <section>
        <h3>Warnings</h3>
        ${list(validation.warnings)}
      </section>
    </div>
    <h3>Missing Fields</h3>
    ${table(validation.missing_fields, [["entity", "Entity"], ["race_number", "Race"], ["horse_number", "No"], ["horse_name", "Horse"], ["field", "Field"]])}
    <h3>Unclear Fields</h3>
    ${table(validation.unclear_fields, [["entity", "Entity"], ["race_number", "Race"], ["horse_number", "No"], ["horse_name", "Horse"], ["field", "Field"], ["reason", "Reason"]])}
    <h3>Possible OCR Errors</h3>
    ${list(validation.possible_ocr_errors)}`
  );
}

function renderRawJson() {
  const scope = selectedJsonScope();
  return panel(
    "Raw JSON",
    "This view is useful when you want to compare a row in the tables against the exact extracted object.",
    `<pre>${escapeHtml(JSON.stringify(scope, null, 2))}</pre>`
  );
}

function selectedJsonScope() {
  if (state.jsonScope === "full") return state.data;
  if (state.jsonScope === "meeting") return state.data.meeting;
  if (state.jsonScope === "validation") return state.data.validation;
  const runner = currentRunner();
  if (runner) return runner;
  const race = currentRace();
  if (race) return race;
  return {
    meeting: state.data.meeting,
    validation: state.data.validation,
  };
}

function bindDynamicActions() {
  document.querySelectorAll("[data-runner-key]").forEach((button) => {
    button.addEventListener("click", () => {
      state.runnerKey = button.dataset.runnerKey;
      els.runnerSelect.value = state.runnerKey;
      state.section = "runners";
      els.navItems.forEach((item) => item.classList.toggle("is-active", item.dataset.section === "runners"));
      render();
    });
  });
}

function currentRace() {
  if (state.raceNumber === "all") return null;
  return state.data.races.find((race) => race.race_number === state.raceNumber) || null;
}

function currentRunner() {
  if (state.runnerKey === "all") return null;
  return state.data.runners.find((runner) => runnerKey(runner) === state.runnerKey) || null;
}

function runnerKey(runner) {
  return `${runner.race_number}:${runner.horse_number}`;
}

function filteredByRace(rows) {
  if (state.raceNumber === "all") return rows;
  return rows.filter((row) => String(row.race_number) === state.raceNumber);
}

function filteredByRunner(rows) {
  if (state.runnerKey === "all") return rows;
  const selected = currentRunner();
  if (!selected) return rows;
  return rows.filter((row) => String(row.race_number) === selected.race_number && String(row.horse_number || "") === selected.horse_number);
}

function applySearch(rows) {
  if (!state.search) return rows;
  return rows.filter((row) => JSON.stringify(row).toLowerCase().includes(state.search));
}

function flattenRatings() {
  const groups = [
    ["computaform_ratings_by_race", "computaform"],
    ["speed_ratings_by_race", "speed"],
    ["best_weighted_by_race", "best_weighted"],
  ];
  const rows = groups.flatMap(([bucketName, source]) =>
    state.data.ratings[bucketName].flatMap((bucket) =>
      bucket.ratings.map((rating) => ({
        ...rating,
        source,
        race_number: bucket.race_number,
      }))
    )
  );
  return rows.concat(
    state.data.ratings.best_on_ratings.map((rating) => ({
      ...rating,
      source: "best_on_ratings",
      rating_type: "best_on_ratings",
      value: "selected",
    }))
  );
}

function panel(title, subtitle, body) {
  return `
    <article class="panel">
      <div class="panel-header">
        <h2>${escapeHtml(title)}</h2>
        <p>${escapeHtml(subtitle || "")}</p>
      </div>
      <div class="panel-body">${body}</div>
    </article>
  `;
}

function metric(label, value) {
  return `<div class="metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value ?? "")}</strong></div>`;
}

function detailGrid(items) {
  return `<div class="detail-grid">${items.map(([label, value]) => `
    <div class="detail-item">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value || "unclear")}</strong>
    </div>
  `).join("")}</div>`;
}

function keyValueTable(obj) {
  return table(
    Object.entries(obj).map(([key, value]) => ({ key, value })),
    [["key", "Field"], ["value", "Value"]]
  );
}

function table(rows, columns) {
  if (!rows || rows.length === 0) return '<p class="muted">No rows match the current selection.</p>';
  const head = columns.map(([, label]) => `<th>${escapeHtml(label)}</th>`).join("");
  const body = rows.map((row) => `<tr>${columns.map(([key, , formatter]) => `<td>${formatter ? formatter(row, key) : escapeHtml(row[key] ?? "")}</td>`).join("")}</tr>`).join("");
  return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function runnerLink(row) {
  return `<button class="row-button" type="button" data-runner-key="${escapeHtml(runnerKey(row))}">${escapeHtml(row.horse_name)}</button>`;
}

function list(items) {
  if (!items || items.length === 0) return '<p class="muted">No items extracted.</p>';
  return `<ul class="pill-list">${items.map((item) => `<li>${escapeHtml(typeof item === "string" ? item : JSON.stringify(item))}</li>`).join("")}</ul>`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

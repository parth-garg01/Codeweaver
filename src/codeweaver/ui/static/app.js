/*
 * CodeWeaver UI.
 *
 * No framework and no build step: the server ships this file as-is and its
 * Content-Security-Policy forbids inline script, so everything lives here.
 *
 * The UI holds no truth of its own. Every number it shows comes from an API
 * response, which is the same record the CLI prints — so the two cannot
 * disagree about whether a migration passed.
 */
"use strict";

const state = {
  project: "",
  outputDir: "",
  sourceLanguage: "python",
  target: "typescript",
  analysis: null,
  plan: null,
  report: null,
  files: [],
  selectedFile: null,
  selectedNode: null,
  execution: { allowTests: false, allowInstall: false },
};

/* -- tiny DOM helpers --------------------------------------------------- */

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function fill(parent, children) {
  parent.replaceChildren(...[].concat(children).filter(Boolean));
}

function emptyRow(columns, message) {
  return el("tr", {}, [el("td", { colspan: columns, class: "empty", text: message })]);
}

function setStatus(text, kind = "idle") {
  const chip = $("#status-chip");
  chip.className = `chip chip--${kind}`;
  chip.replaceChildren(
    kind === "busy" ? el("span", { class: "spinner", "aria-hidden": "true" }) : null,
    document.createTextNode(text)
  );
}

function showError(selector, message) {
  const node = $(selector);
  if (!node) return;
  if (!message) {
    node.hidden = true;
    node.textContent = "";
    return;
  }
  node.hidden = false;
  node.textContent = message;
}

/* -- API ---------------------------------------------------------------- */

async function api(path, { method = "POST", body = null, query = null } = {}) {
  const url = query ? `${path}?${new URLSearchParams(query)}` : path;
  const options = { method, headers: { "Content-Type": "application/json" } };
  if (method !== "GET") options.body = JSON.stringify(body || {});
  const response = await fetch(url, options);
  let payload = {};
  try {
    payload = await response.json();
  } catch (error) {
    throw new Error(`the server returned a non-JSON response (${response.status})`);
  }
  if (!response.ok) throw new Error(payload.error || `request failed (${response.status})`);
  return payload;
}

/* -- navigation --------------------------------------------------------- */

function enableTab(name, enabled) {
  const tab = $$(".tab").find((node) => node.dataset.view === name);
  if (tab) tab.disabled = !enabled;
}

function showView(name) {
  $$(".view").forEach((view) => view.classList.remove("view--active"));
  const target = $(`#view-${name}`);
  if (target) target.classList.add("view--active");
  $$(".tab").forEach((tab) => {
    if (tab.dataset.view === name) tab.setAttribute("aria-current", "page");
    else tab.removeAttribute("aria-current");
  });
}

/* -- project and languages ---------------------------------------------- */

async function loadLanguages() {
  const { matrix, execution } = await api("/api/languages", { method: "GET" });
  state.execution = execution;

  fill(
    $("#source-select"),
    matrix.sources.map((entry) =>
      el("option", { value: entry.language, text: `${entry.displayName} (${entry.language})` })
    )
  );
  fill(
    $("#target-select"),
    matrix.targets.map((entry) =>
      el("option", { value: entry.language, text: `${entry.displayName} (${entry.language})` })
    )
  );

  const rows = matrix.pairs.map((pair) =>
    el("tr", {}, [
      el("td", {}, [el("code", { text: pair.source })]),
      el("td", {}, [el("code", { text: pair.target })]),
      el("td", {}, [
        el("span", {
          class: `risk risk--${pair.runnable ? "low" : "high"}`,
          text: pair.runnable ? pair.level : `${pair.level} (unavailable)`,
        }),
      ]),
      el("td", { text: pair.notes || "—" }),
    ])
  );
  fill($("#pairs-table tbody"), rows.length ? rows : emptyRow(4, "No pairs are implemented."));

  fill(
    $("#toolchains-table tbody"),
    matrix.targets.map((entry) =>
      el("tr", {}, [
        el("td", {}, [el("code", { text: entry.language })]),
        el("td", { text: entry.requiredToolchains.join(", ") || "none" }),
      ])
    )
  );

  const limitations = [
    ...matrix.sources.flatMap((entry) => entry.knownLimitations),
    ...matrix.targets.flatMap((entry) => entry.knownLimitations),
  ];
  fill($("#limitations-list"), limitations.map((text) => el("li", { text })));

  $("#execution-summary").textContent = state.execution.allowTests || state.execution.allowInstall
    ? "This server was started with extra execution enabled."
    : "This server runs type checks only. Nothing from your project is executed.";
}

async function onScan(event) {
  event.preventDefault();
  showError("#project-error", "");
  state.project = $("#project-input").value.trim();
  state.sourceLanguage = $("#source-select").value;
  state.target = $("#target-select").value;
  if (!state.project) return;

  setStatus("analysing", "busy");
  try {
    state.analysis = await api("/api/analyze", {
      body: { project: state.project, sourceLanguage: state.sourceLanguage },
    });
    $("#project-label").textContent = state.analysis.root;
    $("#project-label").classList.remove("path--muted");
    renderOverview();
    renderGraph();
    ["overview", "dependencies"].forEach((name) => enableTab(name, true));
    showView("overview");
    setStatus("analysed", "ok");
    await loadPlan();
  } catch (error) {
    showError("#project-error", error.message);
    setStatus("failed", "error");
  }
}

/* -- overview ----------------------------------------------------------- */

function stat(label, value, kind = "") {
  return el("div", { class: `stat ${kind}` }, [
    el("div", { class: "stat__value", text: String(value) }),
    el("div", { class: "stat__label", text: label }),
  ]);
}

function renderOverview() {
  const analysis = state.analysis;
  const counts = analysis.counts;
  const gaps = analysis.modules.reduce((total, module) => total + module.unsupported, 0);
  fill($("#overview-stats"), [
    stat("files discovered", counts.discovered),
    stat("parsed", counts.parsed),
    stat("skipped", counts.skipped),
    stat("failed", counts.failed, counts.failed ? "stat--error" : ""),
    stat("symbols", analysis.symbols),
    stat("unsupported", gaps, gaps ? "stat--warn" : ""),
  ]);

  fill(
    $("#modules-table tbody"),
    analysis.modules.length
      ? analysis.modules.map((module) =>
          el("tr", {}, [
            el("td", {}, [el("code", { text: module.name })]),
            el("td", {}, [el("code", { text: module.path })]),
            el("td", { class: "num", text: module.functions }),
            el("td", { class: "num", text: module.classes }),
            el("td", { class: "num", text: module.unsupported || "" }),
          ])
        )
      : emptyRow(5, "No modules were parsed.")
  );

  const skipped = analysis.scan.skipped || [];
  fill(
    $("#skipped-table tbody"),
    skipped.length
      ? skipped.slice(0, 50).map((item) =>
          el("tr", {}, [
            el("td", {}, [el("code", { text: item.path })]),
            el("td", { text: item.detail ? `${item.reason} — ${item.detail}` : item.reason }),
          ])
        )
      : emptyRow(2, "Nothing was skipped.")
  );

  fill(
    $("#manifests-list"),
    (analysis.manifests || []).length
      ? analysis.manifests.map((manifest) =>
          el("li", {
            text: `${manifest.path}: ${manifest.projectName || "unnamed"} — ${
              manifest.dependencies.length
            } dependency/ies${manifest.parsed ? "" : " (unreadable)"}`,
          })
        )
      : [el("li", { class: "empty", text: "No manifests were detected." })]
  );

  renderDiagnostics($("#overview-diagnostics"), analysis.diagnostics);
}

function renderDiagnostics(container, diagnostics) {
  const items = diagnostics || [];
  if (!items.length) {
    fill(container, el("p", { class: "empty", text: "No diagnostics." }));
    return;
  }
  fill(
    container,
    items.slice(0, 200).map((item) =>
      el("div", { class: `diagnostic diagnostic--${item.severity}` }, [
        el("span", { class: "diagnostic__severity", text: item.severity }),
        el("span", {
          class: "diagnostic__where",
          text: item.span ? `${item.span.file}:${item.span.startLine}` : item.stage || "—",
        }),
        el("span", {}, [
          el("span", { text: `${item.code}: ${item.message}` }),
          item.hint ? el("span", { class: "diagnostic__hint", text: `hint: ${item.hint}` }) : null,
        ]),
      ])
    )
  );
}

/* -- dependency graph --------------------------------------------------- */

function activeEdgeKinds() {
  return new Set($$(".edge-filter").filter((box) => box.checked).map((box) => box.value));
}

function filteredEdges() {
  const kinds = activeEdgeKinds();
  const includeUnresolved = $("#unresolved-filter").checked;
  return (state.analysis.graph.edges || []).filter(
    (edge) => kinds.has(edge.kind) && (edge.resolved || includeUnresolved)
  );
}

function renderGraph() {
  const graph = state.analysis.graph;
  const needle = $("#graph-search").value.trim().toLowerCase();
  const edges = filteredEdges();

  const degreeIn = new Map();
  const degreeOut = new Map();
  for (const edge of edges) {
    degreeOut.set(edge.source, (degreeOut.get(edge.source) || 0) + 1);
    degreeIn.set(edge.target, (degreeIn.get(edge.target) || 0) + 1);
  }

  const nodes = (graph.nodes || []).filter(
    (node) => !needle || node.id.toLowerCase().includes(needle)
  );
  fill(
    $("#nodes-table tbody"),
    nodes.length
      ? nodes.slice(0, 200).map((node) => {
          const row = el("tr", { tabindex: "0", "data-node": node.id }, [
            el("td", {}, [el("code", { text: node.id })]),
            el("td", { text: node.symbolKind || node.kind }),
            el("td", { class: "num", text: degreeIn.get(node.id) || 0 }),
            el("td", { class: "num", text: degreeOut.get(node.id) || 0 }),
          ]);
          row.addEventListener("click", () => selectNode(node.id));
          row.addEventListener("keydown", (event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              selectNode(node.id);
            }
          });
          return row;
        })
      : emptyRow(4, needle ? "No node matches the filter." : "The graph is empty.")
  );

  drawModuleGraph(edges);
}

function drawModuleGraph(edges) {
  const graph = state.analysis.graph;
  const moduleIds = (graph.nodes || [])
    .filter((node) => node.kind === "module")
    .map((node) => node.id);
  const externalIds = (graph.nodes || [])
    .filter((node) => node.kind === "external")
    .map((node) => node.id);
  const moduleOf = new Map();
  for (const node of graph.nodes || []) {
    moduleOf.set(node.id, node.kind === "module" ? node.id : node.module || node.id);
  }

  const ids = [...moduleIds, ...externalIds];
  if (!ids.length) {
    fill($("#graph-canvas"), el("p", { class: "empty", text: "Nothing to draw." }));
    $("#graph-legend").textContent = "";
    return;
  }

  const projected = new Set();
  for (const edge of edges) {
    const source = moduleOf.get(edge.source);
    const target = graph.nodes.find((node) => node.id === edge.target)?.kind === "external"
      ? edge.target
      : moduleOf.get(edge.target);
    if (!source || !target || source === target) continue;
    projected.add(`${source}\u0000${target}\u0000${edge.resolved ? "r" : "u"}`);
  }

  const columns = Math.min(3, Math.max(1, Math.ceil(ids.length / 6)));
  const boxWidth = 196;
  const boxHeight = 30;
  const gapX = 56;
  const gapY = 20;
  const rows = Math.ceil(ids.length / columns);
  const width = columns * boxWidth + (columns - 1) * gapX + 24;
  const height = rows * boxHeight + (rows - 1) * gapY + 24;

  const position = new Map();
  ids.forEach((id, index) => {
    const column = Math.floor(index / rows);
    const row = index % rows;
    position.set(id, {
      x: 12 + column * (boxWidth + gapX),
      y: 12 + row * (boxHeight + gapY),
    });
  });

  const svgns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgns, "svg");
  svg.setAttribute("width", String(width));
  svg.setAttribute("height", String(height));
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);

  for (const key of projected) {
    const [source, target, resolved] = key.split("\u0000");
    const from = position.get(source);
    const to = position.get(target);
    if (!from || !to) continue;
    const path = document.createElementNS(svgns, "path");
    const x1 = from.x + boxWidth;
    const y1 = from.y + boxHeight / 2;
    const x2 = to.x;
    const y2 = to.y + boxHeight / 2;
    const mid = (x1 + x2) / 2;
    path.setAttribute("d", `M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`);
    path.setAttribute("class", `edge${resolved === "u" ? " edge--unresolved" : ""}`);
    svg.append(path);
  }

  const cycleModules = new Set((state.plan?.cycles || []).flat());
  for (const id of ids) {
    const spot = position.get(id);
    const isExternal = externalIds.includes(id);
    const group = document.createElementNS(svgns, "g");
    group.setAttribute(
      "class",
      `node${cycleModules.has(id) ? " node--cycle" : ""}${isExternal ? " node--external" : ""}`
    );
    const rect = document.createElementNS(svgns, "rect");
    rect.setAttribute("x", String(spot.x));
    rect.setAttribute("y", String(spot.y));
    rect.setAttribute("width", String(boxWidth));
    rect.setAttribute("height", String(boxHeight));
    rect.setAttribute("rx", "4");
    const text = document.createElementNS(svgns, "text");
    text.setAttribute("x", String(spot.x + 8));
    text.setAttribute("y", String(spot.y + boxHeight / 2 + 4));
    const label = isExternal ? id.replace(/^external:/, "") : id;
    text.textContent = label.length > 26 ? `${label.slice(0, 25)}…` : label;
    const title = document.createElementNS(svgns, "title");
    title.textContent = isExternal ? `${label} (not a project module)` : label;
    group.append(rect, text, title);
    svg.append(group);
  }

  fill($("#graph-canvas"), svg);
  $("#graph-legend").textContent =
    "Solid edges are resolved; dashed edges point at packages outside the project. " +
    "A thick border marks a module in a dependency cycle.";
}

async function selectNode(nodeId) {
  state.selectedNode = nodeId;
  $$("#nodes-table tbody tr").forEach((row) =>
    row.setAttribute("aria-selected", String(row.dataset.node === nodeId))
  );
  const panel = $("#node-detail");
  panel.hidden = false;
  $("#node-detail-title").textContent = nodeId;
  fill($("#node-detail-body"), el("p", { class: "empty", text: "Loading impact…" }));
  try {
    const impact = await api("/api/impact", {
      body: { project: state.project, origin: nodeId },
    });
    const dependents = Object.entries(impact.dependents).sort((a, b) => a[1] - b[1]);
    const dependencies = Object.entries(impact.dependencies).sort((a, b) => a[1] - b[1]);
    fill($("#node-detail-body"), [
      el("p", {
        class: "footnote",
        text: `${dependents.length} node(s) depend on this; it depends on ${dependencies.length}.`,
      }),
      el("div", { class: "split" }, [
        el("div", {}, [
          el("h3", { text: "Dependents" }),
          el(
            "ul",
            { class: "list" },
            dependents.length
              ? dependents.slice(0, 40).map(([id, hops]) => el("li", { text: `${id} (${hops} hop)` }))
              : [el("li", { class: "empty", text: "Nothing depends on this." })]
          ),
        ]),
        el("div", {}, [
          el("h3", { text: "Dependencies" }),
          el(
            "ul",
            { class: "list" },
            dependencies.length
              ? dependencies.slice(0, 40).map(([id, hops]) => el("li", { text: `${id} (${hops} hop)` }))
              : [el("li", { class: "empty", text: "This depends on nothing." })]
          ),
        ]),
      ]),
    ]);
  } catch (error) {
    fill($("#node-detail-body"), el("p", { class: "notice notice--error", text: error.message }));
  }
}

/* -- plan --------------------------------------------------------------- */

async function loadPlan() {
  setStatus("planning", "busy");
  try {
    state.plan = await api("/api/plan", {
      body: {
        project: state.project,
        sourceLanguage: state.sourceLanguage,
        target: state.target,
      },
    });
    renderPlan();
    enableTab("plan", true);
    setStatus("plan ready", "ok");
    drawModuleGraph(filteredEdges());
  } catch (error) {
    showError("#plan-error", error.message);
    setStatus("failed", "error");
  }
}

function renderPlan() {
  const plan = state.plan;
  $("#plan-summary").textContent =
    `${plan.sourceLanguage} → ${plan.targetLanguage} (support: ${plan.supportLevel}). ` +
    `${plan.groups.length} group(s), highest risk ${plan.highestRisk}. ${plan.cycleHandling}.`;

  const toolchainNotice = $("#plan-toolchain");
  if (plan.missingToolchains.length) {
    toolchainNotice.hidden = false;
    toolchainNotice.className = "notice notice--warn";
    toolchainNotice.textContent =
      `Missing toolchain(s): ${plan.missingToolchains.join(", ")}. ` +
      "Generation will still run, but validation will be skipped and the result reported as unverified.";
  } else {
    toolchainNotice.hidden = false;
    toolchainNotice.className = "notice";
    toolchainNotice.textContent = `Required toolchain(s) found: ${
      plan.requiredToolchains.join(", ") || "none"
    }.`;
  }

  fill(
    $("#plan-table tbody"),
    plan.groups.length
      ? plan.groups.map((group) =>
          el("tr", {}, [
            el("td", { class: "num", text: group.index }),
            el("td", {}, [
              el("code", { text: group.modules.join(", ") }),
              group.isCycle ? el("span", { class: "risk risk--medium", text: " cycle" }) : null,
            ]),
            el("td", {}, [el("span", { class: `risk risk--${group.risk}`, text: group.risk })]),
            el("td", { text: group.riskReasons.join("; ") }),
          ])
        )
      : emptyRow(4, "Nothing to migrate.")
  );

  fill(
    $("#unsupported-table tbody"),
    plan.unsupported.length
      ? plan.unsupported.map((entry) =>
          el("tr", {}, [
            el("td", {}, [el("code", { text: entry.sourceKind })]),
            el("td", { class: "num", text: entry.count }),
            el("td", {}, [
              el("span", { text: entry.reason }),
              entry.hint ? el("span", { class: "diagnostic__hint", text: `hint: ${entry.hint}` }) : null,
            ]),
            el("td", {}, [el("code", { text: entry.locations.slice(0, 4).join(", ") })]),
          ])
        )
      : emptyRow(4, "Every construct encountered has a mapping.")
  );
}

/* -- migrate, output, validation ---------------------------------------- */

async function onMigrate(event) {
  event.preventDefault();
  showError("#plan-error", "");
  state.outputDir = $("#output-input").value.trim();
  if (!state.outputDir) return;

  const button = $("#migrate-button");
  button.disabled = true;
  setStatus("generating", "busy");
  try {
    const result = await api("/api/migrate", {
      body: {
        project: state.project,
        sourceLanguage: state.sourceLanguage,
        target: state.target,
        outputDir: state.outputDir,
        overwrite: $("#overwrite-check").checked,
        validate: $("#validate-check").checked,
      },
    });
    state.report = result.report;
    state.files = result.writtenFiles;
    renderOutput();
    renderValidation();
    ["output", "validation"].forEach((name) => enableTab(name, true));
    showView("validation");
    const status = state.report.status;
    setStatus(
      status,
      status === "passed" ? "ok" : status === "failed" ? "error" : "warn"
    );
  } catch (error) {
    showError("#plan-error", error.message);
    setStatus("failed", "error");
  } finally {
    button.disabled = false;
  }
}

function renderOutput() {
  const report = state.report;
  $("#output-summary").textContent =
    `${report.generatedFiles.length} file(s) in ${report.outputDir}. ` +
    `${report.counts.translated} module(s) translated. ` +
    (report.unsupported.length
      ? `${report.unsupported.reduce((sum, entry) => sum + entry.count, 0)} gap(s) marked in the source.`
      : "No gaps were marked.");

  const gapKinds = new Set(report.unsupported.flatMap((entry) => entry.locations));
  fill(
    $("#file-list"),
    report.generatedFiles.map((path) => {
      const button = el("button", { type: "button", "data-path": path }, [
        el("span", { text: path }),
        gapKinds.size && path.endsWith(".ts") ? null : null,
      ]);
      button.addEventListener("click", () => openFile(path));
      return el("li", {}, [button]);
    })
  );
}

async function openFile(path) {
  state.selectedFile = path;
  $$("#file-list button").forEach((button) =>
    button.setAttribute("aria-current", String(button.dataset.path === path))
  );
  $("#code-title").textContent = path;
  $("#code-body").textContent = "Loading…";
  fill($("#code-flags"), []);
  try {
    const payload = await api("/api/file", {
      method: "GET",
      query: { root: state.report.outputDir, path },
    });
    $("#code-body").textContent = payload.contents;
    const markers = (payload.contents.match(/CODEWEAVER-UNSUPPORTED/g) || []).length;
    fill(
      $("#code-flags"),
      markers
        ? el("div", {
            class: "notice notice--warn",
            text: `${markers} unsupported construct(s) are marked in this file. It is incomplete.`,
          })
        : []
    );
  } catch (error) {
    $("#code-body").textContent = "";
    fill($("#code-flags"), el("div", { class: "notice notice--error", text: error.message }));
  }
}

function renderValidation() {
  const report = state.report;
  const validation = report.validation;
  const banner = $("#validation-banner");
  banner.className = `banner banner--${report.status.replace(/ /g, "-")}`;
  fill(banner, [
    el("div", { class: "banner__status", text: report.status }),
    el("div", { text: report.statusReason }),
  ]);

  const summary = validation.summary;
  fill($("#validation-stats"), [
    stat("checks passed", summary.passed),
    stat("failed", summary.failed, summary.failed ? "stat--error" : ""),
    stat("skipped", summary.skipped, summary.skipped ? "stat--warn" : ""),
    stat("errored", summary.errored, summary.errored ? "stat--error" : ""),
    stat("files generated", report.generatedFiles.length),
  ]);

  fill(
    $("#checks-table tbody"),
    validation.checks.length
      ? validation.checks.map((check) =>
          el("tr", {}, [
            el("td", { text: check.name }),
            el("td", {}, [
              el("span", {
                class: `risk risk--${
                  check.outcome === "passed" ? "low" : check.outcome === "skipped" ? "medium" : "high"
                }`,
                text: check.outcome,
              }),
            ]),
            el("td", { class: "num", text: check.exitCode === null ? "—" : check.exitCode }),
            el("td", {}, [el("code", { text: check.command.join(" ") || "—" })]),
            el("td", { text: check.reason || "—" }),
          ])
        )
      : emptyRow(5, "No checks were configured.")
  );

  renderDiagnostics(
    $("#validation-diagnostics"),
    validation.checks.flatMap((check) => check.diagnostics).concat(report.diagnostics)
  );
  $("#equivalence-note").textContent = report.equivalenceStatement;
}

/* -- wiring ------------------------------------------------------------- */

function wire() {
  $$(".tab").forEach((tab) =>
    tab.addEventListener("click", () => {
      if (!tab.disabled) showView(tab.dataset.view);
    })
  );
  $("#project-form").addEventListener("submit", onScan);
  $("#migrate-form").addEventListener("submit", onMigrate);
  $("#graph-search").addEventListener("input", () => {
    if (state.analysis) renderGraph();
  });
  $$(".edge-filter")
    .concat([$("#unresolved-filter")])
    .forEach((box) =>
      box.addEventListener("change", () => {
        if (state.analysis) renderGraph();
      })
    );
  $("#source-select").addEventListener("change", (event) => {
    state.sourceLanguage = event.target.value;
  });
  $("#target-select").addEventListener("change", (event) => {
    state.target = event.target.value;
    if (state.analysis) loadPlan();
  });
}

wire();
loadLanguages().catch((error) => showError("#project-error", error.message));

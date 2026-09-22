/**
 * BCM Continuity Planner — standalone demo dashboard.
 * Reads ./data.json (produced by scripts/export_dashboard_data.py) and
 * renders: activity hierarchy tree, BIA assessments w/ MTPD/RTO/RPO/MBCO
 * and gap status, and single-point-of-failure flags. No backend calls.
 */

async function loadData() {
  const statusEl = document.getElementById("load-status");
  try {
    const res = await fetch("./data.json", { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    statusEl.textContent = `Loaded ${data.activities.length} activities, ` +
      `${data.bia_assessments.length} BIA assessments — generated ${data.generated_at}`;
    render(data);
  } catch (err) {
    statusEl.textContent = "Could not load data.json — run scripts/export_dashboard_data.py first. " +
      `(${err.message})`;
  }
}

function el(tag, opts = {}, children = []) {
  const node = document.createElement(tag);
  if (opts.class) node.className = opts.class;
  if (opts.text !== undefined) node.textContent = opts.text;
  if (opts.html !== undefined) node.innerHTML = opts.html;
  for (const child of children) node.appendChild(child);
  return node;
}

function renderActivityNode(node, spofMap) {
  const li = el("li");
  const label = el("span", { class: "activity-node" });
  label.appendChild(document.createTextNode(node.name));
  const spofs = spofMap[node.activity_id];
  if (spofs && spofs.length) {
    label.appendChild(el("span", { class: "spof-badge", text: `${spofs.length} SPOF` }));
  }
  li.appendChild(label);
  if (node.children && node.children.length) {
    const ul = el("ul");
    for (const child of node.children) {
      ul.appendChild(renderActivityNode(child, spofMap));
    }
    li.appendChild(ul);
  }
  return li;
}

function renderHierarchy(data) {
  const container = document.getElementById("hierarchy-tree");
  container.innerHTML = "";
  if (!data.process_activity_trees.length) {
    container.appendChild(el("p", { class: "empty-state", text: "No business processes / activities registered yet." }));
    return;
  }
  for (const { process, activity_tree } of data.process_activity_trees) {
    const wrapper = el("div", { class: "tree" });
    wrapper.appendChild(el("h3", { text: `${process.name} (owner: ${process.process_owner})` }));
    if (!activity_tree.length) {
      wrapper.appendChild(el("p", { class: "empty-state", text: "No activities registered for this process." }));
    } else {
      const ul = el("ul");
      for (const node of activity_tree) {
        ul.appendChild(renderActivityNode(node, data.single_points_of_failure_by_activity));
      }
      wrapper.appendChild(ul);
    }
    container.appendChild(wrapper);
  }
}

function activityNameById(data, activityId) {
  const match = data.activities.find(a => a.activity_id === activityId);
  return match ? match.name : activityId;
}

function renderBiaTable(data) {
  const tbody = document.querySelector("#bia-table tbody");
  tbody.innerHTML = "";
  if (!data.bia_assessments.length) {
    document.getElementById("bia-empty").style.display = "block";
    return;
  }
  document.getElementById("bia-empty").style.display = "none";

  const gapsByBia = {};
  for (const gap of data.gap_analyses) {
    (gapsByBia[gap.bia_id] = gapsByBia[gap.bia_id] || []).push(gap);
  }

  for (const bia of data.bia_assessments) {
    const tr = el("tr");
    tr.appendChild(el("td", { text: activityNameById(data, bia.activity_id) }));
    tr.appendChild(el("td", { text: bia.assessor_name }));
    tr.appendChild(el("td", { text: `${bia.mtpd_hours}h` }));
    tr.appendChild(el("td", { text: `${bia.rto_hours}h` }));
    tr.appendChild(el("td", { text: bia.rpo_hours != null ? `${bia.rpo_hours}h` : "—" }));
    tr.appendChild(el("td", { text: `${bia.mbco_percentage}%` }));

    const gaps = gapsByBia[bia.bia_id] || [];
    const gapCell = el("td");
    if (!gaps.length) {
      gapCell.appendChild(el("span", { class: "pill pill-muted", text: "No gap analysis" }));
    } else {
      for (const gap of gaps) {
        const isOpen = gap.gap_hours > 0;
        const pill = el("span", {
          class: `pill ${isOpen ? "pill-red" : "pill-green"}`,
          text: isOpen ? `Gap open: +${gap.gap_hours}h` : `Gap closed: ${gap.gap_hours}h`,
        });
        gapCell.appendChild(pill);
        gapCell.appendChild(document.createElement("br"));
      }
    }
    tr.appendChild(gapCell);

    const strategies = data.recovery_strategies.filter(s => s.bia_id === bia.bia_id);
    const selected = strategies.find(s => s.is_selected_option);
    const strategyCell = el("td");
    if (selected) {
      strategyCell.appendChild(el("span", { class: "strategy-selected", text: `${selected.strategy_name} (${selected.category})` }));
    } else if (strategies.length) {
      strategyCell.appendChild(el("span", { class: "pill pill-muted", text: `${strategies.length} option(s), none selected` }));
    } else {
      strategyCell.appendChild(el("span", { class: "pill pill-muted", text: "No strategy yet" }));
    }
    tr.appendChild(strategyCell);

    tbody.appendChild(tr);
  }
}

function renderSpofList(data) {
  const container = document.getElementById("spof-list");
  container.innerHTML = "";
  const entries = Object.entries(data.single_points_of_failure_by_activity || {});
  if (!entries.length) {
    container.appendChild(el("p", { class: "empty-state", text: "No single points of failure flagged." }));
    return;
  }
  const ul = el("ul", { class: "spof-list" });
  for (const [activityId, spofs] of entries) {
    for (const spof of spofs) {
      const item = el("li", {
        class: "spof-item",
        text: `${activityNameById(data, activityId)} depends on "${spof.resource_name}" ` +
          `(${spof.resource_type}) — flagged as a single point of failure.`,
      });
      ul.appendChild(item);
    }
  }
  container.appendChild(ul);
}

function render(data) {
  renderHierarchy(data);
  renderBiaTable(data);
  renderSpofList(data);
}

loadData();

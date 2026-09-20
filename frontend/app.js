const queueBody = document.getElementById("queue-body");
const rulesBody = document.getElementById("rules-body");
const simForm = document.getElementById("simulation-form");
const simMetrics = document.getElementById("sim-metrics");
const simPlot = document.getElementById("sim-plot");

function el(tag, attrs = {}, text) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  if (text !== undefined) node.textContent = text;
  return node;
}

function formatDate(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}

async function loadQueue() {
  const response = await fetch("/approval-queue?status=pending");
  const items = await response.json();

  queueBody.replaceChildren();
  if (items.length === 0) {
    const row = el("tr");
    row.appendChild(el("td", { colspan: "6", class: "empty" }, "Nothing pending."));
    queueBody.appendChild(row);
    return;
  }

  for (const item of items) {
    const row = el("tr");
    row.appendChild(el("td", {}, item.action_type));

    const tierCell = el("td");
    tierCell.appendChild(el("span", { class: `tier-badge tier-${item.tier}` }, item.tier));
    row.appendChild(tierCell);

    row.appendChild(el("td", {}, item.severity_hint === null ? "—" : String(item.severity_hint)));
    row.appendChild(el("td", {}, item.content));
    row.appendChild(el("td", {}, formatDate(item.created_at)));

    const actionsCell = el("td");
    const actionsWrap = el("div", { class: "row-actions" });

    const approveBtn = el("button", {}, "Approve");
    approveBtn.onclick = () => resolveItem(item.id, "approved_unchanged");

    const editBtn = el("button", { class: "secondary" }, "Edit & approve");
    const rejectBtn = el("button", { class: "reject" }, "Reject");
    rejectBtn.onclick = () => resolveItem(item.id, "rejected");

    actionsWrap.append(approveBtn, editBtn, rejectBtn);
    actionsCell.appendChild(actionsWrap);

    const editBox = el("textarea", { class: "edit-box", style: "display:none" });
    editBox.value = item.content;
    const submitEditBtn = el("button", { class: "secondary", style: "display:none;margin-top:6px" }, "Submit edit");
    submitEditBtn.onclick = () => resolveItem(item.id, "approved_with_edits", editBox.value);

    editBtn.onclick = () => {
      editBox.style.display = editBox.style.display === "none" ? "block" : "none";
      submitEditBtn.style.display = submitEditBtn.style.display === "none" ? "inline-block" : "none";
    };

    actionsCell.append(editBox, submitEditBtn);
    row.appendChild(actionsCell);
    queueBody.appendChild(row);
  }
}

async function resolveItem(id, decision, editedContent) {
  const body = { decision };
  if (editedContent !== undefined) body.edited_content = editedContent;

  const response = await fetch(`/approval-queue/${id}/resolve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    alert(`Resolve failed: ${error.detail || response.statusText}`);
    return;
  }

  await Promise.all([loadQueue(), loadRules()]);
}

async function loadRules() {
  const response = await fetch("/rules");
  const rules = await response.json();

  rulesBody.replaceChildren();
  if (rules.length === 0) {
    const row = el("tr");
    row.appendChild(el("td", { colspan: "7", class: "empty" }, "No rules yet — run the pipeline once to seed them."));
    rulesBody.appendChild(row);
    return;
  }

  for (const rule of rules) {
    const row = el("tr");
    row.appendChild(el("td", {}, rule.action_type));
    row.appendChild(el("td", {}, `sev ${rule.severity}`));

    const tierCell = el("td");
    tierCell.appendChild(el("span", { class: `tier-badge tier-${rule.tier}` }, rule.tier));
    row.appendChild(tierCell);

    const boundCell = el("td");
    const wrap = el("div", { class: "bound-bar-wrap" });
    const barOuter = el("div", { class: "bound-bar" });
    const pct = Math.round(rule.confidence_bound * 100);
    const barInner = el("div", { class: "bound-bar-fill", style: `width:${pct}%` });
    barOuter.appendChild(barInner);
    wrap.append(barOuter, el("span", {}, `${pct}%`));
    boundCell.appendChild(wrap);
    row.appendChild(boundCell);

    row.appendChild(el("td", {}, `${rule.alpha.toFixed(1)} / ${rule.beta.toFixed(1)}`));
    row.appendChild(el("td", {}, String(rule.demotion_count)));
    row.appendChild(el("td", {}, formatDate(rule.last_updated)));
    rulesBody.appendChild(row);
  }
}

simForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const profile = document.getElementById("sim-profile").value;
  const days = Number(document.getElementById("sim-days").value);
  const seed = Number(document.getElementById("sim-seed").value);

  simMetrics.replaceChildren(el("p", {}, "Running simulation…"));

  const response = await fetch("/simulation/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ profile, days, seed }),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    simMetrics.replaceChildren(el("p", {}, `Simulation failed: ${error.detail || response.statusText}`));
    return;
  }

  const metrics = await response.json();
  renderMetrics(metrics);
  simPlot.src = metrics.plot_url;
  simPlot.classList.add("visible");
});

function renderMetrics(metrics) {
  simMetrics.replaceChildren();

  const card = (label, value) => {
    const c = el("div", { class: "metric-card" });
    c.append(el("div", { class: "label" }, label), el("div", { class: "value" }, value));
    return c;
  };

  simMetrics.appendChild(
    card(
      `False-promotion rate (within ${metrics.false_promotion_window_outcomes} outcomes)`,
      `${(metrics.false_promotion_rate * 100).toFixed(0)}%`
    )
  );
  simMetrics.appendChild(card("Raw false-promotion rate (full run)", `${(metrics.false_promotion_rate_raw * 100).toFixed(0)}%`));

  const timeToTrust = Object.entries(metrics.avg_time_to_trust_by_severity)
    .map(([sev, outcomes]) => `sev ${sev}: ${outcomes.toFixed(1)}`)
    .join(", ") || "—";
  simMetrics.appendChild(card("Outcomes to reach AUTO, by severity", timeToTrust));

  const recovery = metrics.recovery_comparisons.length
    ? metrics.recovery_comparisons.map(([first, recover]) => `${first} → ${recover}`).join(", ")
    : "none observed this run";
  simMetrics.appendChild(card("Recovery time (first promotion → re-promotion)", recovery));
}

document.getElementById("refresh-queue").onclick = loadQueue;
document.getElementById("refresh-rules").onclick = loadRules;

loadQueue();
loadRules();

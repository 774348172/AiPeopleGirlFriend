"use strict";

const FAILURE_LABELS = {
  off_topic: "跑题",
  fact_error: "事实错误",
  unsafe: "安全问题",
  relationship_boundary: "关系越界",
  assistant_tone: "助手腔",
  template_repetition: "模板复读",
  overacting: "表演过度",
  emotion_mismatch: "情绪错位",
  fabricated_reality: "编造现实",
  unsupported_shared_history: "编造共同经历",
  other: "其它"
};

const VERDICT_LABELS = {
  a_much_better: "A 明显更好",
  a_better: "A 略好",
  tie: "基本相当",
  b_better: "B 略好",
  b_much_better: "B 明显更好",
  both_unacceptable: "两者都不可接受"
};

const state = {
  reviewerId: "",
  packets: [],
  rubric: null,
  drafts: {},
  submitted: {},
  currentIndex: 0,
  filter: "all",
  saveTimer: null,
  rendering: false
};

const byId = (id) => document.getElementById(id);

function emptyDraft(unitId) {
  const scores = {};
  for (const dimension of state.rubric.dimensions) {
    scores[dimension.dimension_id] = {candidate_a: null, candidate_b: null};
  }
  return {
    reviewer_id: state.reviewerId,
    unit_id: unitId,
    dimension_scores: scores,
    verdict: null,
    failure_reasons: {candidate_a: [], candidate_b: []},
    rationale: ""
  };
}

function currentPacket() { return state.packets[state.currentIndex]; }

function currentValue() {
  const unitId = currentPacket().unit_id;
  return state.submitted[unitId] || state.drafts[unitId] || emptyDraft(unitId);
}

function statusFor(unitId) {
  if (state.submitted[unitId]) return "submitted";
  if (state.drafts[unitId]) return "draft";
  return "pending";
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    cache: "no-store",
    headers: {"Content-Type": "application/json", ...(options.headers || {})},
    ...options
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
  return body;
}

async function login() {
  const reviewerId = byId("reviewer-id").value.trim();
  byId("login-error").textContent = "";
  try {
    const bootstrap = await request(`/api/bootstrap?reviewer_id=${encodeURIComponent(reviewerId)}`);
    state.reviewerId = bootstrap.reviewer_id;
    state.packets = bootstrap.packets;
    state.rubric = bootstrap.rubric;
    state.drafts = bootstrap.drafts;
    state.submitted = bootstrap.submitted;
    state.reviewMode = bootstrap.review_mode || "blind";
    const firstPending = state.packets.findIndex((packet) => !state.submitted[packet.unit_id]);
    state.currentIndex = firstPending < 0 ? 0 : firstPending;
    localStorage.setItem("chat02e.reviewerId", state.reviewerId);
    byId("active-reviewer").textContent = state.reviewerId;
    byId("export-link").href = `/api/export?reviewer_id=${encodeURIComponent(state.reviewerId)}`;
    byId("login").hidden = true;
    byId("workspace").hidden = false;
    buildRubric();
    buildFailures();
    buildVerdicts();
    renderAll();
  } catch (error) {
    byId("login-error").textContent = error.message;
  }
}

function buildRubric() {
  const root = byId("rubric-rows");
  root.replaceChildren();
  for (const dimension of state.rubric.dimensions) {
    const row = document.createElement("div");
    row.className = "rubric-row";
    const label = document.createElement("div");
    label.className = "dimension-label";
    const strong = document.createElement("strong");
    strong.textContent = dimension.label;
    const detail = document.createElement("span");
    detail.textContent = dimension.question;
    label.append(strong, detail);
    row.append(label, scoreControl(dimension.dimension_id, "candidate_a", ""), scoreControl(dimension.dimension_id, "candidate_b", "candidate-b-score"));
    root.append(row);
  }
}

function scoreControl(dimension, candidate, className) {
  const root = document.createElement("div");
  root.className = `score-control ${className}`;
  for (let score = 1; score <= 5; score += 1) {
    const input = document.createElement("input");
    input.type = "radio";
    input.name = `${dimension}.${candidate}`;
    input.id = `${dimension}.${candidate}.${score}`;
    input.value = String(score);
    input.addEventListener("change", handleChange);
    const label = document.createElement("label");
    label.htmlFor = input.id;
    label.textContent = String(score);
    label.title = state.rubric.dimensions.find((item) => item.dimension_id === dimension).anchors[String(score)];
    root.append(input, label);
  }
  return root;
}

function buildFailures() {
  for (const candidate of ["a", "b"]) {
    const root = byId(`failure-${candidate}`);
    root.replaceChildren();
    for (const [value, labelText] of Object.entries(FAILURE_LABELS)) {
      const input = document.createElement("input");
      input.type = "checkbox";
      input.id = `failure-${candidate}-${value}`;
      input.value = value;
      input.dataset.candidate = `candidate_${candidate}`;
      input.addEventListener("change", handleChange);
      const label = document.createElement("label");
      label.htmlFor = input.id;
      label.textContent = labelText;
      root.append(input, label);
    }
  }
}

function buildVerdicts() {
  const root = byId("verdict-control");
  root.replaceChildren();
  for (const [value, labelText] of Object.entries(VERDICT_LABELS)) {
    const input = document.createElement("input");
    input.type = "radio";
    input.name = "verdict";
    input.id = `verdict-${value}`;
    input.value = value;
    input.addEventListener("change", handleChange);
    const label = document.createElement("label");
    label.htmlFor = input.id;
    label.textContent = labelText;
    root.append(input, label);
  }
}

function readForm() {
  const packet = currentPacket();
  const value = emptyDraft(packet.unit_id);
  for (const dimension of state.rubric.dimensions) {
    for (const candidate of ["candidate_a", "candidate_b"]) {
      const selected = document.querySelector(`input[name="${dimension.dimension_id}.${candidate}"]:checked`);
      value.dimension_scores[dimension.dimension_id][candidate] = selected ? Number(selected.value) : null;
    }
  }
  const verdict = document.querySelector('input[name="verdict"]:checked');
  value.verdict = verdict ? verdict.value : null;
  for (const candidate of ["candidate_a", "candidate_b"]) {
    value.failure_reasons[candidate] = Array.from(document.querySelectorAll(`input[data-candidate="${candidate}"]:checked`)).map((input) => input.value);
  }
  value.rationale = byId("rationale").value;
  return value;
}

function applyForm(value, locked) {
  state.rendering = true;
  for (const dimension of state.rubric.dimensions) {
    for (const candidate of ["candidate_a", "candidate_b"]) {
      const score = value.dimension_scores?.[dimension.dimension_id]?.[candidate];
      document.querySelectorAll(`input[name="${dimension.dimension_id}.${candidate}"]`).forEach((input) => {
        input.checked = Number(input.value) === score;
        input.disabled = locked;
      });
    }
  }
  document.querySelectorAll('input[name="verdict"]').forEach((input) => {
    input.checked = input.value === value.verdict;
    input.disabled = locked;
  });
  document.querySelectorAll(".tag-grid input").forEach((input) => {
    input.checked = (value.failure_reasons?.[input.dataset.candidate] || []).includes(input.value);
    input.disabled = locked;
  });
  byId("rationale").value = value.rationale || "";
  byId("rationale").disabled = locked;
  byId("rationale-count").textContent = `${byId("rationale").value.length} / 4000`;
  state.rendering = false;
}

function renderAll() {
  renderProgress();
  renderList();
  renderCurrent();
}

function renderProgress() {
  const submitted = Object.keys(state.submitted).length;
  byId("progress-label").textContent = `${submitted} / ${state.packets.length}`;
  byId("progress-bar").style.width = `${(submitted / state.packets.length) * 100}%`;
}

function renderList() {
  const root = byId("unit-list");
  root.replaceChildren();
  state.packets.forEach((packet, index) => {
    const status = statusFor(packet.unit_id);
    if (state.filter !== "all" && state.filter !== status) return;
    const button = document.createElement("button");
    button.className = `unit-button ${status}${index === state.currentIndex ? " active" : ""}`;
    button.dataset.index = String(index);
    button.innerHTML = `<span class="unit-index">${String(index + 1).padStart(2, "0")}</span><span class="unit-title"></span><span class="status-dot"></span>`;
    button.querySelector(".unit-title").textContent = packet.prompt[packet.prompt.length - 1].content;
    button.addEventListener("click", () => navigate(index));
    root.append(button);
  });
  requestAnimationFrame(() => root.querySelector(".active")?.scrollIntoView({block: "nearest", inline: "nearest"}));
}

function renderCurrent() {
  const packet = currentPacket();
  const value = currentValue();
  const locked = Boolean(state.submitted[packet.unit_id]);
  byId("case-number").textContent = `题目 ${String(state.currentIndex + 1).padStart(2, "0")}`;
  byId("scenario-class").textContent = packet.scenario_class;
  byId("player-prompt").textContent = packet.prompt[packet.prompt.length - 1].content;
  byId("candidate-a-text").textContent = packet.candidate_a.text;
  byId("candidate-b-text").textContent = packet.candidate_b.text;
  for (const candidate of ["a", "b"]) {
    const label = packet[`candidate_${candidate}`].model_label || "";
    const element = byId(`candidate-${candidate}-model`);
    element.textContent = label;
    element.hidden = !label;
  }
  applyForm(value, locked);
  byId("previous-button").disabled = state.currentIndex === 0;
  byId("next-button").disabled = state.currentIndex === state.packets.length - 1;
  byId("submit-button").disabled = locked;
  byId("submit-button").textContent = locked ? "已提交" : "提交本题";
  byId("lock-state").textContent = locked ? "评分已锁定" : (state.drafts[packet.unit_id] ? "草稿" : "未评分");
  byId("lock-state").className = `lock-state${locked ? " locked" : ""}`;
  byId("validation-error").textContent = "";
}

function handleChange() {
  if (state.rendering || state.submitted[currentPacket().unit_id]) return;
  const value = readForm();
  state.drafts[value.unit_id] = value;
  byId("rationale-count").textContent = `${value.rationale.length} / 4000`;
  byId("lock-state").textContent = "草稿";
  renderList();
  scheduleSave(value);
}

function scheduleSave(value) {
  clearTimeout(state.saveTimer);
  byId("save-state").textContent = "保存中";
  state.saveTimer = setTimeout(async () => {
    try {
      await request("/api/draft", {method: "POST", body: JSON.stringify(value)});
      byId("save-state").textContent = "已保存";
    } catch (error) {
      byId("save-state").textContent = "保存失败";
      toast(error.message);
    }
  }, 450);
}

async function flushSave() {
  clearTimeout(state.saveTimer);
  const packet = currentPacket();
  if (state.submitted[packet.unit_id] || !state.drafts[packet.unit_id]) return;
  await request("/api/draft", {method: "POST", body: JSON.stringify(state.drafts[packet.unit_id])});
  byId("save-state").textContent = "已保存";
}

async function switchReviewer() {
  try {
    await flushSave();
  } catch (error) {
    toast(error.message);
    return;
  }
  localStorage.removeItem("chat02e.reviewerId");
  window.location.replace("/");
}

async function navigate(index) {
  if (index === state.currentIndex) return;
  try { await flushSave(); } catch (error) { toast(error.message); return; }
  state.currentIndex = index;
  renderAll();
  window.scrollTo({top: 0, behavior: "smooth"});
}

function validate(value) {
  for (const dimension of state.rubric.dimensions) {
    const pair = value.dimension_scores[dimension.dimension_id];
    if (pair.candidate_a === null || pair.candidate_b === null) return `请完成“${dimension.label}”评分`;
  }
  if (!value.verdict) return "请选择总体判断";
  if (value.verdict === "both_unacceptable" && (!value.failure_reasons.candidate_a.length || !value.failure_reasons.candidate_b.length)) {
    return "两者都不可接受时，A 和 B 都要选择失败原因";
  }
  return "";
}

async function submitCurrent() {
  const value = readForm();
  const error = validate(value);
  byId("validation-error").textContent = error;
  if (error) return;
  byId("submit-button").disabled = true;
  try {
    const result = await request("/api/submit", {method: "POST", body: JSON.stringify(value)});
    state.submitted[value.unit_id] = {...value, submitted_at: result.submitted_at, revealed: false};
    delete state.drafts[value.unit_id];
    renderAll();
    toast("本题已提交并锁定");
    const next = state.packets.findIndex((packet, index) => index > state.currentIndex && !state.submitted[packet.unit_id]);
    if (next >= 0) setTimeout(() => navigate(next), 500);
  } catch (error) {
    byId("submit-button").disabled = false;
    byId("validation-error").textContent = error.message;
  }
}

function toast(message) {
  const element = byId("toast");
  element.textContent = message;
  element.classList.add("show");
  setTimeout(() => element.classList.remove("show"), 2200);
}

byId("login-button").addEventListener("click", login);
byId("reviewer-id").addEventListener("keydown", (event) => { if (event.key === "Enter") login(); });
byId("switch-reviewer-button").addEventListener("click", switchReviewer);
byId("rationale").addEventListener("input", handleChange);
byId("previous-button").addEventListener("click", () => navigate(state.currentIndex - 1));
byId("next-button").addEventListener("click", () => navigate(state.currentIndex + 1));
byId("submit-button").addEventListener("click", submitCurrent);
document.querySelectorAll(".filter").forEach((button) => button.addEventListener("click", () => {
  state.filter = button.dataset.filter;
  document.querySelectorAll(".filter").forEach((item) => item.classList.toggle("active", item === button));
  renderList();
}));

const requestedReviewer = new URLSearchParams(window.location.search).get("reviewer_id");
const remembered = localStorage.getItem("chat02e.reviewerId");
if (requestedReviewer) {
  byId("reviewer-id").value = requestedReviewer;
  login();
} else if (remembered) {
  byId("reviewer-id").value = remembered;
}

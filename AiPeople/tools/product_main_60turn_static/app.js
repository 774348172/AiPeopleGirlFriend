const $ = (id) => document.getElementById(id);

let auditData = null;
let userReviewData = {completed: 0, total: 60, reviews: {}};
let runStartedHere = false;
let pollTimer = null;
let armLabels = {
  base: {name: "裸基座 + 正常上下文", short_name: "裸基座", detail: "Qwen2.5-7B Q4 · 当前世界 · 最近 6 条真实消息"},
  full_program: {name: "完整 V6 程序", short_name: "完整 V6", detail: "冻结白未晞 Q4 · 检索 · M2 · 连续性审查 · GAME_REPLY"},
};

function armName(arm, short = false) {
  return short ? armLabels[arm].short_name : armLabels[arm].name;
}

function applyAuditConfig(data) {
  if (data.arm_labels?.base && data.arm_labels?.full_program) {
    armLabels = data.arm_labels;
  }
  $("comparisonSubtitle").textContent = `实名对比：${armName("base")} vs ${armName("full_program")}`;
  $("baseIdentityName").textContent = armName("base");
  $("baseIdentityDetail").textContent = armLabels.base.detail || "";
  $("fullIdentityName").textContent = armName("full_program");
  $("fullIdentityDetail").textContent = armLabels.full_program.detail || "";
  $("baseSummaryName").textContent = armName("base");
  $("fullSummaryName").textContent = armName("full_program");
  $("baseArmOption").textContent = armName("base");
  $("fullArmOption").textContent = armName("full_program");
  $("baseVerdictOption").textContent = `${armName("base", true)}更好`;
  $("fullVerdictOption").textContent = `${armName("full_program", true)}更好`;
  if (data.speed_context_note && $("speedContextNote")) {
    $("speedContextNote").textContent = data.speed_context_note;
  }
  if (data.speed_latency_note && $("speedLatencyNote")) {
    $("speedLatencyNote").textContent = data.speed_latency_note;
  }
  if (data.has_prior_review === false) {
    $("showPriorReview").closest("label").hidden = true;
    $("summarySection").hidden = true;
  }
  if (data.run_enabled === false) {
    $("runButton").hidden = true;
    $("cancelButton").hidden = true;
  }
}

async function request(path, method = "GET", body = null) {
  const options = {method, headers: {}};
  if (body !== null) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  const value = await response.json();
  if (!response.ok) {
    throw new Error(typeof value.detail === "string" ? value.detail : JSON.stringify(value.detail));
  }
  return value;
}

function formatDate(value) {
  if (!value) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(new Date(value));
}

function appendSummaryRow(label, base, full, tone = "") {
  const row = document.createElement("tr");
  if (tone) row.className = tone;
  [label, base, full].forEach((value) => {
    const cell = document.createElement("td");
    cell.textContent = value;
    row.append(cell);
  });
  $("summaryBody").append(row);
}

function renderSummary(summary, turns) {
  const base = summary.base;
  const full = summary.full_program;
  $("summaryBody").replaceChildren();
  const fraction = (value, total) => `${value} / ${total}`;
  appendSummaryRow("完整回应当前输入", fraction(base.address_current.pass, base.turns), fraction(full.address_current.pass, full.turns));
  appendSummaryRow("部分回应", base.address_current.partial || 0, full.address_current.partial || 0);
  appendSummaryRow("未回应 / 答非所问", base.address_current.fail || 0, full.address_current.fail || 0, "problem-row");
  appendSummaryRow("符合权威状态", fraction(base.state_consistency.pass, base.turns), fraction(full.state_consistency.pass, full.turns));
  appendSummaryRow("权威状态错误", base.state_consistency.fail || 0, full.state_consistency.fail || 0, "problem-row");
  appendSummaryRow("状态无法确定", base.state_consistency.uncertain || 0, full.state_consistency.uncertain || 0);
  appendSummaryRow("句内逻辑连贯", fraction(base.internal_logic.coherent, base.turns), fraction(full.internal_logic.coherent, full.turns));
  appendSummaryRow("句内逻辑断裂", base.internal_logic.local_logic_break || 0, full.internal_logic.local_logic_break || 0, "problem-row");
  appendSummaryRow("含无依据断言", base.unsupported_claim_turns, full.unsupported_claim_turns, "problem-row");
  appendSummaryRow("四项全部干净通过", fraction(base.all_four_dimensions_clean, base.turns), fraction(full.all_four_dimensions_clean, full.turns), "clean-row");
  const comparisons = turns.map(compareTurn);
  const baseWins = comparisons.filter((value) => value.winner === "base").length;
  const fullWins = comparisons.filter((value) => value.winner === "full_program").length;
  const ties = comparisons.filter((value) => value.winner === "tie").length;
  appendSummaryRow("逐轮人工标签对比胜出", `${baseWins} 轮`, `${fullWins} 轮`, "comparison-row");
  appendSummaryRow("逐轮人工标签对比持平", `${ties} 轮`, `${ties} 轮`);
}

function formatMilliseconds(value) {
  return value == null ? "无有效数据" : `${(Number(value) / 1000).toFixed(2)} 秒`;
}

function renderSpeedSummary(summary) {
  const section = $("speedSection");
  if (!summary?.base || !summary?.full_program) {
    section.hidden = true;
    return;
  }
  section.hidden = false;
  $("baseSpeedName").textContent = armName("base", true);
  $("fullSpeedName").textContent = armName("full_program", true);
  $("speedBody").replaceChildren();
  const row = (label, key, formatter = formatMilliseconds) => {
    const element = document.createElement("tr");
    [label, formatter(summary.base[key]), formatter(summary.full_program[key])].forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      element.append(cell);
    });
    $("speedBody").append(element);
  };
  row("平均总耗时（60轮）", "mean_total_elapsed_ms");
  row("平均总耗时（热启动，2-60轮）", "warm_mean_total_elapsed_ms");
  row("平均首个模型token", "mean_first_output_ms");
  row("平均玩家可见首字", "mean_first_visible_ms");
  row("玩家可见首字中位数", "median_first_visible_ms");
  row("玩家可见首字 P90", "p90_first_visible_ms");
  row("生成速度（含思考）", "aggregate_eval_tokens_per_second", (value) => value == null ? "无有效数据" : `${Number(value).toFixed(2)} token/s`);
  row("平均思考长度", "mean_thinking_chars", (value) => value == null ? "无有效数据" : `${Number(value).toFixed(0)} 字符`);
}

function reviewOf(turn, arm) {
  return turn[arm]?.manual_review || {};
}

function defect(review, type = "all") {
  const checks = {
    address: review.address_current !== "pass",
    state: review.state_consistency !== "pass",
    logic: review.internal_logic !== "coherent",
    unsupported: review.unsupported_claim === true,
  };
  return type === "all" ? Object.values(checks).some(Boolean) : Boolean(checks[type]);
}

function qualityScore(review) {
  const address = {pass: 2, partial: 1, fail: 0}[review.address_current] ?? 0;
  const state = {pass: 2, uncertain: 1, fail: 0}[review.state_consistency] ?? 0;
  const logic = review.internal_logic === "coherent" ? 1 : 0;
  const supported = review.unsupported_claim === false ? 1 : 0;
  return address + state + logic + supported;
}

function compareTurn(turn) {
  const baseReview = reviewOf(turn, "base");
  const fullReview = reviewOf(turn, "full_program");
  const baseScore = qualityScore(baseReview);
  const fullScore = qualityScore(fullReview);
  return {
    baseScore,
    fullScore,
    winner: baseScore === fullScore ? "tie" : baseScore > fullScore ? "base" : "full_program",
  };
}

function armsForFilter() {
  const selected = $("armFilter").value;
  return selected === "all" ? ["base", "full_program"] : [selected];
}

function matchesFilters(turn) {
  const query = $("searchInput").value.trim().toLocaleLowerCase("zh-CN");
  const phase = $("phaseFilter").value;
  const defectType = $("defectFilter").value;
  const verdictType = $("verdictFilter").value;
  const issueOnly = $("issueOnly").checked;
  const unreviewedOnly = $("unreviewedOnly").checked;
  const arms = armsForFilter();
  const searchable = [
    turn.turn, turn.phase, turn.player, turn.authoritative_world, turn.expectation,
    turn.base?.reply, turn.full_program?.reply,
    reviewOf(turn, "base").note, reviewOf(turn, "full_program").note,
    userReviewData.reviews?.[String(turn.turn)]?.note,
  ].filter(Boolean).join(" ").toLocaleLowerCase("zh-CN");

  if (query && !searchable.includes(query)) return false;
  if (phase !== "all" && turn.phase !== phase) return false;
  if (defectType !== "all" && !arms.some((arm) => defect(reviewOf(turn, arm), defectType))) return false;
  if (issueOnly && !arms.some((arm) => defect(reviewOf(turn, arm)))) return false;
  if (verdictType !== "all" && compareTurn(turn).winner !== verdictType) return false;
  if (unreviewedOnly && userReviewData.reviews?.[String(turn.turn)]) return false;
  return true;
}

function labelSpec(review) {
  const address = {
    pass: ["已回应当前输入", "good"],
    partial: ["仅部分回应", "warn"],
    fail: ["未回应 / 答非所问", "bad"],
  }[review.address_current] || ["回应：未标注", "neutral"];
  const state = {
    pass: ["符合权威状态", "good"],
    uncertain: ["状态无法确定", "warn"],
    fail: ["违反权威状态", "bad"],
  }[review.state_consistency] || ["状态：未标注", "neutral"];
  const logic = review.internal_logic === "coherent"
    ? ["句内逻辑连贯", "good"]
    : review.internal_logic === "local_logic_break"
      ? ["句内逻辑断裂", "bad"]
      : ["逻辑：未标注", "neutral"];
  const unsupported = review.unsupported_claim === true
    ? ["存在无依据断言", "bad"]
    : ["无额外编造", "good"];
  return [address, state, logic, unsupported];
}

function createArmResult(turn, arm) {
  const value = turn[arm] || {};
  const review = reviewOf(turn, arm);
  const score = qualityScore(review);
  const section = document.createElement("section");
  section.className = `arm-result ${arm === "base" ? "base-result" : "full-result"}`;
  if ($("armFilter").value !== "all" && $("armFilter").value !== arm) section.classList.add("deemphasized");

  const header = document.createElement("div");
  header.className = "arm-header";
  const identity = document.createElement("div");
  const mark = document.createElement("span");
  mark.className = "identity-mark small";
  mark.textContent = arm === "base" ? "A" : "B";
  const name = document.createElement("strong");
  name.textContent = armName(arm);
  identity.append(mark, name);
  const latency = document.createElement("span");
  latency.className = "latency";
  const metrics = [];
  if (value.elapsed_ms != null) metrics.push(`总计 ${(Number(value.elapsed_ms) / 1000).toFixed(2)}秒`);
  if (value.first_visible_ms != null) metrics.push(`首字 ${(Number(value.first_visible_ms) / 1000).toFixed(2)}秒`);
  if (value.eval_tokens_per_second != null) metrics.push(`${Number(value.eval_tokens_per_second).toFixed(1)} tok/s`);
  if (value.thinking_chars != null) metrics.push(`思考 ${value.thinking_chars}字`);
  latency.textContent = metrics.length ? metrics.join(" · ") : "内部调用未计时";
  header.append(identity, latency);

  const quality = document.createElement("span");
  quality.className = `quality-badge ${score === 6 ? "good" : "bad"}`;
  quality.textContent = `原标签：${score === 6 ? "好" : "差"} · ${score}/6`;

  const replyLabel = document.createElement("h3");
  replyLabel.textContent = "模型原始回复";
  const reply = document.createElement("blockquote");
  reply.textContent = value.reply || "本轮没有有效回复";

  const labels = document.createElement("div");
  labels.className = "review-labels";
  labelSpec(review).forEach(([text, tone]) => {
    const label = document.createElement("span");
    label.className = `review-label ${tone}`;
    label.textContent = text;
    labels.append(label);
  });

  const reason = document.createElement("div");
  reason.className = "review-reason";
  const reasonTitle = document.createElement("h3");
  reasonTitle.textContent = "人工审核理由";
  const reasonText = document.createElement("p");
  reasonText.textContent = review.note || "没有记录人工审核理由。";
  reason.append(reasonTitle, reasonText);
  section.append(header, replyLabel, reply, quality, labels, reason);
  return section;
}

function createVerdict(turn) {
  const comparison = compareTurn(turn);
  const baseReview = reviewOf(turn, "base");
  const fullReview = reviewOf(turn, "full_program");
  const section = document.createElement("section");
  section.className = `turn-verdict winner-${comparison.winner}`;

  const heading = document.createElement("div");
  heading.className = "verdict-heading";
  const label = document.createElement("span");
  label.textContent = "现有标签派生结论";
  const winner = document.createElement("strong");
  winner.textContent = comparison.winner === "base"
    ? `${armName("base")}更好`
    : comparison.winner === "full_program"
      ? `${armName("full_program")}更好`
      : "两侧持平";
  heading.append(label, winner);

  const scoreLine = document.createElement("p");
  scoreLine.className = "verdict-scores";
  scoreLine.textContent = `${armName("base", true)} ${comparison.baseScore}/6 · ${armName("full_program", true)} ${comparison.fullScore}/6`;

  const dimensions = [
    ["回应当前输入", {pass: 2, partial: 1, fail: 0}[baseReview.address_current] ?? 0, {pass: 2, partial: 1, fail: 0}[fullReview.address_current] ?? 0],
    ["权威状态", {pass: 2, uncertain: 1, fail: 0}[baseReview.state_consistency] ?? 0, {pass: 2, uncertain: 1, fail: 0}[fullReview.state_consistency] ?? 0],
    ["句内逻辑", baseReview.internal_logic === "coherent" ? 1 : 0, fullReview.internal_logic === "coherent" ? 1 : 0],
    ["无依据断言", baseReview.unsupported_claim === false ? 1 : 0, fullReview.unsupported_claim === false ? 1 : 0],
  ];
  const baseBetter = dimensions.filter(([, baseValue, fullValue]) => baseValue > fullValue).map(([name]) => name);
  const fullBetter = dimensions.filter(([, baseValue, fullValue]) => fullValue > baseValue).map(([name]) => name);
  const explanation = document.createElement("p");
  explanation.className = "verdict-explanation";
  const parts = [];
  if (baseBetter.length) parts.push(`${armName("base", true)}在${baseBetter.join("、")}上更好`);
  if (fullBetter.length) parts.push(`${armName("full_program", true)}在${fullBetter.join("、")}上更好`);
  explanation.textContent = parts.length ? `${parts.join("；")}。` : "两侧四项人工标签完全相同。";

  section.append(heading, scoreLine, explanation);
  return section;
}

function createChoiceGroup(title, field, turnNumber, choices, selected) {
  const fieldset = document.createElement("fieldset");
  fieldset.className = "decision-group";
  const legend = document.createElement("legend");
  legend.textContent = title;
  const segments = document.createElement("div");
  segments.className = "decision-segments";
  choices.forEach(([value, text]) => {
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = "radio";
    input.name = `${field}_${turnNumber}`;
    input.value = value;
    input.dataset.field = field;
    input.checked = selected === value;
    const span = document.createElement("span");
    span.textContent = text;
    label.append(input, span);
    segments.append(label);
  });
  fieldset.append(legend, segments);
  return fieldset;
}

function createPersonalReview(turn) {
  const saved = userReviewData.reviews?.[String(turn.turn)] || null;
  const section = document.createElement("section");
  section.className = `personal-review ${saved ? "is-reviewed" : "is-pending"}`;
  section.dataset.personalTurn = turn.turn;

  const header = document.createElement("div");
  header.className = "personal-review-header";
  const title = document.createElement("h3");
  title.textContent = "我的重新审核";
  const status = document.createElement("span");
  status.className = "personal-review-status";
  status.textContent = saved ? "已保存" : "未审核";
  header.append(title, status);

  const controls = document.createElement("div");
  controls.className = "personal-review-controls";
  controls.append(
    createChoiceGroup(`A ${armName("base", true)}`, "base_quality", turn.turn, [["good", "好"], ["bad", "差"]], saved?.base_quality),
    createChoiceGroup(`B ${armName("full_program", true)}`, "full_quality", turn.turn, [["good", "好"], ["bad", "差"]], saved?.full_quality),
    createChoiceGroup("本轮哪个更好", "winner", turn.turn, [["base", "A 更好"], ["full_program", "B 更好"], ["tie", "持平"]], saved?.winner),
  );

  const noteLabel = document.createElement("label");
  noteLabel.className = "personal-note";
  const noteTitle = document.createElement("span");
  noteTitle.textContent = "我的备注（可选）";
  const note = document.createElement("textarea");
  note.dataset.field = "note";
  note.maxLength = 1000;
  note.rows = 2;
  note.placeholder = "记录为什么这样判断";
  note.value = saved?.note || "";
  noteLabel.append(noteTitle, note);

  const actions = document.createElement("div");
  actions.className = "personal-review-actions";
  const feedback = document.createElement("span");
  feedback.className = "save-feedback";
  const save = document.createElement("button");
  save.type = "button";
  save.className = "save-review-button";
  save.textContent = saved ? "更新本轮判断" : "保存本轮判断";
  save.addEventListener("click", () => savePersonalReview(turn.turn, section));
  actions.append(feedback, save);

  section.append(header, controls, noteLabel, actions);
  return section;
}

async function savePersonalReview(turnNumber, section) {
  const selected = (field) => section.querySelector(`input[data-field="${field}"]:checked`)?.value;
  const feedback = section.querySelector(".save-feedback");
  const button = section.querySelector(".save-review-button");
  const payload = {
    base_quality: selected("base_quality"),
    full_quality: selected("full_quality"),
    winner: selected("winner"),
    note: section.querySelector('textarea[data-field="note"]').value,
  };
  if (!payload.base_quality || !payload.full_quality || !payload.winner) {
    feedback.textContent = "请完成三项判断";
    feedback.className = "save-feedback error";
    return;
  }
  button.disabled = true;
  feedback.textContent = "保存中...";
  feedback.className = "save-feedback";
  try {
    userReviewData = await request(`/api/user-review/${turnNumber}`, "PUT", payload);
    section.classList.remove("is-pending");
    section.classList.add("is-reviewed");
    section.querySelector(".personal-review-status").textContent = "已保存";
    button.textContent = "更新本轮判断";
    feedback.textContent = "已保存";
    renderRereviewProgress();
    if ($("unreviewedOnly").checked) renderTurns();
  } catch (error) {
    feedback.textContent = error.message;
    feedback.className = "save-feedback error";
  } finally {
    button.disabled = false;
  }
}

function renderRereviewProgress() {
  const reviews = Object.values(userReviewData.reviews || {});
  const total = userReviewData.total || 60;
  const completed = reviews.length;
  const baseWins = reviews.filter((review) => review.winner === "base").length;
  const fullWins = reviews.filter((review) => review.winner === "full_program").length;
  const ties = reviews.filter((review) => review.winner === "tie").length;
  $("rereviewProgressBar").style.width = `${Math.min(100, completed / total * 100)}%`;
  $("rereviewProgressText").textContent = `${completed} / ${total}`;
  $("rereviewBreakdown").textContent = completed
    ? `你的结论：${armName("base", true)} ${baseWins} 胜 · ${armName("full_program", true)} ${fullWins} 胜 · 持平 ${ties}`
    : "尚未开始";
  $("resetRereview").disabled = completed === 0;
}

function createTurn(turn) {
  const fragment = $("turnTemplate").content.cloneNode(true);
  const article = fragment.querySelector(".audit-turn");
  fragment.querySelector(".turn-number").textContent = `第 ${turn.turn} 轮`;
  fragment.querySelector(".phase-badge").textContent = turn.phase;
  fragment.querySelector(".player-text").textContent = turn.player;
  fragment.querySelector(".world-text").textContent = turn.authoritative_world;
  fragment.querySelector(".expectation-text").textContent = turn.expectation;

  const findings = fragment.querySelector(".turn-findings");
  const baseIssue = defect(reviewOf(turn, "base"));
  const fullIssue = defect(reviewOf(turn, "full_program"));
  const finding = document.createElement("span");
  finding.className = baseIssue || fullIssue ? "finding has-issue" : "finding clean";
  finding.textContent = baseIssue || fullIssue
    ? `发现问题：${baseIssue ? armName("base", true) : ""}${baseIssue && fullIssue ? "、" : ""}${fullIssue ? armName("full_program", true) : ""}`
    : "两侧本轮均干净通过";
  findings.append(finding);

  const comparison = fragment.querySelector(".comparison-grid");
  comparison.replaceChildren(createArmResult(turn, "base"), createArmResult(turn, "full_program"));
  fragment.querySelector(".turn-verdict").replaceWith(createVerdict(turn));
  fragment.querySelector(".personal-review").replaceWith(createPersonalReview(turn));
  article.dataset.turn = turn.turn;
  return fragment;
}

function renderTurns() {
  if (!auditData) return;
  const visible = auditData.turns.filter(matchesFilters);
  const list = $("turnList");
  list.replaceChildren();
  visible.forEach((turn) => list.append(createTurn(turn)));
  if (!visible.length) {
    const empty = document.createElement("p");
    empty.className = "message";
    empty.textContent = "没有符合当前筛选条件的轮次。";
    list.append(empty);
  }
  $("resultCount").textContent = `${visible.length} / ${auditData.turns.length} 轮`;
}

function populatePhases(turns) {
  const select = $("phaseFilter");
  select.replaceChildren(new Option("全部阶段", "all"));
  [...new Set(turns.map((turn) => turn.phase))].forEach((phase) => select.add(new Option(phase, phase)));
}

async function loadAudit() {
  try {
    const [data, personal] = await Promise.all([
      request("/api/review"),
      request("/api/user-review"),
    ]);
    auditData = data;
    userReviewData = personal;
    applyAuditConfig(data);
    renderSpeedSummary(data.speed_summary);
    $("reviewError").hidden = true;
    $("summarySection").hidden = false;
    $("generatedAt").textContent = `复核生成：${formatDate(data.generated_at)}`;
    renderSummary(data.summary, data.turns);
    populatePhases(data.turns);
    renderRereviewProgress();
    renderTurns();
  } catch (error) {
    $("reviewError").hidden = false;
    $("reviewError").textContent = `审核结果读取失败：${error.message}`;
    $("turnList").replaceChildren();
  }
}

function renderRunStatus(state) {
  const running = state.status === "running";
  const total = state.total || 60;
  $("runStatus").hidden = !running && !runStartedHere;
  $("progressBar").style.width = `${Math.min(100, (state.progress / total) * 100)}%`;
  $("progressText").textContent = `${state.progress} / ${total}`;
  $("stageText").textContent = state.error ? `${state.current_stage}：${state.error}` : state.current_stage;
  $("runButton").disabled = running;
  $("cancelButton").disabled = !running;

  if (runStartedHere && !running && ["completed", "failed", "cancelled"].includes(state.status)) {
    clearInterval(pollTimer);
    pollTimer = null;
    $("stageText").textContent = state.status === "completed"
      ? "新测试已完成。该批结果尚未人工复核，因此不会套用旧批次的人工标签。"
      : $("stageText").textContent;
  }
}

async function pollStatus() {
  try {
    renderRunStatus(await request("/api/status"));
  } catch (error) {
    $("runStatus").hidden = false;
    $("stageText").textContent = `状态读取失败：${error.message}`;
  }
}

function resetFilters() {
  $("searchInput").value = "";
  $("phaseFilter").value = "all";
  $("armFilter").value = "all";
  $("defectFilter").value = "all";
  $("verdictFilter").value = "all";
  $("issueOnly").checked = false;
  $("unreviewedOnly").checked = true;
  renderTurns();
}

["searchInput", "phaseFilter", "armFilter", "defectFilter", "verdictFilter", "issueOnly", "unreviewedOnly"].forEach((id) => {
  $(id).addEventListener(id === "searchInput" ? "input" : "change", renderTurns);
});
$("resetFilters").addEventListener("click", resetFilters);

$("showPriorReview").addEventListener("change", () => {
  document.body.classList.toggle("show-prior-review", $("showPriorReview").checked);
});

$("resetRereview").addEventListener("click", async () => {
  if (!window.confirm("确定清空当前 60 轮的全部个人重审结果吗？此操作不能撤销。")) return;
  const button = $("resetRereview");
  button.disabled = true;
  try {
    userReviewData = await request("/api/user-review", "DELETE");
    renderRereviewProgress();
    renderTurns();
  } catch (error) {
    $("reviewError").hidden = false;
    $("reviewError").textContent = `清空失败：${error.message}`;
  } finally {
    button.disabled = Object.keys(userReviewData.reviews || {}).length === 0;
  }
});

$("runButton").addEventListener("click", async () => {
  runStartedHere = true;
  auditData = null;
  $("summarySection").hidden = true;
  $("turnList").innerHTML = '<p class="message">正在运行新的 60 轮测试。完成后需对新结果单独进行人工复核。</p>';
  $("resultCount").textContent = "0 / 60 轮";
  try {
    renderRunStatus(await request("/api/run", "POST"));
    if (!pollTimer) pollTimer = setInterval(pollStatus, 1000);
  } catch (error) {
    $("runStatus").hidden = false;
    $("stageText").textContent = error.message;
  }
});

$("cancelButton").addEventListener("click", async () => {
  try { renderRunStatus(await request("/api/cancel", "POST")); }
  catch (error) { $("stageText").textContent = error.message; }
});

loadAudit();
pollStatus();

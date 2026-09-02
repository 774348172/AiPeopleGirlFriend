const $ = (id) => document.getElementById(id);

const ISSUE_LABELS = {
  off_topic: "答非所问 / 未承接",
  state_error: "权威状态错误",
  logic_error: "句内逻辑错误",
  action_narration: "动作旁白",
  unsupported_claim: "无依据扩写",
  other: "其他",
};

let auditData = null;
let userReview = {completed: 0, total: 60, good: 0, bad: 0, reviews: {}};

async function request(path, method = "GET", body = null) {
  const options = {method, headers: {}};
  if (body !== null) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  const value = await response.json();
  if (!response.ok) throw new Error(value.detail || "请求失败");
  return value;
}

function renderProgress() {
  const completed = userReview.completed || 0;
  const total = userReview.total || 60;
  $("progressBar").style.width = `${completed / total * 100}%`;
  $("progressText").textContent = `${completed} / ${total}`;
  $("breakdown").textContent = completed ? `好 ${userReview.good} 轮 · 差 ${userReview.bad} 轮` : "尚未开始";
}

function matches(turn) {
  const saved = userReview.reviews[String(turn.turn)];
  const query = $("searchInput").value.trim().toLocaleLowerCase("zh-CN");
  const phase = $("phaseFilter").value;
  const filter = $("reviewFilter").value;
  const searchable = [turn.turn, turn.phase, turn.player, turn.authoritative_world, turn.expectation, turn.response, saved?.note]
    .filter(Boolean).join(" ").toLocaleLowerCase("zh-CN");
  if (query && !searchable.includes(query)) return false;
  if (phase !== "all" && turn.phase !== phase) return false;
  if (filter === "unreviewed" && saved) return false;
  if ((filter === "good" || filter === "bad") && saved?.quality !== filter) return false;
  return true;
}

function issueOptions(turnNumber, saved) {
  const container = document.createElement("div");
  container.className = "issue-options";
  Object.entries(ISSUE_LABELS).forEach(([value, labelText]) => {
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.name = `issues-${turnNumber}`;
    input.value = value;
    input.checked = saved?.issues?.includes(value) || false;
    const span = document.createElement("span");
    span.textContent = labelText;
    label.append(input, span);
    container.append(label);
  });
  return container;
}

function renderTurns() {
  const turns = auditData.turns.filter(matches);
  const list = $("turnList");
  list.replaceChildren();
  $("resultCount").textContent = `${turns.length} / ${auditData.turns.length} 轮`;
  if (!turns.length) {
    const empty = document.createElement("p");
    empty.className = "message";
    empty.textContent = "当前筛选下没有轮次。";
    list.append(empty);
    return;
  }

  turns.forEach((turn) => {
    const saved = userReview.reviews[String(turn.turn)];
    const fragment = $("turnTemplate").content.cloneNode(true);
    const card = fragment.querySelector(".audit-turn");
    card.dataset.turn = turn.turn;
    fragment.querySelector(".turn-number").textContent = `第 ${turn.turn} 轮`;
    fragment.querySelector(".phase-badge").textContent = turn.phase;
    const state = fragment.querySelector(".turn-state");
    state.textContent = saved ? (saved.quality === "good" ? "已判断：好" : "已判断：差") : "未审核";
    state.className = `turn-state ${saved ? saved.quality : "pending"}`;
    fragment.querySelector(".player-text").textContent = turn.player;
    fragment.querySelector(".world-text").textContent = turn.authoritative_world;
    fragment.querySelector(".expectation-text").textContent = turn.expectation;
    fragment.querySelector(".reply-text").textContent = turn.response;
    fragment.querySelector(".latency").textContent = `${(Number(turn.elapsed_ms) / 1000).toFixed(2)} 秒`;
    const hasNarration = turn.format_flags?.markup_narration || turn.format_flags?.line_narration;
    const formatResult = fragment.querySelector(".format-result");
    formatResult.textContent = hasNarration ? "格式检测：发现疑似动作旁白" : "格式检测：未发现动作旁白";
    formatResult.classList.toggle("problem", hasNarration);

    const good = fragment.querySelector('input[type="radio"][value="good"]');
    const bad = fragment.querySelector('input[type="radio"][value="bad"]');
    good.name = `quality-${turn.turn}`;
    bad.name = `quality-${turn.turn}`;
    good.checked = saved?.quality === "good";
    bad.checked = saved?.quality === "bad";
    const issueContainer = fragment.querySelector(".issue-options");
    issueContainer.replaceWith(issueOptions(turn.turn, saved));
    const note = fragment.querySelector("textarea");
    note.value = saved?.note || "";

    good.addEventListener("change", () => {
      card.querySelectorAll('.issue-options input').forEach((input) => { input.checked = false; });
    });
    fragment.querySelector(".save-button").addEventListener("click", async () => {
      const feedback = card.querySelector(".save-feedback");
      const quality = card.querySelector('input[type="radio"]:checked')?.value;
      const issues = [...card.querySelectorAll('.issue-options input:checked')].map((input) => input.value);
      feedback.className = "save-feedback";
      if (!quality) {
        feedback.textContent = "请先选择好或差";
        feedback.classList.add("error");
        return;
      }
      feedback.textContent = "正在保存...";
      try {
        userReview = await request(`/api/user-review/${turn.turn}`, "PUT", {quality, issues, note: note.value});
        renderProgress();
        renderTurns();
      } catch (error) {
        feedback.textContent = error.message;
        feedback.classList.add("error");
      }
    });
    list.append(fragment);
  });
}

function populatePhases() {
  [...new Set(auditData.turns.map((turn) => turn.phase))].forEach((phase) => {
    const option = document.createElement("option");
    option.value = phase;
    option.textContent = phase;
    $("phaseFilter").append(option);
  });
}

async function initialize() {
  try {
    [auditData, userReview] = await Promise.all([request("/api/review"), request("/api/user-review")]);
    $("modelName").textContent = auditData.model.label;
    $("modelDetail").textContent = `${auditData.model.name} · 最近 ${auditData.controls.history_limit_messages} 条消息 · 纯对白规则每轮注入`;
    populatePhases();
    renderProgress();
    renderTurns();
  } catch (error) {
    $("reviewError").hidden = false;
    $("reviewError").textContent = error.message;
  }
}

["searchInput", "phaseFilter", "reviewFilter"].forEach((id) => $(id).addEventListener(id === "searchInput" ? "input" : "change", renderTurns));
$("resetFilters").addEventListener("click", () => {
  $("searchInput").value = "";
  $("phaseFilter").value = "all";
  $("reviewFilter").value = "unreviewed";
  renderTurns();
});
$("resetReview").addEventListener("click", async () => {
  if (!window.confirm("确定清空这次 60 轮的全部人工审核结果吗？")) return;
  userReview = await request("/api/user-review", "DELETE");
  renderProgress();
  renderTurns();
});

initialize();

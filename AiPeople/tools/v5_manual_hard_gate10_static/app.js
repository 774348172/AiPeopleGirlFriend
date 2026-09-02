const $ = (id) => document.getElementById(id);

const ISSUE_LABELS = {
  did_not_answer: "没有回答问题",
  fact_error: "事实错误",
  subject_error: "人物 / 主体错误",
  logic_error: "句内逻辑错误",
  unsupported_claim: "无依据扩写",
  unknown_handling: "不知道时处理不当",
  other: "其他",
};

let auditData = null;
let userReview = {completed: 0, total: 10, good: 0, bad: 0, reviews: {}};

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

function contextText(context) {
  const parts = [];
  parts.push(`最近对话：${context.recent_dialogue?.length ? context.recent_dialogue.join(" / ") : "无"}`);
  parts.push(`记忆：${context.selected_memories?.length ? context.selected_memories.join(" / ") : "无"}`);
  parts.push(`游戏反馈：${context.game_feedback?.length ? context.game_feedback.join(" / ") : "无"}`);
  return parts.join("\n");
}

function renderProgress() {
  const total = userReview.total || auditData?.turns.length || 10;
  const completed = userReview.completed || 0;
  $("progressBar").style.width = `${total ? completed / total * 100 : 0}%`;
  $("progressText").textContent = `${completed} / ${total}`;
  $("breakdown").textContent = completed ? `好 ${userReview.good} 轮 · 差 ${userReview.bad} 轮` : "尚未开始";
}

function matches(turn) {
  const saved = userReview.reviews[String(turn.turn)];
  const query = $("searchInput").value.trim().toLocaleLowerCase("zh-CN");
  const phase = $("phaseFilter").value;
  const filter = $("reviewFilter").value;
  const searchable = [turn.turn, turn.phase, turn.player, contextText(turn.visible_context), turn.review_focus, turn.response, saved?.note]
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
    fragment.querySelector(".context-text").textContent = contextText(turn.visible_context);
    fragment.querySelector(".focus-text").textContent = turn.review_focus;
    fragment.querySelector(".raw-input").textContent = JSON.stringify(turn.model_input, null, 2);
    fragment.querySelector(".reply-text").textContent = turn.response;
    fragment.querySelector(".latency").textContent = `${(Number(turn.elapsed_ms) / 1000).toFixed(2)} 秒`;

    const good = fragment.querySelector('input[type="radio"][value="good"]');
    const bad = fragment.querySelector('input[type="radio"][value="bad"]');
    good.name = `quality-${turn.turn}`;
    bad.name = `quality-${turn.turn}`;
    good.checked = saved?.quality === "good";
    bad.checked = saved?.quality === "bad";
    fragment.querySelector(".issue-options").replaceWith(issueOptions(turn.turn, saved));
    const note = fragment.querySelector("textarea");
    note.value = saved?.note || "";

    good.addEventListener("change", () => {
      card.querySelectorAll(".issue-options input").forEach((input) => { input.checked = false; });
    });
    fragment.querySelector(".save-button").addEventListener("click", async () => {
      const feedback = card.querySelector(".save-feedback");
      const quality = card.querySelector('input[type="radio"]:checked')?.value;
      const issues = [...card.querySelectorAll(".issue-options input:checked")].map((input) => input.value);
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
    $("modelDetail").textContent = `温度 ${auditData.controls.temperature} · 不开启思考 · 无自动质量判断`;
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
  if (!window.confirm("确定清空这次10轮的全部人工审核结果吗？")) return;
  userReview = await request("/api/user-review", "DELETE");
  renderProgress();
  renderTurns();
});

initialize();

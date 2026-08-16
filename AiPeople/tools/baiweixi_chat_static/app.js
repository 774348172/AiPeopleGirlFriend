const $ = (id) => document.getElementById(id);
const state = {
  saveId: localStorage.getItem("baiweixi_chat_save") || newSaveId(),
  messages: [],
  busy: false,
};

function newSaveId() {
  return `interactive_${crypto.randomUUID().replaceAll("-", "").slice(0, 20)}`;
}

function storageKey() {
  return `baiweixi_chat_messages_${state.saveId}`;
}

function loadMessages() {
  try { state.messages = JSON.parse(localStorage.getItem(storageKey()) || "[]"); }
  catch { state.messages = []; }
  renderMessages();
}

function saveMessages() {
  localStorage.setItem(storageKey(), JSON.stringify(state.messages));
}

function worldPayload() {
  const [location_id, location_label] = $("locationPreset").value.split("|");
  return {
    location_id,
    location_label,
    activity: $("activity").value.trim() || "站着",
    body: $("body").value.trim() || "没有明显不适",
    held_item: $("heldItem").value.trim(),
    scene: $("scene").value.trim() || "场景没有明显变化",
  };
}

async function api(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
  });
  const value = await response.json();
  if (!response.ok) {
    const detail = typeof value.detail === "string" ? value.detail : JSON.stringify(value.detail || value);
    throw new Error(detail);
  }
  return value;
}

function renderMessages() {
  const root = $("messages");
  root.querySelectorAll(".message").forEach((item) => item.remove());
  $("emptyState").hidden = state.messages.length > 0;
  for (const item of state.messages) {
    const row = document.createElement("div");
    row.className = `message ${item.role}${item.error ? " error" : ""}`;
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.textContent = item.text;
    const meta = document.createElement("span");
    meta.className = "message-meta";
    meta.textContent = item.meta || (item.role === "user" ? "你" : "白未晞");
    row.append(bubble, meta);
    root.append(row);
  }
  root.scrollTop = root.scrollHeight;
}

function applyStatus(value) {
  $("saveId").textContent = value.save_id;
  $("gameTime").textContent = value.game_time;
  if (value.world) {
    $("worldVersion").textContent = `v${value.world.version}`;
    const option = `${value.world.location_id}|${value.world.location_label}`;
    if ([...$("locationPreset").options].some((item) => item.value === option)) {
      $("locationPreset").value = option;
    }
    $("activity").value = value.world.activity;
    $("body").value = value.world.body;
    $("heldItem").value = value.world.held_item;
    $("scene").value = value.world.scene;
  }
  if (value.heroine) {
    const mind = value.heroine.living_mind;
    const relation = value.heroine.relationship;
    $("mindVersion").textContent = `v${value.heroine.version}`;
    $("mindState").innerHTML = `
      <div><dt>情感</dt><dd>${escapeHtml(mind.emotion)}</dd></div>
      <div><dt>注意</dt><dd>${escapeHtml(mind.attention)}</dd></div>
      <div><dt>活动</dt><dd>${escapeHtml(mind.current_activity)}</dd></div>
      <div><dt>意图</dt><dd>${escapeHtml(mind.immediate_intent)}</dd></div>
      <div><dt>关系</dt><dd>${escapeHtml(relation.stage)} · ${escapeHtml(relation.trust)}</dd></div>`;
  }
  const reconcile = value.reconcile_queue || {};
  const memory = value.memory_queue || {};
  $("reconcileQueue").textContent = `${reconcile.pending || 0} 待处理 / ${reconcile.failed || 0} 失败`;
  $("memoryQueue").textContent = `${memory.pending || 0} 待处理 / ${memory.failed || 0} 失败`;
}

function escapeHtml(value) {
  const node = document.createElement("span");
  node.textContent = value || "--";
  return node.innerHTML;
}

async function refreshStatus(includeWorld = false) {
  const payload = {save_id: state.saveId};
  if (includeWorld) payload.world = worldPayload();
  applyStatus(await api("/api/status", payload));
}

async function sendMessage(event) {
  event.preventDefault();
  const text = $("messageInput").value.trim();
  if (!text || state.busy) return;
  state.busy = true;
  $("sendButton").disabled = true;
  $("messageInput").disabled = true;
  state.messages.push({role: "user", text, meta: "你"});
  saveMessages();
  renderMessages();
  $("messageInput").value = "";
  resizeInput();
  const waiting = {role: "heroine", text: "……", meta: "白未晞正在回应"};
  state.messages.push(waiting);
  renderMessages();
  try {
    const value = await api("/api/chat", {save_id: state.saveId, text, world: worldPayload()});
    state.messages[state.messages.length - 1] = {
      role: "heroine",
      text: value.reply,
      meta: `白未晞 · ${(value.visible_ms / 1000).toFixed(2)} 秒`,
    };
    $("lastLatency").textContent = `${(value.visible_ms / 1000).toFixed(2)} 秒`;
    $("latencyDetail").textContent = `模型 ${(value.model_ms / 1000).toFixed(2)} 秒 · 提交 ${value.commit_ms.toFixed(1)} ms`;
    applyStatus(value.status);
  } catch (error) {
    state.messages[state.messages.length - 1] = {
      role: "heroine",
      text: `本轮没有提交：${error.message}`,
      meta: "系统失败",
      error: true,
    };
  } finally {
    saveMessages();
    renderMessages();
    state.busy = false;
    $("sendButton").disabled = false;
    $("messageInput").disabled = false;
    $("messageInput").focus();
  }
}

function resizeInput() {
  const input = $("messageInput");
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 150)}px`;
}

async function bootstrap() {
  localStorage.setItem("baiweixi_chat_save", state.saveId);
  loadMessages();
  $("saveId").textContent = state.saveId;
  try {
    const health = await fetch("/api/health").then((response) => response.json());
    $("modelStatus").textContent = `${health.model} · ${health.protocol}`;
    document.querySelector(".status-dot").classList.add("ready");
    await refreshStatus(false);
  } catch (error) {
    $("modelStatus").textContent = `连接失败：${error.message}`;
  }
  setInterval(() => refreshStatus(false).catch(() => {}), 5000);
}

$("composer").addEventListener("submit", sendMessage);
$("messageInput").addEventListener("input", resizeInput);
$("messageInput").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    $("composer").requestSubmit();
  }
});
$("newSave").addEventListener("click", async () => {
  state.saveId = newSaveId();
  state.messages = [];
  localStorage.setItem("baiweixi_chat_save", state.saveId);
  saveMessages();
  renderMessages();
  $("lastLatency").textContent = "--";
  $("latencyDetail").textContent = "";
  await refreshStatus(true);
});

bootstrap();

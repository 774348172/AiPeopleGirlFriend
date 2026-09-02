const $ = (id) => document.getElementById(id);

const state = {
  sessionId: newSessionId(),
  messages: [],
  busy: false,
};

function newSessionId() {
  return `manual_${crypto.randomUUID().replaceAll("-", "")}`;
}

function worldPayload() {
  return {
    location: $("location").value.trim() || "出租屋客厅",
    activity: $("activity").value.trim() || "坐着休息",
    body: $("body").value.trim() || "没有明显不适",
    scene: $("scene").value.trim() || "场景没有明显变化。",
    memories: $("memories").value.trim(),
  };
}

async function post(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
  });
  const value = await response.json();
  if (!response.ok) throw new Error(typeof value.detail === "string" ? value.detail : JSON.stringify(value.detail));
  return value;
}

function render() {
  const root = $("messages");
  root.querySelectorAll(".message").forEach((node) => node.remove());
  $("emptyState").hidden = state.messages.length > 0;
  for (const item of state.messages) {
    const row = document.createElement("div");
    row.className = `message ${item.role}${item.error ? " error" : ""}`;
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.textContent = item.text;
    const meta = document.createElement("span");
    meta.textContent = item.meta;
    row.append(bubble, meta);
    root.append(row);
  }
  root.scrollTop = root.scrollHeight;
}

async function send(event) {
  event.preventDefault();
  const text = $("messageInput").value.trim();
  if (!text || state.busy) return;
  state.busy = true;
  $("sendButton").disabled = true;
  state.messages.push({role: "user", text, meta: "玩家"});
  state.messages.push({role: "assistant", text: "……", meta: "生成中"});
  $("messageInput").value = "";
  render();
  try {
    const value = await post("/api/chat", {
      session_id: state.sessionId,
      text,
      world: worldPayload(),
    });
    state.messages[state.messages.length - 1] = {
      role: "assistant",
      text: value.reply,
      meta: `白未晞 · ${(value.elapsed_ms / 1000).toFixed(2)} 秒`,
    };
    $("turnCount").textContent = `第 ${value.turn} 轮 · 上下文 ${value.history_messages / 2} 轮`;
    $("latency").textContent = `${(value.elapsed_ms / 1000).toFixed(2)} 秒`;
  } catch (error) {
    state.messages[state.messages.length - 1] = {role: "assistant", text: error.message, meta: "调用失败", error: true};
  } finally {
    state.busy = false;
    $("sendButton").disabled = false;
    render();
    $("messageInput").focus();
  }
}

async function reset() {
  await post("/api/reset", {session_id: state.sessionId}).catch(() => {});
  state.sessionId = newSessionId();
  state.messages = [];
  $("sessionId").textContent = state.sessionId;
  $("turnCount").textContent = "第 0 轮";
  $("latency").textContent = "--";
  render();
}

async function bootstrap() {
  $("sessionId").textContent = state.sessionId;
  try {
    const value = await fetch("/api/health").then((response) => response.json());
    $("modelLabel").textContent = value.label;
    $("modelName").textContent = value.model;
    $("protocol").textContent = value.protocol;
    $("statusText").textContent = "已就绪";
    $("statusDot").classList.add("ready");
    document.title = `${value.label} · 白未晞手测`;
  } catch (error) {
    $("statusText").textContent = `连接失败：${error.message}`;
  }
}

$("composer").addEventListener("submit", send);
$("newSession").addEventListener("click", reset);
$("messageInput").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    $("composer").requestSubmit();
  }
});

bootstrap();

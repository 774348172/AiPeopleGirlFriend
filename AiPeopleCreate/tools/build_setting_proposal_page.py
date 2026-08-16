# -*- coding: utf-8 -*-
"""设定补齐建议勾选页生成器（自包含 HTML，判定存 localStorage 可导出）。

内容源：默认内嵌 PROPOSALS（白未晞设定补齐建议 2026-08-15）；
可用 --content <json> 传入自定义建议列表（[{no,title,tag,color,current,blocks,followup,choices}]），
--title/--key 覆盖页标题与 localStorage 键（如主角设定建议）。

用法:
  python tools/build_setting_proposal_page.py
  python tools/build_setting_proposal_page.py --content 设计文档/主角设定补齐建议_20260815.json \
      --title "主角（玩家）设定补齐建议（2026-08-15）" --key protagonist_setting_proposal_20260815
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "设计文档" / "白未晞设定补齐建议_20260815_review.html"
PAGE_KEY = "baiweixi_setting_proposal_20260815"
PAGE_TITLE = "白未晞 设定补齐建议"

# ── 建议内容（与建议稿一致）──

PROPOSALS = [
    {
        "no": 1,
        "title": "现代物品知识状态表",
        "tag": "item 类数据正典依据",
        "color": "#0d9488",
        "current": "bible.yaml city_life.modern_knowledge_gaps 只有 5 条粗粒度；timeline 只冻结了 Day 4-5“学会水龙头、热水壶、冰箱”。item 池 59 条话题的“会用/在学/没见过”全靠池条目 evidence_state 猜。",
        "blocks": [
            ("bible.yaml 新增 modern_items 小节", """modern_items:
  learned:   # 已学会，能独立使用
    - 水龙头、热水壶、冰箱          # timeline Day 4-5 已冻结
    - 电灯开关、插头和插座          # 学会用电的基础
    - 吹风机、热水器调温            # 主角教过，已会用
    - 电梯楼层按钮、过街按钮、门禁刷卡  # 已学会，偶尔不熟练
    - 电视开关与遥控器换台          # 能自己开电视看
  learning:  # 在学：会用一点、会出错、认真记
    - 手机（解锁/看时间/拍照/充电）   # 学手机基础操作中，不会打字和支付
    - 洗衣机、微波炉、电饭煲、燃气灶  # 厨房电器在学，怕火
    - 扫码支付、纸币硬币找零、数找零   # 付钱在学，经常算错
    - 空调、吸尘器、加湿器、空气炸锅、挂烫机、电子秤、电蚊香、遥控器换电池
    - 电视遥控器找不到了会帮着找，但不懂菜单设置
    - 快递（取件/拆箱/驿站）        # 知道“东西会送到家”，不懂流程
    - 地铁、公交、过闸机、红绿灯、出租车计价器、共享单车、外卖App、语音助手
    - 图书馆、医院挂号机、公园健身器材、自动扶梯、小区饮水机、快递柜、取款机、自助结账机、会员卡
  observed:  # 会认不会用/不理解原理
    - 路灯、广告牌、停车场的杆子     # 知道是“给人和车用的”，不懂为什么自动"""),
        ],
        "followup": "item 池 59 条 evidence_state 按上表对齐（现有 8 条 supported 核对，其余 insufficient 不变）。",
        "choices": [],
    },
    {
        "no": 2,
        "title": "通识知识边界",
        "tag": "general 类数据正典依据",
        "color": "#64748b",
        "current": "只有 knowledge_contrast（“会语言、识字、妖族常识和部分古老知识”）。general 池 30 条哪些懂哪些不懂无依据。",
        "blocks": [
            ("bible.yaml 新增 general_knowledge_boundary 小节", """general_knowledge_boundary:
  observed_nature:   # 深山生活观察可得 → 知道，表述朴素
    - 月亮圆缺、星星、影子、回声
    - 水烧开冒泡、鱼用鳃呼吸、昆虫趋光
    - 猫头鹰白天睡晚上活动、鱼干是晒干的
    - 看天色/风/云辨天气迹象（但说不清成因）
  modern_science:    # 现代科学成因 → 不知道，诚实说不懂或凭感觉猜
    - 为什么下雨、为什么下雪、天为什么是蓝的
    - 电器原理、手机原理
  inherited_common:  # 妖族传承常识 → 知道但表述古旧
    - 灵气/妖气/妖力、野外草药与生存知识
    - 妖族习俗与忌讳（部分，残缺）"""),
        ],
        "followup": "general 池 30 条 evidence_state 按上表对齐（observed_nature→supported/缺省，modern_science→insufficient）。",
        "choices": [],
    },
    {
        "no": 3,
        "title": "特殊记忆细节补全",
        "tag": "special 类数据支撑面",
        "color": "#a855f7",
        "current": "timeline.yaml 的 ev:bwx_first_help 标注“具体帮助内容尚未冻结”，memory 池有 2 个话题引用它——教师只能模糊带过；高频回忆事件缺感官细节层，grounding 支撑面窄。",
        "blocks": [
            ("3a：冻结 ev:bwx_first_help 具体内容（三选一）", """A：某天打烊后，她以猫形把主角忘在柜台下的钥匙推出来，主角才发现钥匙丢了
B：主角搬货箱时纸袋破角，咖啡豆撒了一地，她以猫形帮忙把散豆拢回袋边
C：营业时间有老鼠窜进店里吓到顾客，她以猫形把老鼠赶出店门（顾客眼里只是普通猫抓老鼠）"""),
            ("3b：高频事件补感官细节（只补感受，不碰妖果/名字来源未知边界）", """ev:bwx_accident（雨夜）  雨很大，路灯昏黄，积水没过脚踝；车灯白得刺眼，声音震得她耳朵疼
ev:bwx_rescued（救助）   外套带着咖啡和雨水的味道；纸箱里垫着旧衣服，有太阳晒过的气味
ev:bwx_first_conversation（初遇）  她说出名字时声音很轻；灯下她的眼睛亮晶晶的
ev:bwx_spirit_fruit（觉醒）  只记得饿得发昏、醒来脑子里多了很多东西、身体发烫；果子的样子和味道记不清
ev:bwx_city_stray_life（城市流浪）  屋檐滴水、楼道灯忽明忽暗、冬天冷得蜷成一团"""),
        ],
        "followup": "timeline.yaml 对应事件 summary 补细节（或新增 detail 字段）；memory 池可新增 2-3 条引用 first_help 的话题。",
        "choices": [
            {"key": "3a", "label": "3a 冻结帮助内容", "options": ["A", "B", "C"]},
            {"key": "3b", "label": "3b 感官细节", "toggle": True},
        ],
    },
    {
        "no": 4,
        "title": "能力边界封闭条款",
        "tag": "堵能力编造漏网",
        "color": "#ef4444",
        "current": "abilities.minor_spells 5 项 + limits 7 项，无“未列出能力不得声称”的封闭句。复核表 #10“妖力屏蔽声音”漏网导出。",
        "blocks": [
            ("bible.yaml abilities.limits 追加 + canon.json ability_limits 同步", """  limits:
    # ...现有 7 条...
    - 能力以 minor_spells 清单为准：未列入的能力（屏蔽声音、感知天气温度、
      读心之外的超自然感知等）不主动使用或声称——传承里没有的就是没有"""),
        ],
        "followup": "reply.py 可加生成侧守卫（“妖力/法术+未列能力动词”→重试），或先靠 G5/gold 集拦截。",
        "choices": [],
    },
    {
        "no": 5,
        "title": "被挽留时的反应模式",
        "tag": "#35 失败根因",
        "color": "#f43f5e",
        "current": "wants_to_stay 与嘴硬 denial 已有，但“被玩家明确挽留时怎么反应”未设定。抽检 #35（“你留她”）教师生成“其实已经不想走了”（直白承认）被否；重生成 6 次全部失败。",
        "blocks": [
            ("5a：bible.yaml situation_reactions 新增 wanted_to_stay", """  wanted_to_stay:   # 被明确挽留/被说“这里就是你的家”时
    - 不会直接承认想留下：先沉默（可能很久），然后转移话题（“……再说吧”“先吃饭吧”）或只应“嗯”
    - 会用行动回应：把拖鞋摆回原位、把包袱放回纸箱边、留在原地没走
    - 放松时可能漏半句真心（“……也不是不能待”），但不会说“其实我已经不想走了”这种直白话"""),
            ("5b：bible.yaml speech_patterns 新增 wanted_to_stay 组", """  wanted_to_stay:   # 被挽留时（嘴硬到漏出真心，但不直白承认）
    - "……再说吧。"
    - "嗯。（没走）"
    - "这里……还行。"
    - "谁说不走了。只是现在还没打算走。"
    - "你少说这种话。（耳朵动了动）"
    - "……随你便。（把包袱放回纸箱边）" """),
        ],
        "followup": "reply_merge.txt 的 wants_to_stay 约束段补充“被挽留时”行为句；重生成 #35 验证。",
        "choices": [
            {"key": "5a", "label": "5a 行为规则", "toggle": True},
            {"key": "5b", "label": "5b 挽留句式", "toggle": True},
        ],
    },
    {
        "no": 6,
        "title": "世界设定：城市设施一句话",
        "tag": "item 话题世界支撑",
        "color": "#3b82f6",
        "current": "世界设定/松江府/ 明说“只建设出租屋、街角咖啡厅和少量剧情场景”，bible/canon 无公共设施条目；item 池 59 条引用了地铁/商场/火车站/图书馆/医院/超市/公园。",
        "blocks": [
            ("世界设定/松江府/bible.yaml 城市规模段追加", """  city_scale: 现代大都市：地铁、公交、商圈、医院、图书馆、公园、火车站等公共设施齐备；
    故事场景当前只重点建设出租屋与街角咖啡厅，设施只作背景存在，不展开具体设定"""),
        ],
        "followup": "无数据连锁；item 话题合法性获得世界正典支撑。",
        "choices": [],
    },
    {
        "no": 7,
        "title": "小项：主角家电清单 + 手机学习状态",
        "tag": "配套补充",
        "color": "#10b981",
        "current": "主角 bible 未列家中设施；timeline 只定了水龙头/热水壶/冰箱，但 romance 池有“她学手机”、item 池有手机充电/拍照/语音助手。",
        "blocks": [
            ("7a：人物设定/主角/bible.yaml living 段追加", """  home_appliances: 出租屋为带基础家电的老旧小户型：冰箱、洗衣机、微波炉、电饭煲、
    热水壶、空调、电视等均有（明细以白未晞 item 池为准，不逐件冻结）"""),
            ("7b：timeline.yaml 新增事件 ev:bwx_phone_learning", """  - date: Day 6-7
    summary: 开始学手机基础操作（解锁、看时间、拍照），仍不会打字和支付；对手机里有人声应答感到新奇
    valence: 0.4
    people: [主角]
    importance: 5
    tags: [手机, 现代生活, 在学]
    id: ev:bwx_phone_learning
    visibility: profile_private
    disclosure_policy: direct_allowed"""),
        ],
        "followup": "7a 不改数据；7b 会改 profile snapshot hash → 已生成批次 lock 变化（正典变更的正常连锁，受影响批次重跑）。",
        "choices": [
            {"key": "7a", "label": "7a 主角家电清单", "toggle": True},
            {"key": "7b", "label": "7b 手机学习事件", "toggle": True},
        ],
    },
]

CSS = """
  body { font-family: "Microsoft YaHei", sans-serif; margin: 0; background: #f1f5f9; color: #1e293b; }
  header { background: #0f172a; color: #fff; padding: 14px 24px; position: sticky; top: 0; z-index: 10; }
  header h1 { margin: 0; font-size: 18px; }
  #progress { margin-top: 6px; font-size: 13px; color: #94a3b8; }
  #tips { margin-top: 4px; font-size: 12px; color: #cbd5e1; }
  .toolbar { display: flex; gap: 8px; margin-top: 8px; flex-wrap: wrap; }
  .btn { padding: 6px 14px; border: none; border-radius: 6px; cursor: pointer; font-size: 13px; }
  .btn.primary { background: #3b82f6; color: #fff; }
  .btn.gray { background: #334155; color: #fff; }
  .btn.green { background: #16a34a; color: #fff; }
  main { max-width: 900px; margin: 20px auto; padding: 0 16px; }
  .card { background: #fff; border-radius: 10px; padding: 18px 20px; margin-bottom: 16px;
          box-shadow: 0 1px 3px rgba(0,0,0,.08); border-left: 4px solid #cbd5e1; }
  .card.adopt { border-left-color: #22c55e; }
  .card.reject { border-left-color: #ef4444; }
  .card h2 { margin: 0 0 8px; font-size: 16px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .tag { padding: 2px 8px; border-radius: 999px; color: #fff; font-size: 12px; }
  .sec { font-size: 12px; color: #64748b; margin: 10px 0 4px; font-weight: 600; }
  .current { background: #fef3c7; border-radius: 8px; padding: 10px 12px; font-size: 13px; line-height: 1.7; color: #78350f; }
  pre.yaml { background: #0f172a; color: #e2e8f0; border-radius: 8px; padding: 12px 14px; font-size: 12.5px;
             line-height: 1.6; overflow-x: auto; margin: 6px 0 0; white-space: pre; font-family: Consolas, monospace; }
  .followup { background: #ecfdf5; border-radius: 8px; padding: 8px 12px; font-size: 12.5px; color: #065f46; margin-top: 8px; }
  .choices { margin-top: 10px; display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
  .choice { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; padding: 4px 10px;
            border: 1px solid #cbd5e1; border-radius: 999px; cursor: pointer; user-select: none; background: #fff; }
  .choice.on { border-color: #0d9488; background: #ccfbf1; color: #134e4a; }
  .verdict { margin-top: 14px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .vbtn { padding: 6px 18px; border: 2px solid #cbd5e1; border-radius: 8px; cursor: pointer; font-size: 14px; background: #fff; }
  .vbtn.on-adopt { border-color: #22c55e; background: #dcfce7; color: #166534; }
  .vbtn.on-reject { border-color: #ef4444; background: #fee2e2; color: #991b1b; }
  .note { flex: 1; min-width: 180px; padding: 6px 10px; border: 1px solid #cbd5e1; border-radius: 6px; }
"""

# 注意：本模板是普通字符串（非 f-string），花括号一律单层；__CARDS__ 由 build_html 替换
JS_TEMPLATE = """
const KEY = "__KEY__";
const ITEMS = __CARDS__;
let state = {};
try { state = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch(e) {}

function render() {
  const main = document.getElementById("main");
  main.innerHTML = "";
  let judged = 0;
  ITEMS.forEach(it => {
    const v = state[it.no] || {};
    if (v.verdict) judged++;
    const card = document.createElement("div");
    card.className = "card " + (v.verdict === "adopt" ? "adopt" : v.verdict === "reject" ? "reject" : "");
    let blocksHtml = "";
    it.blocks.forEach(b => {
      blocksHtml += `<div class="sec">${b[0]}</div><pre class="yaml">${b[1]}</pre>`;
    });
    let choicesHtml = "";
    it.choices.forEach(c => {
      const key = c.key;
      if (c.toggle) {
        const on = (v.choices || {})[key] === true;
        choicesHtml += `<span class="choice ${on ? "on" : ""}" data-no="${it.no}" data-key="${key}" data-toggle="1">${c.label} ${on ? "✓" : "☐"}</span>`;
      } else {
        const cur = (v.choices || {})[key] || "";
        const opts = c.options.map(o =>
          `<span class="choice ${cur === o ? "on" : ""}" data-no="${it.no}" data-key="${key}" data-opt="${o}">${o}</span>`
        ).join("");
        choicesHtml += `<span style="font-size:13px;color:#475569">${c.label}：</span>${opts}`;
      }
    });
    card.innerHTML = `
      <h2><span class="tag" style="background:${it.color}">${it.tag}</span>
          <span>#${it.no} ${it.title}</span></h2>
      <div class="sec">现状</div>
      <div class="current">${it.current}</div>
      ${blocksHtml}
      <div class="followup">📎 采用后连锁：${it.followup}</div>
      ${choicesHtml ? `<div class="choices">${choicesHtml}</div>` : ""}
      <div class="verdict">
        <button class="vbtn ${v.verdict === "adopt" ? "on-adopt" : ""}" data-no="${it.no}" data-v="adopt">✓ 采用</button>
        <button class="vbtn ${v.verdict === "reject" ? "on-reject" : ""}" data-no="${it.no}" data-v="reject">✗ 不采用</button>
        <input class="note" data-no="${it.no}" placeholder="备注（可选）" value="${(v.note || "").replace(/"/g, "&quot;")}">
      </div>`;
    main.appendChild(card);
  });
  document.getElementById("progress").textContent = `已判定 ${judged} / ${ITEMS.length}`;
}

function save() {
  localStorage.setItem(KEY, JSON.stringify(state));
  render();
}

document.addEventListener("click", e => {
  const btn = e.target.closest(".vbtn");
  if (btn) {
    state[btn.dataset.no] = state[btn.dataset.no] || {};
    state[btn.dataset.no].verdict = btn.dataset.v;
    save();
  }
  const choice = e.target.closest(".choice");
  if (choice) {
    const no = choice.dataset.no, key = choice.dataset.key;
    state[no] = state[no] || {};
    state[no].choices = state[no].choices || {};
    if (choice.dataset.toggle) {
      state[no].choices[key] = !state[no].choices[key];
    } else {
      state[no].choices[key] = choice.dataset.opt;
    }
    save();
  }
  if (e.target.id === "nextBtn") {
    const first = ITEMS.find(it => !(state[it.no] || {}).verdict);
    if (first) document.querySelector(`[data-no="${first.no}"]`)?.scrollIntoView({behavior: "smooth"});
    else alert("全部已判定");
  }
  if (e.target.id === "exportBtn" || e.target.id === "copyBtn") {
    const out = ITEMS.map(it => {
      const v = state[it.no] || {};
      return { no: it.no, title: it.title, verdict: v.verdict || "", choices: v.choices || {}, note: v.note || "" };
    });
    const text = JSON.stringify(out, null, 2);
    if (e.target.id === "exportBtn") {
      const blob = new Blob([text], {type: "application/json"});
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "setting_proposal_result_baiweixi.json";
      a.click();
    } else {
      navigator.clipboard.writeText(text).then(() => alert("已复制 JSON"));
    }
  }
  if (e.target.id === "resetBtn") {
    if (confirm("确认重置全部判定？")) { state = {}; localStorage.removeItem(KEY); render(); }
  }
});

document.addEventListener("input", e => {
  if (e.target.classList.contains("note")) {
    state[e.target.dataset.no] = state[e.target.dataset.no] || {};
    state[e.target.dataset.no].note = e.target.value;
    localStorage.setItem(KEY, JSON.stringify(state));
  }
});

render();
"""

HTML_SHELL = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>白未晞设定补齐建议（2026-08-15）</title>
<style>__CSS__</style>
</head>
<body>
<header>
  <h1>__TITLE__（__COUNT__ 项）</h1>
  <div id="progress">已判定 0 / __COUNT__</div>
  <div id="tips">逐项选择 采用/不采用；带子选项的项（#3/#5/#7）可细选。判定存本地，完成后点「导出 JSON」把结果发回。</div>
  <div class="toolbar">
    <button class="btn primary" id="nextBtn">下一个未判定 ↓</button>
    <button class="btn primary" id="exportBtn">导出 JSON</button>
    <button class="btn green" id="copyBtn">复制结果</button>
    <button class="btn gray" id="resetBtn">重置</button>
  </div>
</header>
<main id="main"></main>
<script>__JS__</script>
</body>
</html>
"""


def build_html(proposals=None, *, title: str = PAGE_TITLE, page_key: str = PAGE_KEY) -> str:
    proposals = proposals if proposals is not None else PROPOSALS
    cards_js = json.dumps(proposals, ensure_ascii=False)
    js = JS_TEMPLATE.replace("__CARDS__", cards_js).replace("__KEY__", page_key)
    html = HTML_SHELL.replace("__CSS__", CSS).replace("__JS__", js)
    html = html.replace("__COUNT__", str(len(proposals))).replace("__TITLE__", title)
    return html


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--content", default=None, help="建议列表 JSON（[{no,title,tag,color,current,blocks,followup,choices}]）")
    ap.add_argument("--out", default=None, help="输出 HTML 路径")
    ap.add_argument("--title", default=PAGE_TITLE, help="页标题")
    ap.add_argument("--key", default=PAGE_KEY, help="localStorage 键")
    args = ap.parse_args()
    proposals = PROPOSALS
    if args.content:
        proposals = json.loads(Path(args.content).read_text(encoding="utf-8"))
    out = Path(args.out) if args.out else OUT
    html = build_html(proposals, title=args.title, page_key=args.key)
    out.write_text(html, encoding="utf-8", newline="\n")
    print(f"勾选页已生成: {out}")


if __name__ == "__main__":
    main()

using System;
using System.Collections.Generic;
using System.Text;
using AiPeople.Core;

namespace AiPeople.World
{
    public sealed class PendingAction
    {
        public string instanceId = Guid.NewGuid().ToString("N");
        public string status = "queued";
        public string actionId;
        public string description;
        public float remainingSeconds;
        public Dictionary<string, string> parameters = new Dictionary<string, string>();
        public string travelToLocationId = string.Empty;
        public string spawnEntityId = string.Empty;
    }

    /// <summary>
    /// 游戏世界状态（接口契约：世界唯一事实源）——进行中动作、物品实体、女主活动与反馈。
    /// 线程模型：HTTP 线程只做提交/投影（加锁短临界区）；时间推进 <see cref="Tick"/> 只在主线程调用。
    /// </summary>
    public sealed class GameWorldState
    {
        private const int MaxFeedback = 8;

        private readonly object _lock = new object();
        private readonly List<PendingAction> _pending = new List<PendingAction>();
        private readonly Dictionary<string, string> _itemStates = new Dictionary<string, string>();
        private readonly List<string> _feedback = new List<string>();
        private readonly List<Dictionary<string, string>> _playerFactReports = new List<Dictionary<string, string>>();
        private string _heroineActivity = "实际活动尚未采集";
        private string _heroineLocationId = string.Empty;
        private readonly List<ActionFactData> _receipts = new List<ActionFactData>();

        public void ObserveHeroine(string locationId) { lock (_lock) { _heroineLocationId = locationId; } }
        public void ObserveEntities(EntityFactData[] entities)
        {
            lock (_lock)
            {
                _itemStates.Clear();
                foreach (var e in entities) _itemStates[e.entity_id] = e.label + "；" + e.location_label + "；" + e.state;
            }
        }

        public void SetActionStatus(string instanceId, string status)
        {
            lock (_lock)
            {
                var action = _pending.Find(p => p.instanceId == instanceId);
                if (action != null)
                {
                    action.status = status;
                    if (_pending[0] == action) _heroineActivity = action.actionId + "：" + status;
                }
            }
        }

        public void ReadyForConfirmation(string instanceId)
        {
            lock (_lock)
            {
                var action = _pending.Find(p => p.instanceId == instanceId);
                if (action != null) { action.status = "awaiting_confirmation"; action.remainingSeconds = 0; }
            }
        }

        public bool ConfirmAction(string instanceId, bool succeeded, string reason, string resultEntityId = "")
        {
            lock (_lock)
            {
                var action = _pending.Find(p => p.instanceId == instanceId);
                if (action == null || (succeeded && action.status != "awaiting_confirmation")) return false;
                _pending.Remove(action);
                _heroineActivity = _pending.Count == 0 ? "当前没有执行动作" : "下一个动作等待执行";
                var receipt = ToFact(action);
                receipt.status = succeeded ? "completed" : "failed";
                receipt.reason = reason;
                receipt.result_entity_id = resultEntityId;
                _receipts.Add(receipt);
                if (_receipts.Count > 8) _receipts.RemoveAt(0);
                // Entity effects are observed from real scene objects, not copied
                // from the action catalog's proposed effects.
                PushFeedbackLocked($"动作{action.actionId}：{receipt.status}；{reason}");
                return true;
            }
        }

        private static ActionFactData ToFact(PendingAction p) => new ActionFactData {
            instance_id = p.instanceId, action_id = p.actionId, status = p.status,
            target_location_id = p.travelToLocationId, remaining_seconds = p.remainingSeconds,
            item_id = p.parameters.TryGetValue("item_id", out var item) ? item : "",
            result_entity_id = "", reason = "" };

        public ActionFactData[] ActionFacts() { lock (_lock) { return _pending.ConvertAll(ToFact).ToArray(); } }
        public ActionFactData[] ReceiptFacts() { lock (_lock) { return _receipts.ToArray(); } }

        public string HeroineActivity
        {
            get { lock (_lock) { return _heroineActivity; } }
        }

        public string HeroineLocationId
        {
            get { lock (_lock) { return _heroineLocationId; } }
        }

        /// <summary>提交动作（HTTP 线程可调用）。校验：存在性、必要参数、简单冲突。</summary>
        public bool Execute(string actionId, IDictionary<string, string> parameters, out string reason)
        {
            reason = string.Empty;
            ActionDefinition definition = ActionCatalog.Get(actionId);
            if (definition == null)
            {
                reason = $"动作不在游戏清单内：{actionId}";
            }
            else
            {
                foreach (string required in definition.requiredParameters)
                {
                    if (parameters == null || !parameters.ContainsKey(required) || string.IsNullOrEmpty(parameters[required]))
                    {
                        reason = $"缺少必要参数：{required}";
                        break;
                    }
                }
            }

            lock (_lock)
            {
                if (reason.Length == 0)
                {
                    foreach (PendingAction pending in _pending)
                    {
                        if (pending.actionId == actionId)
                        {
                            reason = "她已经在做同一件事";
                            break;
                        }
                    }
                }

                if (reason.Length == 0 && _pending.Count >= 3)
                {
                    reason = "她手头的事情已经排满";
                }

                if (reason.Length > 0)
                {
                    _receipts.Add(new ActionFactData { instance_id = Guid.NewGuid().ToString("N"),
                        action_id = actionId, status = "rejected", reason = reason,
                        target_location_id = "", item_id = "", result_entity_id = "" });
                    if (_receipts.Count > 8) _receipts.RemoveAt(0);
                    PushFeedbackLocked($"动作被拒绝：{reason}");
                    return false;
                }

                var action = new PendingAction
                {
                    actionId = definition.id,
                    description = definition.description,
                    remainingSeconds = actionId == "cook_meal" ? definition.durationSeconds : 0,
                    travelToLocationId = definition.travelToLocationId,
                    spawnEntityId = definition.spawnEntityId,
                };

                if (parameters != null)
                {
                    foreach (KeyValuePair<string, string> pair in parameters)
                    {
                        action.parameters[pair.Key] = pair.Value;
                    }
                }

                if (action.travelToLocationId.Length == 0
                    && action.parameters.TryGetValue("target_location_id", out string target))
                {
                    action.travelToLocationId = target;
                }

                _pending.Add(action);
                if (_pending.Count == 1) _heroineActivity = "动作已排队，尚未执行";

                return true;
            }
        }

        /// <summary>只推进 running 阶段；到期只进入待确认，不登记完成或物品效果。</summary>
        public List<PendingAction> Tick(float deltaSeconds)
        {
            var completed = new List<PendingAction>();
            if (deltaSeconds <= 0f)
            {
                return completed;
            }

            lock (_lock)
            {
                for (int i = _pending.Count - 1; i >= 0; i--)
                {
                    PendingAction pending = _pending[i];
                    if (pending.status != "running") continue;
                    pending.remainingSeconds = Math.Max(0, pending.remainingSeconds - deltaSeconds);
                    if (pending.remainingSeconds > 0f)
                    {
                        continue;
                    }

                    pending.status = "awaiting_confirmation";
                    completed.Add(pending);
                }

                if (_pending.Count == 0)
                {
                    _heroineActivity = "在屋里待着";
                }
                else
                {
                    _heroineActivity = _pending[0].actionId + "：" + _pending[0].status;
                }
            }

            return completed;
        }

        public bool SubmitPlayerFactReport(IDictionary<string, string> report, out string error)
        {
            error = string.Empty;
            foreach (string key in new[] { "report_id", "subject", "predicate", "value" })
            {
                if (report == null || !report.ContainsKey(key) || string.IsNullOrEmpty(report[key]))
                {
                    error = $"player fact report 需要 {key}";
                    return false;
                }
            }

            string status = report.ContainsKey("status") ? report["status"] : "pending_confirmation";
            if (status != "pending_confirmation")
            {
                error = "player fact report 必须保持 pending_confirmation";
                return false;
            }

            lock (_lock)
            {
                if (_playerFactReports.Count >= 8)
                {
                    _playerFactReports.RemoveAt(0);
                }

                _playerFactReports.Add(new Dictionary<string, string>(report));
            }

            return true;
        }

        public string ProjectJson()
        {
            lock (_lock)
            {
                var builder = new StringBuilder(512);
                builder.Append("{\"pending_actions\":[");
                for (int i = 0; i < _pending.Count; i++)
                {
                    PendingAction pending = _pending[i];
                    if (i > 0)
                    {
                        builder.Append(',');
                    }

                    builder.Append('{');
                    builder.Append("\"action_id\":").Append(JsonUtil.Str(pending.actionId)).Append(',');
                    builder.Append("\"instance_id\":").Append(JsonUtil.Str(pending.instanceId)).Append(',');
                    builder.Append("\"status\":").Append(JsonUtil.Str(pending.status)).Append(',');
                    builder.Append("\"description\":").Append(JsonUtil.Str(pending.description)).Append(',');
                    builder.Append("\"remaining_seconds\":").Append(pending.remainingSeconds.ToString("F1", System.Globalization.CultureInfo.InvariantCulture)).Append(',');
                    builder.Append("\"params\":{");
                    bool first = true;
                    foreach (KeyValuePair<string, string> pair in pending.parameters)
                    {
                        if (!first)
                        {
                            builder.Append(',');
                        }

                        first = false;
                        builder.Append(JsonUtil.Str(pair.Key)).Append(':').Append(JsonUtil.Str(pair.Value));
                    }

                    builder.Append("}}");
                }

                builder.Append("],\"item_states\":{");
                bool firstItem = true;
                foreach (KeyValuePair<string, string> pair in _itemStates)
                {
                    if (!firstItem)
                    {
                        builder.Append(',');
                    }

                    firstItem = false;
                    builder.Append(JsonUtil.Str(pair.Key)).Append(':').Append(JsonUtil.Str(pair.Value));
                }

                builder.Append("},\"heroine_activity\":").Append(JsonUtil.Str(_heroineActivity));
                builder.Append(",\"heroine_location_id\":").Append(JsonUtil.Str(_heroineLocationId));
                builder.Append(",\"recent_feedback\":[");
                for (int i = 0; i < _feedback.Count; i++)
                {
                    if (i > 0)
                    {
                        builder.Append(',');
                    }

                    builder.Append(JsonUtil.Str(_feedback[i]));
                }

                builder.Append("],\"player_fact_reports\":[");
                for (int i = 0; i < _playerFactReports.Count; i++)
                {
                    Dictionary<string, string> report = _playerFactReports[i];
                    if (i > 0)
                    {
                        builder.Append(',');
                    }

                    builder.Append('{');
                    bool firstReport = true;
                    foreach (KeyValuePair<string, string> pair in report)
                    {
                        if (!firstReport)
                        {
                            builder.Append(',');
                        }

                        firstReport = false;
                        builder.Append(JsonUtil.Str(pair.Key)).Append(':').Append(JsonUtil.Str(pair.Value));
                    }

                    builder.Append('}');
                }

                builder.Append("],\"unknown_facts\":[]}");
                return builder.ToString();
            }
        }

        /// <summary>测试/调试用：返回进行中动作的快照。</summary>
        public List<PendingAction> PendingSnapshot()
        {
            lock (_lock)
            {
                var copy = new List<PendingAction>(_pending.Count);
                foreach (PendingAction pending in _pending)
                {
                    copy.Add(new PendingAction
                    {
                        instanceId = pending.instanceId,
                        status = pending.status,
                        actionId = pending.actionId,
                        description = pending.description,
                        remainingSeconds = pending.remainingSeconds,
                        parameters = new Dictionary<string, string>(pending.parameters),
                        travelToLocationId = pending.travelToLocationId,
                        spawnEntityId = pending.spawnEntityId,
                    });
                }

                return copy;
            }
        }

        public Dictionary<string, string> ItemStatesSnapshot()
        {
            lock (_lock)
            {
                return new Dictionary<string, string>(_itemStates);
            }
        }

        private void PushFeedbackLocked(string message)
        {
            _feedback.Add(message);
            if (_feedback.Count > MaxFeedback)
            {
                _feedback.RemoveAt(0);
            }
        }
    }
}

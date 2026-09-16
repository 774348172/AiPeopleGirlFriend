using System;
using System.Collections.Generic;
using System.Text;
using AiPeople.Core;

namespace AiPeople.World
{
    public sealed class PendingAction
    {
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
        private string _heroineActivity = "在纸箱边坐着";
        private string _heroineLocationId = string.Empty;

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
                    PushFeedbackLocked($"动作被拒绝：{reason}");
                    return false;
                }

                var action = new PendingAction
                {
                    actionId = definition.id,
                    description = definition.description,
                    remainingSeconds = definition.durationSeconds,
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
                _heroineActivity = definition.description;
                if (action.travelToLocationId.Length > 0)
                {
                    _heroineLocationId = action.travelToLocationId;
                }

                return true;
            }
        }

        /// <summary>推进游戏时间（仅主线程）。返回本帧完成的动作，供导演生成实体。</summary>
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
                    pending.remainingSeconds -= deltaSeconds;
                    if (pending.remainingSeconds > 0f)
                    {
                        continue;
                    }

                    _pending.RemoveAt(i);
                    ApplyEffectsLocked(pending);
                    completed.Add(pending);
                }

                if (_pending.Count == 0)
                {
                    _heroineActivity = "在屋里待着";
                }
                else
                {
                    _heroineActivity = _pending[0].description;
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
                    builder.Append("\"description\":").Append(JsonUtil.Str(pending.description)).Append(',');
                    builder.Append("\"remaining_seconds\":").Append(pending.remainingSeconds.ToString("F1")).Append(',');
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

        private void ApplyEffectsLocked(PendingAction pending)
        {
            ActionDefinition definition = ActionCatalog.Get(pending.actionId);
            if (definition != null)
            {
                foreach (KeyValuePair<string, string> pair in definition.itemStateEffects)
                {
                    _itemStates[pair.Key] = pair.Value;
                }
            }

            if (pending.actionId == "pick_up_item"
                && pending.parameters.TryGetValue("item_id", out string itemId)
                && !string.IsNullOrEmpty(itemId))
            {
                _itemStates[itemId] = "在白未晞手里";
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

#if UNITY_EDITOR
using AiPeople.Core;
using NUnit.Framework;

namespace AiPeople.Tests
{
    public sealed class M2ResilienceTests
    {
        [Test]
        public void StatusError_PreservesLastCommittedSnapshot()
        {
            var model = new GameStateModel();
            var snapshot = new StatusResponse
            {
                save_id = "save-001",
                game_time = "第18日 20:10:00",
                world = new WorldStateView { location_label = "出租屋客厅" }
            };
            model.Apply(snapshot);
            model.ReportError("后端暂时不可用");

            Assert.AreSame(snapshot, model.Latest);
            Assert.AreEqual("后端暂时不可用", model.LastError);
        }

        [Test]
        public void SparseStatus_IsAcceptedForDisplayFallback()
        {
            var model = new GameStateModel();
            model.Apply(new StatusResponse { save_id = "save-001" });

            Assert.IsTrue(model.HasData);
            Assert.IsNull(model.Latest.world);
            Assert.IsNull(model.Latest.heroine);
        }
    }
}
#endif

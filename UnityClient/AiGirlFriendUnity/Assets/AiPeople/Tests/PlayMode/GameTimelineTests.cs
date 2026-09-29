#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using AiPeople.Core;
using AiPeople.World;
using NUnit.Framework;
using UnityEngine;
using Object = UnityEngine.Object;

namespace AiPeople.Tests
{
    public sealed class GameTimelineTests
    {
        [Test]
        public void CookingUsesClockAndCannotCompleteDuringPause()
        {
            var root = new GameObject("ClockTest");
            var actor = new GameObject("ClockActor");
            try
            {
                var clockObject = new GameObject("Clock"); clockObject.transform.SetParent(root.transform);
                var clock = clockObject.AddComponent<GameTimeline>(); clock.enabled=false; clock.ApplyUnityScale=false;
                var director=root.AddComponent<HeroineDirector>(); director.enabled=false; director.Timeline=clock;
                var stoveObject=new GameObject("TestStove"); stoveObject.transform.SetParent(root.transform);
                director.Stove=stoveObject.AddComponent<KitchenDevice>();
                director.Stove.Configure("kitchen_stove","燃气灶",true);
                var table=new GameObject("Table"); table.transform.SetParent(root.transform);
                var state=new GameWorldState();
                director.Configure(state,actor.transform,null,table.transform,root.transform,null);
                director.RegisterAnchor("apartment_kitchen",Vector3.zero);
                state.Execute("cook_meal",new Dictionary<string,string>(),out _);
                clock.Advance(100); director.SendMessage("Update");
                Assert.AreEqual("queued",state.PendingSnapshot()[0].status);
                clock.Initialize(new GameClockData { ready=true, elapsed_seconds=100 });
                clock.Advance(1); director.SendMessage("Update");
                Assert.AreEqual(1199,state.PendingSnapshot()[0].remainingSeconds);
                clock.Control(true,2);
                clock.Advance(2000); director.SendMessage("Update");
                Assert.AreEqual(101,clock.Seconds);
                Assert.AreEqual(1199,state.PendingSnapshot()[0].remainingSeconds);
                Assert.IsEmpty(state.ReceiptFacts());
                clock.Control(false,2);
                clock.Advance(599); director.SendMessage("Update");
                Assert.AreEqual(1,state.PendingSnapshot()[0].remainingSeconds);
                clock.Advance(.5f); director.SendMessage("Update");
                Assert.AreEqual(1300,clock.Seconds);
                Assert.AreEqual("completed",state.ReceiptFacts()[0].status);
                Assert.AreEqual(1,director.CaptureEntities(null).Length);
            }
            finally { Object.DestroyImmediate(root); Object.DestroyImmediate(actor); }
        }

        [Test]
        public void ClockRestoresWithoutRollbackAndKeepsPause()
        {
            string save="clock_test_"+Guid.NewGuid().ToString("N");
            var root=new GameObject("PersistedClock");
            try
            {
                var a=root.AddComponent<GameTimeline>(); a.ApplyUnityScale=false; a.Configure(save);
                a.Initialize(new GameClockData {ready=true,elapsed_seconds=100});
                a.Advance(20); a.Control(true,4);
                Object.DestroyImmediate(a);
                var b=root.AddComponent<GameTimeline>(); b.ApplyUnityScale=false; b.Configure(save);
                b.Initialize(new GameClockData {ready=true,elapsed_seconds=110});
                Assert.AreEqual(120,b.Seconds); Assert.IsTrue(b.Paused); Assert.AreEqual(4,b.Speed);
                b.Advance(1000); Assert.AreEqual(120,b.Seconds);
                Assert.IsFalse(b.Control(false,float.NaN)); Assert.IsFalse(b.Control(false,9));
            }
            finally
            {
                Object.DestroyImmediate(root);
                PlayerPrefs.DeleteKey("AiPeople.GameClock.v1."+save); PlayerPrefs.Save();
            }
        }
    }
}
#endif

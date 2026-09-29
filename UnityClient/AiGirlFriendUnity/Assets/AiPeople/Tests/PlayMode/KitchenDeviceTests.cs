#if UNITY_EDITOR
using System.Collections;
using System.Collections.Generic;
using AiPeople.World;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;

namespace AiPeople.Tests
{
    public sealed class KitchenDeviceTests
    {
        [Test]
        public void DevicePresenceDoesNotInventOperatingState()
        {
            var room=new GameObject("KitchenFridge");
            try
            {
                room.transform.position=new Vector3(-2.77f,0,3.85f);
                var fridge=room.AddComponent<KitchenDevice>();
                fridge.Configure("kitchen_fridge","冰箱",false);
                var fact=fridge.Capture(true);
                Assert.AreEqual("apartment_kitchen",fact.location_id);
                Assert.AreEqual("unknown",fact.operating_state);
                Assert.AreEqual("unknown",fact.door_state);
                Assert.IsFalse(fridge.SetRunning(true));
            }
            finally { Object.DestroyImmediate(room); }
        }

        [Test]
        public void AuthoredKitchenDeviceAtWallMarginStillReportsKitchen()
        {
            var room = new GameObject("KitchenKettle");
            try
            {
                room.transform.position = new Vector3(-3f, 0, 4.77f);
                var kettle = room.AddComponent<KitchenDevice>();
                kettle.Configure("kitchen_kettle", "电热水壶", false);
                var fact = kettle.Capture(true);
                Assert.AreEqual("apartment_kitchen", fact.location_id);
                Assert.AreEqual("unknown", fact.operating_state);
            }
            finally { Object.DestroyImmediate(room); }
        }

        [UnityTest]
        public IEnumerator ClosingStoveDuringCookingFailsWithoutCreatingBowl()
        {
            var root=new GameObject("KitchenAction");
            var actor=new GameObject("Cook");
            try
            {
                var state=new GameWorldState();
                var director=root.AddComponent<HeroineDirector>(); director.enabled=false;
                var stove=new GameObject("Stove"); stove.transform.SetParent(root.transform);
                var hood=new GameObject("Hood"); hood.transform.SetParent(root.transform);
                director.Stove=stove.AddComponent<KitchenDevice>();
                director.Stove.Configure("kitchen_stove","燃气灶",true);
                director.Hood=hood.AddComponent<KitchenDevice>();
                director.Hood.Configure("kitchen_hood","抽油烟机",true);
                director.Configure(state,actor.transform,null,root.transform,root.transform,null);
                director.RegisterAnchor("apartment_kitchen",Vector3.zero);
                state.Execute("cook_meal",new Dictionary<string,string>(),out _);
                director.SendMessage("Update"); director.SendMessage("Update");
                Assert.AreEqual("running",state.PendingSnapshot()[0].status);
                Assert.IsTrue(director.Stove.IsOn);
                Assert.IsTrue(director.Hood.IsOn);
                director.Stove.SetRunning(false,true);
                director.SendMessage("Update");
                Assert.IsEmpty(state.PendingSnapshot());
                Assert.AreEqual("failed",state.ReceiptFacts()[0].status);
                Assert.IsFalse(director.Hood.IsOn);
                Assert.IsEmpty(director.CaptureEntities(null));
                yield return null;
            }
            finally { Object.DestroyImmediate(root); Object.DestroyImmediate(actor); }
        }
    }
}
#endif

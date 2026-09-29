#if UNITY_EDITOR
using System.Collections;
using System.Collections.Generic;
using AiPeople.World;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;

namespace AiPeople.Tests
{
    public sealed class GameSnapshotTests
    {
        [Test]
        public void CalibratedRoomCentersAndDoorMarginsHaveExplicitLocations()
        {
            string[] expected = { "apartment_living_room", "apartment_kitchen", "apartment_master_bedroom",
                "apartment_secondary_bedroom", "apartment_bathroom", "apartment_balcony", "apartment_hall" };
            for (int i=0;i<expected.Length;i++)
                Assert.AreEqual(expected[i],ApartmentLocations.Locate("baiweixi",ImportedApartmentLayout.TraversalPoints[i].Position,true,"idle").location_id);
            Assert.AreEqual("apartment_interior",ApartmentLocations.Locate("baiweixi",new Vector3(2.87f,0,-.65f),true,"walking").location_id);
        }

        [UnityTest]
        public IEnumerator MoveCompletesAtPhysicalArrivalAndMissingTargetFails()
        {
            var root = new GameObject("SnapshotDirector");
            var actor = new GameObject("SnapshotActor");
            try
            {
                var state = new GameWorldState();
                var director = root.AddComponent<HeroineDirector>();
                director.Configure(state,actor.transform,null,null,root.transform,null);
                director.RegisterAnchor("apartment_kitchen",new Vector3(0,0,1));
                director.walkSpeed=2;
                Assert.IsTrue(state.Execute("move_to",new Dictionary<string,string>{{"target_location_id","apartment_kitchen"}},out _));
                var id=state.PendingSnapshot()[0].instanceId;
                yield return null;
                Assert.IsEmpty(state.ReceiptFacts());
                Assert.AreNotEqual("apartment_kitchen",state.HeroineLocationId);
                float until=Time.realtimeSinceStartup+3;
                while(state.PendingSnapshot().Count>0 && Time.realtimeSinceStartup<until) yield return null;
                Assert.Less(Vector3.Distance(actor.transform.position,new Vector3(0,0,1)),.13f);
                Assert.AreEqual("completed",state.ReceiptFacts()[0].status);
                Assert.IsFalse(state.ConfirmAction(id,true,"duplicate"));
                state.Execute("move_to",new Dictionary<string,string>{{"target_location_id","nonexistent"}},out _);
                yield return null;
                Assert.AreEqual("failed",state.ReceiptFacts()[1].status);
            }
            finally { Object.DestroyImmediate(root);Object.DestroyImmediate(actor); }
        }

        [UnityTest]
        public IEnumerator CookingCommitsOnlyAfterRealBowlExists()
        {
            var root=new GameObject("CookingDirector");
            var actor=new GameObject("CookingActor");
            try
            {
                var table=new GameObject("Table"); table.transform.SetParent(root.transform);table.transform.position=Vector3.up*.75f;
                var state=new GameWorldState();
                var director=root.AddComponent<HeroineDirector>();
                var stoveObject=new GameObject("TestStove"); stoveObject.transform.SetParent(root.transform);
                director.Stove=stoveObject.AddComponent<KitchenDevice>();
                director.Stove.Configure("kitchen_stove","燃气灶",true);
                director.Configure(state,actor.transform,null,table.transform,root.transform,null);
                director.RegisterAnchor("apartment_kitchen",Vector3.zero);
                state.Execute("cook_meal",new Dictionary<string,string>(),out _);
                yield return null;
                Assert.AreEqual("running",state.PendingSnapshot()[0].status);
                state.Tick(1200);
                Assert.AreEqual(0,director.CaptureEntities(null).Length);
                Assert.AreEqual(0,state.ReceiptFacts().Length);
                yield return null;
                var entities=director.CaptureEntities(null);
                Assert.AreEqual(1,entities.Length);
                Assert.AreEqual("一碗热汤面",entities[0].label);
                Assert.AreEqual(entities[0].entity_id,state.ReceiptFacts()[0].result_entity_id);
                Assert.AreEqual("completed",state.ReceiptFacts()[0].status);
                yield return null;
                Assert.AreEqual(1,director.CaptureEntities(null).Length,"Completion must not duplicate bowls");
            }
            finally { Object.DestroyImmediate(root);Object.DestroyImmediate(actor); }
        }

        [UnityTest]
        public IEnumerator PickupTransfersOneRealEntityAndNonexistentItemNeverAppears()
        {
            var root=new GameObject("EntityDirector");
            var actor=new GameObject("EntityActor");
            try
            {
                var state=new GameWorldState();
                var director=root.AddComponent<HeroineDirector>();
                director.Configure(state,actor.transform,null,null,root.transform,null);
                director.walkSpeed=3;
                var item=GameObject.CreatePrimitive(PrimitiveType.Cylinder);
                item.transform.SetParent(root.transform); item.transform.position=new Vector3(0,.7f,3);
                var entity=item.AddComponent<WorldEntity>();entity.entityId="water_cup";entity.displayName="水杯";entity.canPickUp=true;
                director.RegisterEntity(entity);
                state.Execute("pick_up_item",new Dictionary<string,string>{{"item_id","water_cup"}},out _);
                yield return null;
                Assert.AreEqual("moving",state.ActionFacts()[0].status,"Pickup must walk even without a target location ID");
                float until=Time.realtimeSinceStartup+3;
                while(state.PendingSnapshot().Count>0 && Time.realtimeSinceStartup<until) yield return null;
                Assert.AreEqual("baiweixi",entity.ownerId);
                Assert.AreEqual(actor.transform,entity.transform.parent);
                Assert.AreEqual("completed",state.ReceiptFacts()[0].status);
                Assert.AreEqual("water_cup",state.ReceiptFacts()[0].result_entity_id);
                Assert.AreEqual(1,director.CaptureEntities(null).Length);
                state.Execute("pick_up_item",new Dictionary<string,string>{{"item_id","missing_item"}},out _);
                yield return null;
                Assert.AreEqual("failed",state.ReceiptFacts()[1].status);
                Assert.AreEqual(1,director.CaptureEntities(null).Length);
            }
            finally {Object.DestroyImmediate(root);Object.DestroyImmediate(actor);}
        }
    }
}
#endif

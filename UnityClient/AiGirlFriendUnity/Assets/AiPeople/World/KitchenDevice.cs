using System;
using AiPeople.Core;
using UnityEngine;

namespace AiPeople.World
{
    // A model's presence does not imply that its power, door or contents are known.
    public sealed class KitchenDevice : MonoBehaviour
    {
        public string deviceId;
        public string displayName;
        public bool controllable;
        public bool IsOn { get; private set; }
        public int ManualRevision { get; private set; }
        public event Action Changed;

        private Renderer[] _indicators;
        private Transform _fan;

        public void Configure(string id, string label, bool canToggle)
        {
            deviceId = id;
            displayName = label;
            controllable = canToggle;
            if (!canToggle) return;

            gameObject.AddComponent<Interactable>().kind = InteractKind.ToggleDevice;
            var control = GameObject.CreatePrimitive(PrimitiveType.Cube);
            control.name = "PowerButton";
            control.transform.SetParent(transform, false);
            control.transform.localPosition = id == "kitchen_stove"
                ? new Vector3(.38f, .95f, .25f) : new Vector3(.39f, 1.80f, .23f);
            control.transform.localScale = new Vector3(.07f, .06f, .07f);
            _indicators = new Renderer[1];
            _indicators[0] = control.GetComponent<Renderer>();
            if (id == "kitchen_stove")
            {
                var burner = GameObject.CreatePrimitive(PrimitiveType.Cylinder);
                burner.name = "LitBurner";
                burner.transform.SetParent(transform, false);
                burner.transform.localPosition = new Vector3(0, 1.018f, .17f);
                burner.transform.localScale = new Vector3(.11f, .004f, .11f);
                burner.GetComponent<Collider>().enabled = false;
                Array.Resize(ref _indicators, 2);
                _indicators[1] = burner.GetComponent<Renderer>();
            }
            else
            {
                var fan = GameObject.CreatePrimitive(PrimitiveType.Cube);
                fan.name = "HoodFan";
                fan.transform.SetParent(transform, false);
                fan.transform.localPosition = new Vector3(.02f, 1.68f, 0);
                fan.transform.localScale = new Vector3(.03f, .025f, .28f);
                fan.GetComponent<Collider>().enabled = false;
                fan.GetComponent<Renderer>().sharedMaterial = MaterialLibrary.Get("Furniture/Steel", new Color(.44f,.50f,.50f));
                _fan = fan.transform;
            }
            RefreshVisual();
        }

        public bool SetRunning(bool running, bool manual = false)
        {
            if (!controllable) return false;
            if (manual) ManualRevision++;
            if (IsOn == running) return true;
            IsOn = running;
            RefreshVisual();
            Changed?.Invoke();
            return true;
        }

        private void RefreshVisual()
        {
            if (_indicators == null) return;
            Material material = IsOn
                ? MaterialLibrary.Get("Device/Running", new Color(.95f, .38f, .13f))
                : MaterialLibrary.Get("Device/Stopped", new Color(.30f, .34f, .35f));
            foreach (var indicator in _indicators) indicator.sharedMaterial = material;
        }

        private void Update()
        {
            if (IsOn && _fan != null) _fan.Rotate(Vector3.up, 540f * Time.deltaTime, Space.Self);
        }

        public DeviceFactData Capture(bool imported)
        {
            var room = ApartmentLocations.Locate("device", transform.position, imported, "");
            // These authored kitchen nodes can sit within the conservative wall margin.
            if (imported && room.location_id == "apartment_interior")
            {
                room.location_id = "apartment_kitchen";
                room.location_label = "出租屋厨房";
            }
            return new DeviceFactData {
                device_id = deviceId, label = displayName, location_id = room.location_id,
                location_label = room.location_label, x = room.x, y = room.y, z = room.z,
                operating_state = controllable ? IsOn ? "running" : "stopped" : "unknown",
                door_state = "unknown",
            };
        }
    }
}

using System.IO;
using System.Text;
using AiPeople.World;
using UnityEngine;

namespace AiPeople.EditorTools
{
    /// <summary>Measured correction for this reconstruction only, applied to original buffers each install.</summary>
    public static class ApartmentMetricCalibration
    {
        public static float ShellHeight(float y, float z)
        {
            // Keep slab, floor relief and thresholds untouched. Preserve the already metre-sized rail.
            if (y <= .12f) return y;
            float corrected;
            if (y <= .6f) corrected = Mathf.Lerp(.12f, .9f, (y - .12f) / .48f);
            else if (y <= 1.177f) corrected = Mathf.Lerp(.9f, 2.1f, (y - .6f) / .577f);
            else if (y <= 1.394f) corrected = Mathf.Lerp(2.1f, 2.4f, (y - 1.177f) / .217f);
            else corrected = 2.4f + (y - 1.394f) * .3f / (1.822f - 1.394f);
            // Blend along the short rail-to-house returns, without a discontinuous seam.
            return Mathf.Lerp(y, corrected, Mathf.SmoothStep(0, 1, Mathf.InverseLerp(-5.25f, -4.9f, z)));
        }

        public static void Audit(ApartmentBuilder.Result world, GameObject character)
        {
            var report = new StringBuilder("Calibration measurements in Unity metres; editor geometry, not a building survey.\n");
            var shell = world.Root.Find("Structure/ImportedApartmentShell");
            var geometry = shell.Find("ShellGeometry");
            report.AppendLine("shellBounds=" + geometry.GetComponent<Renderer>().bounds.ToString("F4"));
            foreach (var door in shell.GetComponentsInChildren<ApartmentDoor>())
            {
                var visible = door.leaf.GetComponent<Renderer>().bounds;
                var physical = door.leaf.GetComponent<Collider>().bounds;
                report.AppendLine($"door={door.doorId} bottom={visible.min.y:F4} top={visible.max.y:F4} height={visible.size.y:F4} collisionTop={physical.max.y:F4}");
            }
            foreach (var r in character.GetComponentsInChildren<Renderer>())
                report.AppendLine("character=" + r.name + " bounds=" + r.bounds.ToString("F4"));
            foreach (string name in new[] { "BalconyFrontGuard", "BalconyLeftGuard", "BalconyRightGuard" })
                report.AppendLine(name + " bounds=" + shell.Find(name).GetComponent<Collider>().bounds.ToString("F4"));
            // Entrance-facing surface at the end of the actual narrow hall, not just a nearby waypoint.
            foreach (float eye in new[] { .95f, 1.5f })
            {
                var fromHall = ImportedApartmentLayout.EntryPosition + Vector3.up * eye;
                if (!Physics.Raycast(fromHall, Vector3.forward, out var exit, 4))
                    throw new System.Exception("Entrance surface missing");
                var fromBox = world.CardboardBox.transform.position;
                fromBox.y = eye;
                var delta = exit.point - fromBox;
                bool blocked = Physics.Raycast(fromBox, delta.normalized, out var hit, delta.magnitude - .04f,
                    ~0, QueryTriggerInteraction.Ignore);
                report.AppendLine($"boxEye={eye:F2} exit={exit.point.ToString("F4")} visible={!blocked} blocker={(blocked ? hit.collider.name : "none")} hit={hit.point.ToString("F4")}");
            }
            var camera = new GameObject("CalibrationCamera").AddComponent<Camera>();
            camera.transform.position = ImportedApartmentLayout.CameraPosition;
            camera.transform.LookAt(ImportedApartmentLayout.CameraTarget);
            foreach (float aspect in new[] { 16f / 9f, 16f / 10f, 21f / 9f })
            {
                camera.aspect = aspect;
                camera.fieldOfView = ImportedApartmentLayout.CameraFieldOfView(aspect);
                var feet = camera.WorldToViewportPoint(ImportedApartmentLayout.HeroinePosition);
                var head = camera.WorldToViewportPoint(ImportedApartmentLayout.HeroinePosition + Vector3.up * 1.62f);
                report.AppendLine($"aspect={aspect:F4} fov={camera.fieldOfView:F2} feet={feet.ToString("F4")} head={head.ToString("F4")} heightFraction={head.y-feet.y:F4}");
            }
            Object.DestroyImmediate(camera.gameObject);
            Directory.CreateDirectory("../output/apartment-calibration");
            File.WriteAllText("../output/apartment-calibration/measurements.txt", report.ToString());
            Debug.Log(report.ToString());
        }
    }
}

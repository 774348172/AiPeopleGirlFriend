using UnityEngine;
using UnityEngine.Rendering;

namespace AiPeople.World
{
    /// <summary>Keep the calibrated composition when the desktop resolution changes.</summary>
    [RequireComponent(typeof(Camera))]
    public sealed class ApartmentWallpaperCamera : MonoBehaviour
    {
        private Camera _camera;
        private float _aspect;

        private void Awake() { _camera = GetComponent<Camera>(); Refresh(); }
        private void OnEnable() => RenderPipelineManager.beginCameraRendering += BeforeRender;
        private void OnDisable() => RenderPipelineManager.beginCameraRendering -= BeforeRender;
        private void BeforeRender(ScriptableRenderContext context, Camera camera)
        {
            if (camera == _camera) Refresh();
        }
        private void OnPreCull() => Refresh();

        private void Refresh()
        {
            if (Mathf.Approximately(_aspect, _camera.aspect)) return;
            _aspect = _camera.aspect;
            _camera.fieldOfView = ImportedApartmentLayout.CameraFieldOfView(_aspect);
        }
    }
}

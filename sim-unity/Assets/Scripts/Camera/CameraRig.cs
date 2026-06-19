using UnityEngine;
#if ENABLE_INPUT_SYSTEM
using UnityEngine.InputSystem;
#endif

[DisallowMultipleComponent]
public sealed class CameraRig : MonoBehaviour
{
    public enum ViewMode { Chase, Fpv }

    public Transform drone;
    [SerializeField] Camera targetCamera;

    [Header("Chase")]
    public Vector3 chaseOffset = new(0f, 2.2f, -5.5f);
    public float chaseLerp = 8f;
    public float chaseLookHeight = 0.35f;

    [Header("FPV")]
    public Vector3 fpvLocalOffset = new(0f, 0.04f, 0.08f);
    public float fpvTiltDeg = 22f;

    ViewMode _view = ViewMode.Chase;

    void Reset()
    {
        targetCamera = Camera.main;
    }

    void Awake()
    {
        if (targetCamera == null)
            targetCamera = Camera.main;
    }

    void Update()
    {
        if (WasCameraTogglePressed())
            ToggleView();
    }

    static bool WasCameraTogglePressed()
    {
#if ENABLE_INPUT_SYSTEM
        return Keyboard.current != null && Keyboard.current.cKey.wasPressedThisFrame;
#elif ENABLE_LEGACY_INPUT_MANAGER
        return Input.GetKeyDown(KeyCode.C);
#else
        return false;
#endif
    }

    public void ToggleView()
    {
        _view = _view == ViewMode.Chase ? ViewMode.Fpv : ViewMode.Chase;
    }

    public void SetView(ViewMode mode) => _view = mode;

    void LateUpdate()
    {
        if (drone == null || targetCamera == null) return;

        if (_view == ViewMode.Fpv)
        {
            targetCamera.transform.position = drone.TransformPoint(fpvLocalOffset);
            targetCamera.transform.rotation = drone.rotation * Quaternion.Euler(-fpvTiltDeg, 0f, 0f);
        }
        else
        {
            Vector3 targetPos = drone.TransformPoint(chaseOffset);
            targetCamera.transform.position = Vector3.Lerp(
                targetCamera.transform.position,
                targetPos,
                Time.deltaTime * chaseLerp);
            targetCamera.transform.LookAt(drone.position + Vector3.up * chaseLookHeight);
        }
    }
}

using UnityEngine;

[DisallowMultipleComponent]
[DefaultExecutionOrder(-50)]
public sealed class DroneSimBootstrap : MonoBehaviour
{
    [Header("Quad geometry")]
    public float armLengthM = 0.14f;
    public bool createVisualProxy = true;

    [Header("Управление")]
    [Tooltip("Геймпад напрямую в Unity — основной режим.")]
    public bool useLocalGamepad = true;
    [Tooltip("UDP через backend — только если нужен тот же путь, что прод.")]
    public bool useBackendUdp = false;

    void Awake()
    {
        DisableLegacy();

        if (GetComponent<Rigidbody>() == null)
            gameObject.AddComponent<Rigidbody>();

        if (GetComponent<Collider>() == null)
        {
            var box = gameObject.AddComponent<BoxCollider>();
            box.size = new Vector3(0.16f, 0.05f, 0.16f);
        }

        DronePhysics physics = GetComponent<DronePhysics>() ?? gameObject.AddComponent<DronePhysics>();
        if (GetComponent<SimManager>() == null) gameObject.AddComponent<SimManager>();

        if (useLocalGamepad && GetComponent<LocalGamepadInput>() == null)
            gameObject.AddComponent<LocalGamepadInput>();

        if (useBackendUdp)
        {
            if (GetComponent<UdpBridge>() == null) gameObject.AddComponent<UdpBridge>();
            if (GetComponent<TelemetryPublisher>() == null) gameObject.AddComponent<TelemetryPublisher>();
        }

        EnsureMotorMounts(physics);
        if (createVisualProxy)
            EnsureVisualProxy();

        CameraRig camRig = FindFirstObjectByType<CameraRig>();
        if (camRig == null && Camera.main != null)
            camRig = Camera.main.gameObject.AddComponent<CameraRig>();

        if (camRig != null)
            camRig.drone = transform;
    }

    void DisableLegacy()
    {
        MonoBehaviour[] components = GetComponents<MonoBehaviour>();
        for (int i = 0; i < components.Length; i++)
        {
            string name = components[i].GetType().Name;
            if (name == "UnityUdpBridge" || name == "UnitySimDroneController")
                components[i].enabled = false;
        }
    }

    void EnsureMotorMounts(DronePhysics physics)
    {
        if (physics.motorMounts != null && physics.motorMounts.Length == 4 &&
            physics.motorMounts[0] != null)
            return;

        float a = armLengthM * 0.70710678f;
        physics.motorMounts = new[]
        {
            CreateMount("Motor_BR", new Vector3(+a, 0.02f, -a)),
            CreateMount("Motor_FR", new Vector3(+a, 0.02f, +a)),
            CreateMount("Motor_BL", new Vector3(-a, 0.02f, -a)),
            CreateMount("Motor_FL", new Vector3(-a, 0.02f, +a)),
        };
    }

    Transform CreateMount(string name, Vector3 localPos)
    {
        Transform t = transform.Find(name);
        if (t == null)
        {
            var go = new GameObject(name);
            go.transform.SetParent(transform, false);
            t = go.transform;
        }
        t.localPosition = localPos;
        return t;
    }

    void EnsureVisualProxy()
    {
        if (transform.Find("BodyProxy") != null) return;

        GameObject body = GameObject.CreatePrimitive(PrimitiveType.Cube);
        body.name = "BodyProxy";
        body.transform.SetParent(transform, false);
        body.transform.localScale = new Vector3(0.18f, 0.04f, 0.18f);
        Collider col = body.GetComponent<Collider>();
        if (col != null) Destroy(col);

        CreateProp("Prop_BR", new Vector3(+0.10f, 0.05f, -0.10f));
        CreateProp("Prop_FR", new Vector3(+0.10f, 0.05f, +0.10f));
        CreateProp("Prop_BL", new Vector3(-0.10f, 0.05f, -0.10f));
        CreateProp("Prop_FL", new Vector3(-0.10f, 0.05f, +0.10f));
    }

    void CreateProp(string name, Vector3 localPos)
    {
        GameObject cyl = GameObject.CreatePrimitive(PrimitiveType.Cylinder);
        cyl.name = name;
        cyl.transform.SetParent(transform, false);
        cyl.transform.localScale = new Vector3(0.20f, 0.008f, 0.20f);
        cyl.transform.localPosition = localPos;
        Collider col = cyl.GetComponent<Collider>();
        if (col != null) Destroy(col);
    }
}

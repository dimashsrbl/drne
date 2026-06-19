using UnityEngine;

[DisallowMultipleComponent]
public sealed class TelemetryPublisher : MonoBehaviour
{
    [SerializeField] UdpBridge bridge;
    [SerializeField] SimManager sim;

    [Header("Rate")]
    public float rateHz = 30f;

    [Header("Fake GPS origin")]
    public double originLat = 51.1694;
    public double originLon = 71.4491;

    float _acc;

    void Reset()
    {
        bridge = GetComponent<UdpBridge>();
        sim = GetComponent<SimManager>();
    }

    void Update()
    {
        if (bridge == null || sim == null || rateHz <= 0f) return;

        _acc += Time.deltaTime;
        float period = 1f / rateHz;
        if (_acc < period) return;
        _acc = 0f;

        Rigidbody rb = sim.Body;
        if (rb == null) return;

        string json = Protocol.BuildTelemetryJson(
            originLat,
            originLon,
            Time.time,
            sim.Armed,
            sim.Mode,
            rb.position,
            rb.linearVelocity,
            rb.rotation,
            rb.angularVelocity,
            sim.MotorOutputs,
            sim.BaroAlt);

        bridge.SendTelemetryJson(json);
    }
}

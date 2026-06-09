using UnityEngine;

[DisallowMultipleComponent]
public class UnitySimDroneController : MonoBehaviour
{
    [Header("Physics tuning (MVP)")]
    public float hoverThrust = 9.81f;          // N per kg-ish (we apply as acceleration)
    public float thrustScale = 20f;            // how strong throttle is
    public float tiltTorque = 8f;              // roll/pitch torque strength
    public float yawTorque = 2.5f;             // yaw torque strength
    public float autoLevel = 2.0f;             // stabilize back to level
    public float drag = 0.2f;
    public float angularDrag = 0.8f;

    [Header("Mode / state")]
    public bool Armed = false;
    public string Mode = "SIM";

    private Rigidbody _rb;

    // manual inputs from backend
    private float _pitch; // -1..1
    private float _roll;  // -1..1
    private float _yaw;   // -1..1
    private float _thrust; // 0..1

    // "mission" state (very rough)
    private Vector3? _gotoLocalTarget = null;
    private float _gotoTargetAlt = 0f;
    private Vector3 _homeLocal = Vector3.zero;

    public float Speed => _rb != null ? _rb.linearVelocity.magnitude : 0f;
    public float YawDeg => transform.eulerAngles.y;

    private void Awake()
    {
        _rb = GetComponent<Rigidbody>();
        if (_rb == null)
        {
            _rb = gameObject.AddComponent<Rigidbody>();
        }

        _rb.useGravity = true;
        _rb.drag = drag;
        _rb.angularDrag = angularDrag;
        _rb.mass = 1.0f;
    }

    private void FixedUpdate()
    {
        if (!Armed) return;

        // if we have a mission target - override manual slightly (MVP)
        if (_gotoLocalTarget.HasValue)
        {
            Vector3 target = _gotoLocalTarget.Value;
            Vector3 to = target - transform.position;
            Vector3 toXZ = new Vector3(to.x, 0, to.z);
            float dist = toXZ.magnitude;

            // simple P-controller to push toward target
            Vector3 desiredDir = dist > 0.2f ? toXZ.normalized : Vector3.zero;
            // map desired direction into pitch/roll (-1..1)
            // forward = +pitch (nose down) but depends on convention; keep simple:
            Vector3 fwd = transform.forward;
            Vector3 right = transform.right;
            float f = Vector3.Dot(desiredDir, fwd);
            float r = Vector3.Dot(desiredDir, right);
            _pitch = Mathf.Clamp(f * 0.6f, -0.6f, 0.6f);
            _roll = Mathf.Clamp(r * 0.6f, -0.6f, 0.6f);

            // hold altitude
            float altErr = (_gotoTargetAlt - transform.position.y);
            _thrust = Mathf.Clamp01(0.55f + altErr * 0.08f);

            if (dist < 0.8f && Mathf.Abs(altErr) < 0.8f)
            {
                _gotoLocalTarget = null;
            }
        }

        ApplyForces();
    }

    private void ApplyForces()
    {
        // throttle -> upward force in local up direction
        // map 0..1 around hover
        float upAccel = ( _thrust - 0.5f ) * thrustScale;
        Vector3 up = transform.up;
        _rb.AddForce(up * upAccel, ForceMode.Acceleration);

        // roll/pitch torques (tilt)
        float pitchT = _pitch * tiltTorque;
        float rollT = -_roll * tiltTorque;
        _rb.AddTorque(transform.right * pitchT, ForceMode.Acceleration);
        _rb.AddTorque(transform.forward * rollT, ForceMode.Acceleration);

        // yaw torque
        _rb.AddTorque(transform.up * (_yaw * yawTorque), ForceMode.Acceleration);

        // autolevel (stabilize)
        Vector3 axis;
        float angle;
        Quaternion q = transform.rotation;
        Quaternion level = Quaternion.Euler(0f, transform.eulerAngles.y, 0f);
        Quaternion delta = level * Quaternion.Inverse(q);
        delta.ToAngleAxis(out angle, out axis);
        if (angle > 180f) angle -= 360f;
        _rb.AddTorque(axis.normalized * (angle * Mathf.Deg2Rad) * autoLevel, ForceMode.Acceleration);
    }

    // --- commands from backend ---
    public void SetManual(int pitch, int roll, int yaw, int thrust)
    {
        // expect -1000..1000 and 0..1000
        _pitch = Mathf.Clamp(pitch / 1000f, -1f, 1f);
        _roll = Mathf.Clamp(roll / 1000f, -1f, 1f);
        _yaw = Mathf.Clamp(yaw / 1000f, -1f, 1f);
        _thrust = Mathf.Clamp01(thrust / 1000f);
        Mode = "MANUAL";
    }

    public void Arm(bool on)
    {
        Armed = on;
        if (!Armed)
        {
            _pitch = _roll = _yaw = 0f;
            _thrust = 0f;
            _gotoLocalTarget = null;
        }
        Mode = Armed ? "ARMED" : "DISARMED";
    }

    public void Takeoff(float alt)
    {
        // very rough: set goto target to current xz with desired altitude
        _gotoTargetAlt = Mathf.Max(0.5f, alt);
        _gotoLocalTarget = new Vector3(transform.position.x, _gotoTargetAlt, transform.position.z);
        Mode = "TAKEOFF";
    }

    public void Land()
    {
        _gotoLocalTarget = null;
        _gotoTargetAlt = 0f;
        _thrust = 0.2f;
        Mode = "LAND";
    }

    public void RTL()
    {
        _gotoTargetAlt = Mathf.Max(2f, transform.position.y);
        _gotoLocalTarget = new Vector3(_homeLocal.x, _gotoTargetAlt, _homeLocal.z);
        Mode = "RTL";
    }

    public void SetHome(double lat, double lon, float alt)
    {
        _homeLocal = transform.position;
        Mode = "HOME_SET";
    }

    public void SetMode(string mode)
    {
        if (!string.IsNullOrEmpty(mode)) Mode = mode;
    }

    public void Goto(double lat, double lon, float alt)
    {
        // We don't convert geo->local here (для MVP). UnityUdpBridge uses local->geo only.
        // Для MVP: используем "виртуальную" точку перед дроном на XZ.
        Vector3 forward = transform.forward;
        Vector3 target = transform.position + new Vector3(forward.x, 0f, forward.z).normalized * 10f;
        _gotoTargetAlt = Mathf.Max(0.5f, alt);
        _gotoLocalTarget = new Vector3(target.x, _gotoTargetAlt, target.z);
        Mode = "GOTO";
    }
}


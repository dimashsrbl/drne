using UnityEngine;

[DisallowMultipleComponent]
[RequireComponent(typeof(Rigidbody))]
public sealed class DronePhysics : MonoBehaviour
{
    [Header("Rigidbody")]
    public float massKg = 0.72f;
    public Vector3 inertiaTensor = new(0.008f, 0.012f, 0.008f);
    public float linearDrag = 0.15f;
    public float angularDrag = 0.4f;

    [Header("Motors (Betaflight QUAD X: BR, FR, BL, FL)")]
    public Transform[] motorMounts = new Transform[4];
    public int[] spinDir = { -1, -1, +1, +1 };

    [Header("Motor physics")]
    public float motorMaxOmega = 720f;
    public float motorTimeConstant = 0.03f;

    Rigidbody _rb;
    readonly MotorModel[] _motors = new MotorModel[4];
    IFlightController _fc;
    readonly float[] _lastMotorCmd = new float[4];

    public Rigidbody Body => _rb;
    public float[] LastMotorCommands => _lastMotorCmd;

    public void Bind(IFlightController fc) => _fc = fc;

    void Awake()
    {
        for (int i = 0; i < _motors.Length; i++)
            _motors[i] = new MotorModel();

        _rb = GetComponent<Rigidbody>();
        _rb.mass = massKg;
        _rb.useGravity = true;
        _rb.linearDamping = linearDrag;
        _rb.angularDamping = angularDrag;
        _rb.inertiaTensor = inertiaTensor;
        _rb.interpolation = RigidbodyInterpolation.Interpolate;
        _rb.collisionDetectionMode = CollisionDetectionMode.ContinuousDynamic;

        Time.fixedDeltaTime = 0.002f;
        Time.maximumDeltaTime = 0.02f;

        for (int i = 0; i < _motors.Length; i++)
        {
            _motors[i].maxOmega = motorMaxOmega;
            _motors[i].timeConstant = motorTimeConstant;
            CalibrateMotorThrust(_motors[i], massKg);
        }
    }

    void FixedUpdate()
    {
        float dt = Time.fixedDeltaTime;
        SensorSample sensors = ReadSensors();
        float[] cmd = _fc != null ? _fc.Step(in sensors, dt) : _lastMotorCmd;

        for (int i = 0; i < 4; i++)
            _lastMotorCmd[i] = cmd != null && i < cmd.Length ? Mathf.Clamp01(cmd[i]) : 0f;

        float yawTorque = 0f;
        for (int i = 0; i < 4; i++)
        {
            if (motorMounts == null || i >= motorMounts.Length || motorMounts[i] == null)
                continue;

            _motors[i].Step(_lastMotorCmd[i], dt);
            Transform mount = motorMounts[i];
            _rb.AddForceAtPosition(mount.up * _motors[i].LastThrust, mount.position, ForceMode.Force);
            yawTorque += spinDir[i] * _motors[i].LastReactionTorque;
        }

        _rb.AddRelativeTorque(0f, yawTorque, 0f, ForceMode.Force);
    }

    public SensorSample ReadSensors()
    {
        Vector3 gravityBody = transform.InverseTransformDirection(Physics.gravity);
        return new SensorSample
        {
            positionWorld = _rb.position,
            angularVelocity = transform.InverseTransformDirection(_rb.angularVelocity),
            linearAccelBody = gravityBody,
            attitude = _rb.rotation,
            baroAltitude = Mathf.Max(0f, _rb.position.y),
            velocityWorld = _rb.linearVelocity,
        };
    }

    public void ResetMotors()
    {
        for (int i = 0; i < _motors.Length; i++)
            _motors[i].Reset();
    }

    static void CalibrateMotorThrust(MotorModel motor, float mass)
    {
        float weight = mass * Physics.gravity.magnitude;
        float hoverPerMotor = weight / 4f;
        float hoverOmega = motor.maxOmega * 0.41f;
        motor.kThrust = hoverPerMotor / (hoverOmega * hoverOmega);
        motor.kTorque = motor.kThrust * 0.015f;
    }
}

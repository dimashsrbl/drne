using UnityEngine;

[DisallowMultipleComponent]
[DefaultExecutionOrder(10)]
public sealed class SimManager : MonoBehaviour
{
    [Header("References")]
    [SerializeField] UdpBridge bridge;
    [SerializeField] DronePhysics physics;
    [SerializeField] CameraRig cameraRig;

    [Header("Geo (goto / home)")]
    public double originLat = 51.1694;
    public double originLon = 71.4491;

    readonly PidFlightController _pid = new();
    Vector3 _spawnPos;
    Quaternion _spawnRot;
    Vector3 _homeLocal;

    bool _rcArmed;
    float _rollStick;
    float _pitchStick;
    float _yawStick;
    float _throttleStick;

    public bool Armed => _pid.Armed;
    public string Mode => _pid.Mode;
    public float BaroAlt => physics != null ? physics.ReadSensors().baroAltitude : 0f;
    public float[] MotorOutputs => physics != null ? physics.LastMotorCommands : null;
    public Rigidbody Body => physics != null ? physics.Body : null;

    public void SetLocalSticks(float roll, float pitch, float yaw, float throttle)
    {
        _rollStick = Mathf.Clamp(roll, -1f, 1f);
        _pitchStick = Mathf.Clamp(pitch, -1f, 1f);
        _yawStick = Mathf.Clamp(yaw, -1f, 1f);
        _throttleStick = Mathf.Clamp01(throttle);
    }

    void Reset()
    {
        bridge = GetComponent<UdpBridge>();
        physics = GetComponent<DronePhysics>();
        cameraRig = FindFirstObjectByType<CameraRig>();
    }

    void Awake()
    {
        if (physics == null) physics = GetComponent<DronePhysics>();
        if (bridge == null) bridge = GetComponent<UdpBridge>();
        if (physics != null)
        {
            _spawnPos = physics.transform.position;
            _spawnRot = physics.transform.rotation;
            _homeLocal = _spawnPos;
            physics.Bind(_pid);
        }
    }

    void OnEnable()
    {
        if (bridge != null)
            bridge.OnCommand += HandleCommand;
    }

    void OnDisable()
    {
        if (bridge != null)
            bridge.OnCommand -= HandleCommand;
    }

    void Update()
    {
        _pid.SetRc(_rollStick, _pitchStick, _yawStick, _throttleStick, _rcArmed);

        if (_pid.Armed && _pid.Mode == "land" && BaroAlt < 0.08f && Body != null &&
            Body.linearVelocity.magnitude < 0.35f)
        {
            Disarm();
        }
    }

    void HandleCommand(SimCommand cmd)
    {
        switch (cmd.kind)
        {
            case SimCommandKind.Rc:
                _rollStick = cmd.rollStick;
                _pitchStick = cmd.pitchStick;
                _yawStick = cmd.yawStick;
                _throttleStick = cmd.throttleStick;
                _rcArmed = cmd.armed;
                break;

            case SimCommandKind.LegacyManual:
                Protocol.LegacyManualToSticks(
                    cmd.legacyPitch, cmd.legacyRoll, cmd.legacyYaw, cmd.legacyThrust,
                    out _pitchStick, out _rollStick, out _yawStick, out _throttleStick);
                break;

            case SimCommandKind.LegacyArm:
                Arm();
                break;

            case SimCommandKind.LegacyDisarm:
                Disarm();
                break;

            case SimCommandKind.LegacyTakeoff:
                Arm();
                _pid.SetAltTarget(Mathf.Max(0.5f, cmd.takeoffAlt));
                break;

            case SimCommandKind.LegacyLand:
                _pid.StartLand();
                break;

            case SimCommandKind.LegacyRtl:
                _pid.SetGotoTarget(_homeLocal, Mathf.Max(1.5f, BaroAlt));
                break;

            case SimCommandKind.LegacyGoto:
                Vector3 worldTarget = GeoUtil.GeoToLocal(cmd.gotoLat, cmd.gotoLon, originLat, originLon);
                if (worldTarget.sqrMagnitude < 0.25f)
                {
                    Vector3 fwd = physics.transform.forward;
                    worldTarget = physics.transform.position +
                                  new Vector3(fwd.x, 0f, fwd.z).normalized * 10f;
                }
                worldTarget.y = 0f;
                _pid.SetGotoTarget(worldTarget, cmd.gotoAlt);
                break;

            case SimCommandKind.LegacySetHome:
                _homeLocal = physics.transform.position;
                break;

            case SimCommandKind.SetMode:
                ApplyMode(cmd.mode);
                break;

            case SimCommandKind.SimAction:
                if (cmd.action == "reset" || cmd.action == "teleport")
                    Respawn(cmd);
                break;
        }
    }

    void ApplyMode(string mode)
    {
        if (string.IsNullOrEmpty(mode)) return;
        string m = mode.ToUpperInvariant();
        if (m.Contains("ANGLE") || m.Contains("STAB"))
            _pid.SetMode("angle");
        else if (m.Contains("ACRO") || m.Contains("RATE"))
            _pid.SetMode("rate");
        else if (m.Contains("ALTHOLD") || m.Contains("BARO"))
            _pid.SetMode("althold");
        else
            _pid.SetMode(mode.ToLowerInvariant());
    }

    public void Arm()
    {
        _rcArmed = true;
        _pid.SetRc(_rollStick, _pitchStick, _yawStick, _throttleStick, true);
    }

    public void Disarm()
    {
        _rcArmed = false;
        _throttleStick = 0f;
        _pid.SetRc(0f, 0f, 0f, 0f, false);
        _pid.ResetState();
        if (physics != null)
            physics.ResetMotors();
    }

    void Respawn(SimCommand cmd)
    {
        if (physics == null || physics.Body == null) return;

        Rigidbody rb = physics.Body;
        rb.linearVelocity = Vector3.zero;
        rb.angularVelocity = Vector3.zero;

        if (cmd.action == "teleport" && cmd.teleportPos != null && cmd.teleportPos.Length >= 3)
        {
            rb.position = new Vector3(cmd.teleportPos[0], cmd.teleportPos[1], cmd.teleportPos[2]);
            rb.rotation = Quaternion.Euler(0f, cmd.teleportYaw, 0f);
        }
        else
        {
            rb.position = _spawnPos;
            rb.rotation = _spawnRot;
        }

        Disarm();
        physics.ResetMotors();
    }
}

using UnityEngine;

public sealed class PidFlightController : IFlightController
{
    sealed class PidAxis
    {
        public float P = 0.08f;
        public float I = 0.015f;
        public float D = 0.002f;
        public float IMax = 0.35f;
        float _integrator;
        float _prevMeas;

        public float Step(float target, float measured, float dt)
        {
            float error = target - measured;
            _integrator = Mathf.Clamp(_integrator + error * dt, -IMax, IMax);
            float deriv = dt > 1e-6f ? (measured - _prevMeas) / dt : 0f;
            _prevMeas = measured;
            return P * error + I * _integrator - D * deriv;
        }

        public void Reset()
        {
            _integrator = 0f;
            _prevMeas = 0f;
        }
    }

    [System.Serializable]
    public struct Tunables
    {
        public float maxTiltDeg;
        public float angleP;
        public float maxRollRateDeg;
        public float maxPitchRateDeg;
        public float maxYawRateDeg;
        public float hoverThrottle;
        public float throttleGain;
        public float altPGain;
        public float posPGain;
        public float landDescentMps;
    }

    public Tunables tunables = new Tunables
    {
        maxTiltDeg = 22f,
        angleP = 3.2f,
        maxRollRateDeg = 200f,
        maxPitchRateDeg = 200f,
        maxYawRateDeg = 150f,
        hoverThrottle = 0.41f,
        throttleGain = 0.45f,
        altPGain = 0.10f,
        posPGain = 0.08f,
        landDescentMps = 0.35f,
    };

    readonly PidAxis _rollRate = new PidAxis { P = 0.045f, I = 0.008f, D = 0.0015f };
    readonly PidAxis _pitchRate = new PidAxis { P = 0.045f, I = 0.008f, D = 0.0015f };
    readonly PidAxis _yawRate = new PidAxis { P = 0.06f, I = 0.01f, D = 0f };

    readonly float[] _motorOut = new float[4];

    bool _armed;
    string _mode = "angle";
    float _rollStick;
    float _pitchStick;
    float _yawStick;
    float _throttleStick;

    float? _altTarget;
    Vector3? _gotoLocal;
    bool _landing;

    public bool Armed => _armed;
    public string Mode => _mode;
    public float[] LastMotorCommands => _motorOut;

    public void SetRc(float rollStick, float pitchStick, float yawStick, float throttleStick, bool armed)
    {
        _rollStick = Mathf.Clamp(rollStick, -1f, 1f);
        _pitchStick = Mathf.Clamp(pitchStick, -1f, 1f);
        _yawStick = Mathf.Clamp(yawStick, -1f, 1f);
        _throttleStick = Mathf.Clamp01(throttleStick);

        if (armed && !_armed)
            _landing = false;

        if (!armed && _armed)
            ResetState();

        _armed = armed;
    }

    public void SetMode(string mode)
    {
        if (string.IsNullOrEmpty(mode)) return;
        _mode = mode.ToLowerInvariant();
    }

    public void ResetState()
    {
        _rollRate.Reset();
        _pitchRate.Reset();
        _yawRate.Reset();
        _altTarget = null;
        _gotoLocal = null;
        _landing = false;
        for (int i = 0; i < 4; i++) _motorOut[i] = 0f;
    }

    public void SetAltTarget(float? altitudeMeters)
    {
        _altTarget = altitudeMeters;
        _gotoLocal = null;
        _landing = false;
        if (altitudeMeters.HasValue)
            _mode = "althold";
    }

    public void SetGotoTarget(Vector3? localTarget, float targetAlt)
    {
        _gotoLocal = localTarget;
        _altTarget = targetAlt;
        _landing = false;
        _mode = "poshold";
    }

    public void StartLand()
    {
        _landing = true;
        _gotoLocal = null;
        _altTarget = 0.05f;
        _mode = "land";
    }

    public float[] Step(in SensorSample sensors, float dt)
    {
        if (!_armed)
        {
            for (int i = 0; i < 4; i++) _motorOut[i] = 0f;
            return _motorOut;
        }

        float roll = GetRoll(sensors.attitude);
        float pitch = GetPitch(sensors.attitude);

        float rollStick = _rollStick;
        float pitchStick = _pitchStick;

        if (_gotoLocal.HasValue)
            ApplyGotoStick(sensors.positionWorld, ref rollStick, ref pitchStick);

        float throttle = ComputeThrottle(sensors);

        float targetRollRate;
        float targetPitchRate;

        if (_mode == "rate" || _mode == "acro")
        {
            targetRollRate = DegToRad(tunables.maxRollRateDeg) * rollStick;
            targetPitchRate = -DegToRad(tunables.maxPitchRateDeg) * pitchStick;
        }
        else
        {
            float maxTilt = tunables.maxTiltDeg * Mathf.Deg2Rad;
            float targetRoll = rollStick * maxTilt;
            float targetPitch = -pitchStick * maxTilt;
            targetRollRate = tunables.angleP * (targetRoll - roll);
            targetPitchRate = tunables.angleP * (targetPitch - pitch);
        }

        float targetYawRate = DegToRad(tunables.maxYawRateDeg) * _yawStick;

        // Unity body: roll rate ≈ local Z, pitch rate ≈ local X, yaw ≈ local Y
        float rollRate = sensors.angularVelocity.z;
        float pitchRate = sensors.angularVelocity.x;
        float yawRate = sensors.angularVelocity.y;

        float rollCmd = _rollRate.Step(targetRollRate, rollRate, dt);
        float pitchCmd = _pitchRate.Step(targetPitchRate, pitchRate, dt);
        float yawCmd = _yawRate.Step(targetYawRate, yawRate, dt);

        MixQuadX(throttle, rollCmd, pitchCmd, yawCmd, _motorOut);
        return _motorOut;
    }

    void ApplyGotoStick(Vector3 pos, ref float rollStick, ref float pitchStick)
    {
        if (!_gotoLocal.HasValue) return;

        Vector3 to = _gotoLocal.Value - pos;
        Vector3 toXZ = new Vector3(to.x, 0f, to.z);
        if (toXZ.sqrMagnitude < 0.25f)
        {
            _gotoLocal = null;
            if (_mode == "poshold")
                _mode = "althold";
            return;
        }

        Vector3 desired = toXZ.normalized;
        pitchStick = Mathf.Clamp(desired.z * tunables.posPGain * 10f, -0.65f, 0.65f);
        rollStick = Mathf.Clamp(desired.x * tunables.posPGain * 10f, -0.65f, 0.65f);
    }

    float ComputeThrottle(in SensorSample sensors)
    {
        // Газ на минимуме — моторы не крутятся (как MOTOR_STOP), иначе переворот на земле
        if (_throttleStick < 0.08f)
            return 0f;

        float throttle = tunables.hoverThrottle + (_throttleStick - 0.5f) * tunables.throttleGain;

        if (_altTarget.HasValue)
        {
            float altErr = _altTarget.Value - sensors.baroAltitude;
            if (_landing && sensors.baroAltitude < 0.12f)
                throttle = tunables.hoverThrottle * 0.12f;
            else
                throttle = tunables.hoverThrottle + altErr * tunables.altPGain;

            if (_landing)
                throttle -= tunables.landDescentMps * 0.05f;
        }

        return Mathf.Clamp01(throttle);
    }

    static void MixQuadX(float throttle, float roll, float pitch, float yaw, float[] motors)
    {
        motors[0] = throttle + roll - pitch - yaw;
        motors[1] = throttle - roll - pitch + yaw;
        motors[2] = throttle + roll + pitch + yaw;
        motors[3] = throttle - roll + pitch - yaw;

        float max = motors[0];
        float min = motors[0];
        for (int i = 1; i < 4; i++)
        {
            if (motors[i] > max) max = motors[i];
            if (motors[i] < min) min = motors[i];
        }

        if (max > 1f || min < 0f)
        {
            float scale = 1f;
            if (max > 1f) scale = Mathf.Min(scale, (1f - throttle) / Mathf.Max(0.001f, max - throttle));
            if (min < 0f) scale = Mathf.Min(scale, throttle / Mathf.Max(0.001f, throttle - min));
            roll *= scale;
            pitch *= scale;
            yaw *= scale;
            motors[0] = throttle + roll - pitch - yaw;
            motors[1] = throttle - roll - pitch + yaw;
            motors[2] = throttle + roll + pitch + yaw;
            motors[3] = throttle - roll + pitch - yaw;
        }

        for (int i = 0; i < 4; i++)
            motors[i] = Mathf.Clamp01(motors[i]);
    }

    static float GetRoll(Quaternion q)
    {
        float sinr = 2f * (q.w * q.x + q.y * q.z);
        float cosr = 1f - 2f * (q.x * q.x + q.y * q.y);
        return Mathf.Atan2(sinr, cosr);
    }

    static float GetPitch(Quaternion q)
    {
        float sinp = 2f * (q.w * q.y - q.z * q.x);
        if (Mathf.Abs(sinp) >= 1f) return sinp >= 0f ? Mathf.PI * 0.5f : -Mathf.PI * 0.5f;
        return Mathf.Asin(sinp);
    }

    static float DegToRad(float deg) => deg * Mathf.Deg2Rad;
}

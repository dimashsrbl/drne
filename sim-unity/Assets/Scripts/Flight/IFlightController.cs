using UnityEngine;

public interface IFlightController
{
    bool Armed { get; }
    string Mode { get; }

    void SetRc(float rollStick, float pitchStick, float yawStick, float throttleStick, bool armed);
    void SetMode(string mode);
    void ResetState();

    void SetAltTarget(float? altitudeMeters);
    void SetGotoTarget(Vector3? localTarget, float targetAlt);
    void StartLand();

    float[] LastMotorCommands { get; }
    float[] Step(in SensorSample sensors, float dt);
}

public struct SensorSample
{
    public Vector3 positionWorld;
    public Vector3 angularVelocity;
    public Vector3 linearAccelBody;
    public Quaternion attitude;
    public float baroAltitude;
    public Vector3 velocityWorld;
}

using UnityEngine;

public sealed class MotorModel
{
    public float kThrust = 1.05e-5f;
    public float kTorque = 1.6e-7f;
    public float maxOmega = 720f;
    public float timeConstant = 0.03f;

    float _omega;

    public float Omega => _omega;
    public float LastThrust { get; private set; }
    public float LastReactionTorque { get; private set; }

    public void Reset()
    {
        _omega = 0f;
        LastThrust = 0f;
        LastReactionTorque = 0f;
    }

    public void Step(float command, float dt)
    {
        float target = Mathf.Clamp01(command) * maxOmega;
        float alpha = timeConstant > 1e-5f ? Mathf.Clamp01(dt / timeConstant) : 1f;
        _omega += (target - _omega) * alpha;
        LastThrust = kThrust * _omega * _omega;
        LastReactionTorque = kTorque * _omega * _omega;
    }
}

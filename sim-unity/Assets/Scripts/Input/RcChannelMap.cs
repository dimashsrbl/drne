using UnityEngine;

/// <summary>
/// Тот же маппинг, что frontend/src/hooks/useGamepad.ts (Tango/Jumper USB HID, Mode 2).
/// </summary>
public static class RcChannelMap
{
    public const int Roll = 0;
    public const int Pitch = 1;
    public const int Throttle = 2;
    public const int Yaw = 3;
    public const int Swa = 4;   // ARM: +1 вверх, -1 вниз
    public const int Swb = 5;
    public const int Swc = 6;
    public const int Swd = 7;

    public const float Deadzone = 0.05f;

    public static float ApplyDeadzone(float v, float dz = Deadzone)
    {
        return Mathf.Abs(v) < dz ? 0f : v;
    }

    public static int SnapSwitch(float v)
    {
        if (v > 0.5f) return 1;
        if (v < -0.5f) return -1;
        return 0;
    }

    /// <summary>Как useGamepad: pitch invert, throttle 0..1, roll/yaw -1..1.</summary>
    public static void ToSticks(float[] axes, int count,
        out float roll, out float pitch, out float yaw, out float throttle)
    {
        float Raw(int i) => i >= 0 && i < count ? axes[i] : 0f;

        roll = ApplyDeadzone(Raw(Roll));
        pitch = ApplyDeadzone(Raw(Pitch));
        pitch = -pitch; // invert: вперёд = положительный pitch (как useGamepad)
        yaw = ApplyDeadzone(Raw(Yaw));

        float thrRaw = ApplyDeadzone(Raw(Throttle));
        throttle = Mathf.Clamp01((thrRaw + 1f) * 0.5f);
    }

    public static bool ShouldArm(float[] axes, int count)
    {
        return SnapSwitch(Raw(axes, count, Swa)) == 1;
    }

    public static bool ShouldDisarm(float[] axes, int count)
    {
        return SnapSwitch(Raw(axes, count, Swa)) == -1;
    }

    static float Raw(float[] axes, int count, int i) => i >= 0 && i < count ? axes[i] : 0f;
}

using System.Collections.Generic;
using System.Text;
using UnityEngine;
#if ENABLE_INPUT_SYSTEM
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.Controls;
#endif

/// <summary>
/// RC USB → Unity. Маппинг 1:1 с frontend useGamepad.ts (оси 0–7, SWA=ARM).
/// </summary>
[DisallowMultipleComponent]
[DefaultExecutionOrder(-100)]
public sealed class LocalGamepadInput : MonoBehaviour
{
    [SerializeField] SimManager sim;

    [Header("Клавиатура")]
    public Key armKey = Key.Space;
    public Key disarmKey = Key.X;

    [Header("HUD")]
    public bool showHud = true;

    string _deviceName = "нет";
    string _readMethod = "-";
    readonly float[] _axes = new float[8];
    int _axisCount;

    float _outRoll;
    float _outPitch;
    float _outYaw;
    float _outThr;

    void Reset() => sim = GetComponent<SimManager>();

    void Awake()
    {
        if (sim == null) sim = GetComponent<SimManager>();
    }

    void OnEnable()
    {
#if ENABLE_INPUT_SYSTEM
        InputSystem.onDeviceChange += OnDeviceChange;
        foreach (Joystick j in Joystick.all)
            if (j != null) InputSystem.EnableDevice(j);
#endif
    }

    void OnDisable()
    {
#if ENABLE_INPUT_SYSTEM
        InputSystem.onDeviceChange -= OnDeviceChange;
#endif
    }

#if ENABLE_INPUT_SYSTEM
    static void OnDeviceChange(InputDevice device, InputDeviceChange change)
    {
        if (device is Joystick && change == InputDeviceChange.Added)
            InputSystem.EnableDevice(device);
    }
#endif

    void Update()
    {
        if (sim == null) return;

        PollAxes();
        RcChannelMap.ToSticks(_axes, _axisCount, out _outRoll, out _outPitch, out _outYaw, out _outThr);
        sim.SetLocalSticks(_outRoll, _outPitch, _outYaw, _outThr);

        if (RcChannelMap.ShouldArm(_axes, _axisCount))
            sim.Arm();
        else if (RcChannelMap.ShouldDisarm(_axes, _axisCount))
            sim.Disarm();

        HandleKeyboard();
    }

    void PollAxes()
    {
        for (int i = 0; i < _axes.Length; i++) _axes[i] = 0f;
        _axisCount = 0;
        _readMethod = "-";

#if ENABLE_LEGACY_INPUT_MANAGER
        if (TryPollLegacyJoy1())
            return;
#endif

#if ENABLE_INPUT_SYSTEM
        InputSystem.Update();
        if (TryPollInputSystemSorted())
            return;
#endif

        _deviceName = "нет — кликни Game, подвигай стики";
    }

#if ENABLE_LEGACY_INPUT_MANAGER
    bool TryPollLegacyJoy1()
    {
        string[] names = Input.GetJoystickNames();
        if (names == null || names.Length == 0 || string.IsNullOrEmpty(names[0]))
            return false;

        _deviceName = names[0];
        int active = 0;
        for (int i = 0; i < 8; i++)
        {
            float v = Input.GetAxisRaw("Joy1 Axis " + (i + 1));
            _axes[i] = v;
            if (Mathf.Abs(v) > 0.01f) active++;
        }
        _axisCount = 8;
        _readMethod = "Legacy/Joy1 (как Web Gamepad)";
        return true;
    }
#endif

#if ENABLE_INPUT_SYSTEM
    bool TryPollInputSystemSorted()
    {
        Joystick js = Joystick.current;
        if (js == null && Joystick.all.Count > 0)
            js = Joystick.all[0];
        if (js == null) return false;

        InputSystem.EnableDevice(js);
        _deviceName = js.displayName;

        var sorted = new List<(uint byteOffset, float value)>();
        foreach (InputControl c in js.allControls)
        {
            if (c is not AxisControl axis) continue;
            sorted.Add((c.stateBlock.byteOffset, axis.ReadValue()));
        }

        if (sorted.Count == 0) return false;

        sorted.Sort((a, b) => a.byteOffset.CompareTo(b.byteOffset));

        int n = 0;
        uint lastOffset = uint.MaxValue;
        foreach (var item in sorted)
        {
            if (item.byteOffset == lastOffset) continue;
            lastOffset = item.byteOffset;
            if (n >= _axes.Length) break;
            _axes[n++] = item.value;
        }

        _axisCount = n;
        _readMethod = "InputSystem/sorted";
        return n >= 4;
    }
#endif

    void HandleKeyboard()
    {
#if ENABLE_INPUT_SYSTEM
        if (Keyboard.current == null) return;
        if (Keyboard.current[armKey].wasPressedThisFrame) sim.Arm();
        if (Keyboard.current[disarmKey].wasPressedThisFrame) sim.Disarm();
#endif
    }

    void OnGUI()
    {
        if (!showHud || sim == null) return;

        GUI.Box(new Rect(10, 10, 480, 230), "RC — как useGamepad.ts");
        GUI.Label(new Rect(20, 32, 460, 18), _deviceName + "  |  " + _readMethod);
        GUI.Label(new Rect(20, 50, 460, 18),
            "0=roll 1=pitch 2=thr 3=yaw 4=SWA(ARM↑/↓)  |  Space/X");
        GUI.Label(new Rect(20, 68, 460, 18),
            sim.Armed ? "ARMED" : "DISARMED");

        var sb = new StringBuilder();
        for (int i = 0; i < _axisCount; i++)
            sb.Append(i).Append("=").Append(_axes[i].ToString("F2")).Append("  ");
        GUI.Label(new Rect(20, 90, 460, 36), sb.ToString());

        GUI.Label(new Rect(20, 130, 460, 18),
            string.Format("→ roll={0:F2} pitch={1:F2} thr={2:F2} yaw={3:F2}  SWA={4}",
                _outRoll, _outPitch, _outThr, _outYaw,
                RcChannelMap.SnapSwitch(_axisCount > 4 ? _axes[4] : 0f)));
        GUI.Label(new Rect(20, 148, 460, 18), "alt=" + sim.BaroAlt.ToString("F2") + "m");
    }
}

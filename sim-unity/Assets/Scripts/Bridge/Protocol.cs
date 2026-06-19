using System;
using System.Globalization;
using System.Text;
using UnityEngine;

public enum SimCommandKind
{
    Rc,
    SetMode,
    SimAction,
    LegacyManual,
    LegacyArm,
    LegacyDisarm,
    LegacyTakeoff,
    LegacyLand,
    LegacyRtl,
    LegacyGoto,
    LegacySetHome,
}

public struct SimCommand
{
    public SimCommandKind kind;
    public int version;

    public float rollStick;
    public float pitchStick;
    public float yawStick;
    public float throttleStick;
    public bool armed;

    public string mode;
    public string action;
    public float takeoffAlt;
    public double gotoLat;
    public double gotoLon;
    public float gotoAlt;
    public float[] teleportPos;
    public float teleportYaw;

    public int legacyPitch;
    public int legacyRoll;
    public int legacyYaw;
    public int legacyThrust;
}

public static class Protocol
{
    public static bool TryParse(string json, out SimCommand cmd)
    {
        cmd = default;
        if (string.IsNullOrWhiteSpace(json)) return false;

        string type = ReadString(json, "type");
        if (string.IsNullOrEmpty(type)) return false;

        cmd.version = ReadInt(json, "v", 1);

        switch (type)
        {
            case "rc":
                cmd.kind = SimCommandKind.Rc;
                cmd.armed = ReadBool(json, "arm", true);
                return ParseRcChannels(json, ref cmd);

            case "set_mode":
                cmd.kind = SimCommandKind.SetMode;
                cmd.mode = ReadString(json, "mode");
                return !string.IsNullOrEmpty(cmd.mode);

            case "sim":
                cmd.kind = SimCommandKind.SimAction;
                cmd.action = ReadString(json, "action");
                cmd.teleportPos = ReadFloatArray(json, "pos", 3);
                cmd.teleportYaw = ReadFloat(json, "yaw", 0f);
                return !string.IsNullOrEmpty(cmd.action);

            case "manual":
                cmd.kind = SimCommandKind.LegacyManual;
                cmd.legacyPitch = ReadInt(json, "pitch", 0);
                cmd.legacyRoll = ReadInt(json, "roll", 0);
                cmd.legacyYaw = ReadInt(json, "yaw", 0);
                cmd.legacyThrust = ReadInt(json, "thrust", 0);
                return true;

            case "arm":
                cmd.kind = SimCommandKind.LegacyArm;
                return true;

            case "disarm":
                cmd.kind = SimCommandKind.LegacyDisarm;
                return true;

            case "takeoff":
                cmd.kind = SimCommandKind.LegacyTakeoff;
                cmd.takeoffAlt = ReadFloat(json, "alt", 1f);
                return true;

            case "land":
                cmd.kind = SimCommandKind.LegacyLand;
                return true;

            case "rtl":
                cmd.kind = SimCommandKind.LegacyRtl;
                return true;

            case "goto":
                cmd.kind = SimCommandKind.LegacyGoto;
                cmd.gotoLat = ReadDouble(json, "lat", 0);
                cmd.gotoLon = ReadDouble(json, "lon", 0);
                cmd.gotoAlt = ReadFloat(json, "alt", 1f);
                return true;

            case "set_home":
                cmd.kind = SimCommandKind.LegacySetHome;
                return true;

            default:
                return false;
        }
    }

    static bool ParseRcChannels(string json, ref SimCommand cmd)
    {
        float[] ch = ReadFloatArray(json, "channels", 8);
        if (ch == null || ch.Length < 4) return false;

        cmd.rollStick = PwmToStick(ch[0]);
        cmd.pitchStick = PwmToStick(ch[1]);
        cmd.throttleStick = PwmToThrottle(ch[2]);
        cmd.yawStick = PwmToStick(ch[3]);

        if (ch.Length > 4)
            cmd.armed = ch[4] >= 1700f;
        return true;
    }

    public static float PwmToStick(float us) => Mathf.Clamp((us - 1500f) / 500f, -1f, 1f);

    public static float PwmToThrottle(float us)
    {
        float t = (us - 1000f) / 1000f;
        return Mathf.Clamp01(t);
    }

    public static void LegacyManualToSticks(int pitch, int roll, int yaw, int thrust,
        out float pitchStick, out float rollStick, out float yawStick, out float throttleStick)
    {
        pitchStick = Mathf.Clamp(pitch / 1000f, -1f, 1f);
        rollStick = Mathf.Clamp(roll / 1000f, -1f, 1f);
        yawStick = Mathf.Clamp(yaw / 1000f, -1f, 1f);
        throttleStick = Mathf.Clamp01(thrust / 1000f);
    }

    public static string BuildTelemetryJson(
        double originLat,
        double originLon,
        float timeSec,
        bool armed,
        string mode,
        Vector3 pos,
        Vector3 vel,
        Quaternion rot,
        Vector3 angVel,
        float[] motors,
        float baroAlt)
    {
        GeoUtil.LocalToGeo(pos.x, pos.z, originLat, originLon, out double lat, out double lon);
        float yaw = rot.eulerAngles.y;
        float speed = new Vector3(vel.x, 0f, vel.z).magnitude;

        StringBuilder sb = new StringBuilder(512);
        sb.Append('{');
        sb.Append("\"v\":2,");
        sb.Append("\"type\":\"telemetry\",");
        sb.AppendFormat(CultureInfo.InvariantCulture, "\"t\":{0:F3},", timeSec);
        sb.Append(armed ? "\"armed\":true," : "\"armed\":false,");
        sb.AppendFormat(CultureInfo.InvariantCulture, "\"mode\":\"{0}\",", Escape(mode ?? "sim"));
        AppendVec3(sb, "pos", pos);
        sb.Append(',');
        AppendVec3(sb, "vel", vel);
        sb.Append(',');
        sb.AppendFormat(CultureInfo.InvariantCulture,
            "\"att_q\":[{0:F5},{1:F5},{2:F5},{3:F5}],",
            rot.x, rot.y, rot.z, rot.w);
        AppendVec3(sb, "ang_vel", angVel);
        sb.Append(',');
        AppendFloatArray(sb, "motors", motors);
        sb.Append(',');
        sb.AppendFormat(CultureInfo.InvariantCulture, "\"baro_alt\":{0:F3},", baroAlt);
        sb.AppendFormat(CultureInfo.InvariantCulture, "\"lat\":{0:F7},", lat);
        sb.AppendFormat(CultureInfo.InvariantCulture, "\"lon\":{0:F7},", lon);
        sb.AppendFormat(CultureInfo.InvariantCulture, "\"alt\":{0:F3},", Mathf.Max(0f, pos.y));
        sb.AppendFormat(CultureInfo.InvariantCulture, "\"yaw\":{0:F2},", yaw);
        sb.AppendFormat(CultureInfo.InvariantCulture, "\"speed\":{0:F3}", speed);
        sb.Append('}');
        return sb.ToString();
    }

    static void AppendVec3(StringBuilder sb, string key, Vector3 v)
    {
        sb.AppendFormat(CultureInfo.InvariantCulture,
            "\"{0}\":[{1:F4},{2:F4},{3:F4}]", key, v.x, v.y, v.z);
    }

    static void AppendFloatArray(StringBuilder sb, string key, float[] values)
    {
        sb.Append('"').Append(key).Append("\":[");
        if (values != null)
        {
            for (int i = 0; i < values.Length; i++)
            {
                if (i > 0) sb.Append(',');
                sb.AppendFormat(CultureInfo.InvariantCulture, "{0:F3}", values[i]);
            }
        }
        sb.Append(']');
    }

    static string Escape(string s) => s.Replace("\\", "\\\\").Replace("\"", "\\\"");

    static string ReadString(string json, string key)
    {
        string needle = "\"" + key + "\"";
        int i = json.IndexOf(needle, StringComparison.Ordinal);
        if (i < 0) return null;
        i = json.IndexOf(':', i);
        if (i < 0) return null;
        i++;
        while (i < json.Length && char.IsWhiteSpace(json[i])) i++;
        if (i >= json.Length || json[i] != '"') return null;
        i++;
        int start = i;
        while (i < json.Length && json[i] != '"') i++;
        return json.Substring(start, i - start);
    }

    static int ReadInt(string json, string key, int def)
    {
        return Mathf.RoundToInt(ReadFloat(json, key, def));
    }

    static float ReadFloat(string json, string key, float def)
    {
        string needle = "\"" + key + "\"";
        int i = json.IndexOf(needle, StringComparison.Ordinal);
        if (i < 0) return def;
        i = json.IndexOf(':', i);
        if (i < 0) return def;
        i++;
        while (i < json.Length && char.IsWhiteSpace(json[i])) i++;
        int start = i;
        while (i < json.Length && "0123456789+-eE.".IndexOf(json[i]) >= 0) i++;
        if (start == i) return def;
        float v;
        return float.TryParse(json.Substring(start, i - start), NumberStyles.Float, CultureInfo.InvariantCulture, out v) ? v : def;
    }

    static double ReadDouble(string json, string key, double def)
    {
        return ReadFloat(json, key, (float)def);
    }

    static bool ReadBool(string json, string key, bool def)
    {
        string needle = "\"" + key + "\"";
        int i = json.IndexOf(needle, StringComparison.Ordinal);
        if (i < 0) return def;
        return json.IndexOf("true", i, StringComparison.OrdinalIgnoreCase) >= 0
               && json.IndexOf("true", i, StringComparison.OrdinalIgnoreCase) < i + 12;
    }

    static float[] ReadFloatArray(string json, string key, int maxLen)
    {
        string needle = "\"" + key + "\"";
        int i = json.IndexOf(needle, StringComparison.Ordinal);
        if (i < 0) return null;
        i = json.IndexOf('[', i);
        if (i < 0) return null;
        int end = json.IndexOf(']', i);
        if (end < 0) return null;

        string inner = json.Substring(i + 1, end - i - 1);
        string[] parts = inner.Split(',');
        float[] arr = new float[Mathf.Min(maxLen, parts.Length)];
        int n = 0;
        for (int p = 0; p < parts.Length && n < maxLen; p++)
        {
            string s = parts[p].Trim();
            if (s.Length == 0) continue;
            float v;
            if (float.TryParse(s, NumberStyles.Float, CultureInfo.InvariantCulture, out v))
                arr[n++] = v;
        }
        if (n == 0) return null;
        if (n == arr.Length) return arr;
        float[] trimmed = new float[n];
        Array.Copy(arr, trimmed, n);
        return trimmed;
    }
}

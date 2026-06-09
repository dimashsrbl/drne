using System;
using System.Net;
using System.Net.Sockets;
using System.Text;
using UnityEngine;

[DisallowMultipleComponent]
public class UnityUdpBridge : MonoBehaviour
{
    [Header("UDP Ports")]
    public string cmdListenHost = "127.0.0.1";
    public int cmdListenPort = 15000;
    public string telemetryHost = "127.0.0.1";
    public int telemetryPort = 15001;

    [Header("Origin (for fake GPS)")]
    public double originLat = 51.1694;
    public double originLon = 71.4491;

    [Header("Telemetry")]
    public float telemetryHz = 20f;

    private UdpClient _cmdRx;
    private IPEndPoint _cmdEp;
    private UdpClient _telemTx;
    private IPEndPoint _telemEp;

    private UnitySimDroneController _drone;
    private float _nextTelemAt;

    private void Awake()
    {
        _drone = GetComponent<UnitySimDroneController>();
        if (_drone == null)
        {
            Debug.LogError("[UnityUdpBridge] UnitySimDroneController not found on same GameObject");
        }

        _cmdEp = new IPEndPoint(IPAddress.Parse(cmdListenHost), cmdListenPort);
        _cmdRx = new UdpClient(_cmdEp);
        _cmdRx.Client.ReceiveTimeout = 5;

        _telemEp = new IPEndPoint(IPAddress.Parse(telemetryHost), telemetryPort);
        _telemTx = new UdpClient();
    }

    private void OnDestroy()
    {
        try { _cmdRx?.Close(); } catch { }
        try { _telemTx?.Close(); } catch { }
    }

    private void Update()
    {
        // receive (non-blocking-ish)
        while (_cmdRx != null && _cmdRx.Available > 0)
        {
            try
            {
                IPEndPoint remote = null;
                byte[] bytes = _cmdRx.Receive(ref remote);
                string s = Encoding.UTF8.GetString(bytes).Trim();
                if (!string.IsNullOrEmpty(s)) HandleCommandJson(s);
            }
            catch { break; }
        }

        // telemetry
        if (telemetryHz > 0 && Time.time >= _nextTelemAt)
        {
            _nextTelemAt = Time.time + 1f / Mathf.Max(1f, telemetryHz);
            SendTelemetry();
        }
    }

    private void HandleCommandJson(string s)
    {
        // Minimal JSON parser через JsonUtility: нужен wrapper с заранее известными полями.
        // Поэтому делаем быстрый "type sniff".
        if (!s.Contains("\"type\"")) return;
        try
        {
            var t = JsonUtility.FromJson<CmdTypeOnly>(s);
            if (t == null || string.IsNullOrEmpty(t.type) || _drone == null) return;

            switch (t.type)
            {
                case "manual":
                    var m = JsonUtility.FromJson<CmdManual>(s);
                    _drone.SetManual(m.pitch, m.roll, m.yaw, m.thrust);
                    break;
                case "arm":
                    _drone.Arm(true);
                    break;
                case "disarm":
                    _drone.Arm(false);
                    break;
                case "takeoff":
                    var tk = JsonUtility.FromJson<CmdTakeoff>(s);
                    _drone.Takeoff(tk.alt);
                    break;
                case "land":
                    _drone.Land();
                    break;
                case "rtl":
                    _drone.RTL();
                    break;
                case "goto":
                    var g = JsonUtility.FromJson<CmdGoto>(s);
                    _drone.Goto(g.lat, g.lon, g.alt);
                    break;
                case "set_mode":
                    var sm = JsonUtility.FromJson<CmdSetMode>(s);
                    _drone.SetMode(sm.mode);
                    break;
                case "set_home":
                    var sh = JsonUtility.FromJson<CmdSetHome>(s);
                    _drone.SetHome(sh.lat, sh.lon, sh.alt);
                    break;
            }
        }
        catch (Exception)
        {
            // ignore malformed
        }
    }

    private void SendTelemetry()
    {
        if (_telemTx == null || _drone == null) return;

        // local XYZ -> fake geo
        Vector3 pos = transform.position;
        double lat, lon;
        LocalToGeo(pos.x, pos.z, originLat, originLon, out lat, out lon);

        float alt = Mathf.Max(0f, pos.y);
        float speed = _drone.Speed;
        float yaw = _drone.YawDeg;

        var msg = new TelemetryMsg
        {
            type = "telemetry",
            lat = lat,
            lon = lon,
            alt = alt,
            yaw = yaw,
            speed = speed,
            armed = _drone.Armed,
            mode = _drone.Mode
        };
        string json = JsonUtility.ToJson(msg);
        byte[] bytes = Encoding.UTF8.GetBytes(json);
        _telemTx.Send(bytes, bytes.Length, _telemEp);
    }

    // very rough: meters to deg
    private static void LocalToGeo(float xEastM, float zNorthM, double originLat, double originLon, out double lat, out double lon)
    {
        double metersPerDegLat = 111_320.0;
        double metersPerDegLon = 111_320.0 * Math.Cos(originLat * Math.PI / 180.0);
        lat = originLat + (zNorthM / metersPerDegLat);
        lon = originLon + (xEastM / metersPerDegLon);
    }

    [Serializable] private class CmdTypeOnly { public string type; }
    [Serializable] private class CmdManual { public string type; public int pitch; public int roll; public int yaw; public int thrust; }
    [Serializable] private class CmdTakeoff { public string type; public float alt; }
    [Serializable] private class CmdGoto { public string type; public double lat; public double lon; public float alt; }
    [Serializable] private class CmdSetMode { public string type; public string mode; }
    [Serializable] private class CmdSetHome { public string type; public double lat; public double lon; public float alt; }

    [Serializable]
    private class TelemetryMsg
    {
        public string type;
        public double lat;
        public double lon;
        public float alt;
        public float yaw;
        public float speed;
        public bool armed;
        public string mode;
    }
}


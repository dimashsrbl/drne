using System;
using System.Collections.Concurrent;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using UnityEngine;

[DisallowMultipleComponent]
public sealed class UdpBridge : MonoBehaviour
{
    [Header("UDP")]
    public string cmdListenHost = "127.0.0.1";
    public int cmdListenPort = 15000;
    public string telemetryHost = "127.0.0.1";
    public int telemetryPort = 15001;

    UdpClient _rx;
    UdpClient _tx;
    Thread _rxThread;
    volatile bool _running;
    IPEndPoint _telemEndpoint;

    readonly ConcurrentQueue<SimCommand> _inbox = new ConcurrentQueue<SimCommand>();

    public event Action<SimCommand> OnCommand;

    void OnEnable()
    {
        try
        {
            _rx = new UdpClient(new IPEndPoint(IPAddress.Parse(cmdListenHost), cmdListenPort));
            _tx = new UdpClient();
            _telemEndpoint = new IPEndPoint(IPAddress.Parse(telemetryHost), telemetryPort);
            _running = true;
            _rxThread = new Thread(ReceiveLoop) { IsBackground = true, Name = "UnityUdpRx" };
            _rxThread.Start();
            Debug.Log("[UdpBridge] Listening cmd udp://" + cmdListenHost + ":" + cmdListenPort);
        }
        catch (Exception ex)
        {
            Debug.LogError("[UdpBridge] Failed to bind: " + ex.Message);
        }
    }

    void OnDisable()
    {
        _running = false;
        try { _rx?.Close(); } catch { }
        try { _tx?.Close(); } catch { }
        try { _rxThread?.Join(300); } catch { }
        _rx = null;
        _tx = null;
    }

    void Update()
    {
        while (_inbox.TryDequeue(out SimCommand cmd))
        {
            if (OnCommand != null)
                OnCommand.Invoke(cmd);
        }
    }

    void ReceiveLoop()
    {
        IPEndPoint remote = new IPEndPoint(IPAddress.Any, 0);
        while (_running)
        {
            try
            {
                if (_rx == null) break;
                byte[] data = _rx.Receive(ref remote);
                string text = Encoding.UTF8.GetString(data).Trim();
                if (Protocol.TryParse(text, out SimCommand cmd))
                    _inbox.Enqueue(cmd);
            }
            catch (SocketException)
            {
                if (!_running) break;
            }
            catch (ObjectDisposedException)
            {
                break;
            }
            catch (Exception ex)
            {
                if (_running)
                    Debug.LogWarning("[UdpBridge] RX error: " + ex.Message);
            }
        }
    }

    public void SendTelemetryJson(string json)
    {
        if (_tx == null || string.IsNullOrEmpty(json)) return;
        byte[] bytes = Encoding.UTF8.GetBytes(json);
        _tx.Send(bytes, bytes.Length, _telemEndpoint);
    }
}

# Wiring guide — Pixhawk 2.4.8 · Raspberry Pi 5 · iFlight BLITZ E55 4-in-1 · XL4016

Setup: 6S quadcopter, ArduPilot Copter, missions sent from your own software → Raspberry Pi → Pixhawk over MAVLink.

How the pieces relate: the **battery** feeds three consumers (ESC directly, Pixhawk via its power input, Pi via the XL4016). The **Pixhawk** flies the aircraft and drives the ESC. The **Pi** is a companion computer that talks MAVLink to the Pixhawk over one UART; your software talks to the Pi over the network and your scripts on the Pi command the Pixhawk (missions, arming, modes).

---

## 1. Power tree

```
6S LiPo (22.2 V nom / 25.2 V full)
 ├── thick leads (12–14 AWG) ──► BLITZ E55 B+ / B− pads  (+ low-ESR cap at the pads)
 ├── Option A: power module ──► Pixhawk POWER port (5.3 V + voltage sense)
 │              └─ PM output XT60 ──► XL4016 IN
 └── Option B (no power module): parallel tap ──► XL4016 IN
                XL4016 OUT 5.1 V ──► Pi 5  AND  ──► Pixhawk servo rail (+/−)
```

### 1.1 Battery → ESC
- Solder an XT60/XT90 pigtail (12–14 AWG) to the E55's `B+` / `B−` pads.
- Solder the **supplied low-ESR capacitor** directly across the pads, correct polarity (striped side = negative). On 6S this is not optional — it protects the ESC from voltage spikes.
- The E55 is rated 2–6S, 55 A per motor — battery current must **not** pass through anything thinner than these leads.

### 1.2 Battery → XL4016 → Raspberry Pi 5
- XL4016 input: 8–40 V, so 6S (max 25.2 V) is fine. Wire `IN+`/`IN−` to a battery tap (through the power module output if you get one — see 1.3).
- **Before connecting the Pi**: power the XL4016 alone and turn the trimmer until the output reads **5.1 V** on a multimeter (5.1 rather than 5.0 compensates cable drop).
- Connect `OUT+`/`OUT−` to the Pi, short 18–20 AWG wires:
  - **USB-C (recommended)** — cut a USB-C cable or use a USB-C plug breakout. Since the XL4016 can't do USB-PD negotiation, the Pi assumes a 3 A supply and caps USB-peripheral current at 600 mA. Fix with one line in `/boot/firmware/config.txt`: `usb_max_current_enable=1` (or set EEPROM `PSU_MAX_CURRENT=5000`). Headless with nothing heavy on USB, it works even without this.
  - **GPIO alternative** — `OUT+` to pins 2 and 4 (5 V), `OUT−` to pin 6 (GND). Bypasses the Pi's input protection, so double-check voltage first; this is nonetheless the common way on drones.
- The XL4016 claims 8 A but plan on ~5 A continuous with a heatsink and airflow. A Pi 5 flight computer typically draws 1–2.5 A, 5 A worst case. An inline 5 A fuse on the input is cheap insurance.

### 1.3 Powering the Pixhawk
- **Option A (recommended): power module.** Most 2.4.8 kits include one — a small board with XT60 in/out and a 6-pin cable. Battery → PM → 6-pin DF13 into the **POWER** port. This gives the Pixhawk a clean 5.3 V *and battery-voltage telemetry*, which you want for battery failsafes on autonomous missions. Route only the XL4016 (Pi) through the PM's output XT60, never the ESC — the PM is only good for ~30 A continuous while the motors can pull far more. If you don't have one, they cost a few dollars — worth buying.
- **Option B: servo-rail power.** The Pixhawk also accepts power through the servo rail's `+`/`−` pins (valid range 4.1–5.7 V). Feed the same XL4016 5.1 V output into any spare `+`/`−` column on the rail. Works, but you get no battery-voltage telemetry (workaround: `BATT_MONITOR = 9` uses voltage/current reported by the ESC over its telemetry wire — see §2).
- The Pixhawk does **not** power the servo rail itself, and it does not power the ESC — that's normal; the E55's signal lines only need ground reference and signal.

### 1.4 Grounds
Everything already shares battery ground, but the signal links must carry their own ground wire: one harness GND to the Pixhawk rail, one GND in the TELEM2↔Pi cable. Never rely on "it's grounded through the power wiring elsewhere."

---

## 2. BLITZ E55 → Pixhawk (motor signals)

The E55's stack harness (per your iFlight wiring PDF) carries: `M1 M2 M3 M4 · TLM/TX · CUR · 2×BAT · 2×GND`. It was designed to plug into a BLITZ F7, so for the Pixhawk you keep the ESC-side plug and re-terminate the other end with Dupont/servo-style pins.

| Harness wire | Goes to | Notes |
|---|---|---|
| M1 | AUX 1 signal pin (S row) | = ArduPilot output SERVO9 |
| M2 | AUX 2 signal pin | = SERVO10 |
| M3 | AUX 3 signal pin | = SERVO11 |
| M4 | AUX 4 signal pin | = SERVO12 |
| GND (one of the two) | any `−` pin on the rail | signal ground reference — required |
| TLM / TX | SERIAL 4/5 port, pin 3 (RX4) | optional: per-motor RPM/V/A/temp telemetry |
| CUR | — not connected | analog current; tape it off |
| BAT ×2 | — **NOT CONNECTED, INSULATE** | raw 22 V for FPV FCs; would destroy a Pixhawk input |
| GND (second) | — insulate or use for TLM ground | |

Why AUX and not MAIN: on the Pixhawk 2.4.8 only the AUX (FMU) outputs support **DShot** — the digital protocol, no calibration, no mid-range drift, and it enables BLHeli_32 passthrough for configuring the ESC from Mission Planner. MAIN 1–8 sit behind the IO coprocessor and are PWM-only (firmware 4.5+ has an experimental `BRD_IO_DSHOT`, unreliable on 2.4.8 clones — avoid).

**Fallback (also fine):** classic PWM on MAIN 1–4 (`SERVO1–4` as Motor1–4, `MOT_PWM_TYPE=0`, then set `MOT_PWM_MIN=1000 / MOT_PWM_MAX=2000` or run ESC calibration). BLHeli_32 auto-detects PWM.

On the rail, the three pin rows are: **S** (signal, nearest the top of the case), **+** (middle), **−** (ground, bottom) — verify against the tiny `S / + / −` marks by the rail before plugging.

### Motor order & direction
Wire M1→AUX1 … M4→AUX4 first, then fix everything in software — never by re-soldering:
1. Props off. Mission Planner → Setup → Optional Hardware → **Motor Test**. Buttons spin motors in *position* order, clockwise from the front-right: A = front-right, B = back-right, C = back-left, D = front-left.
2. ArduPilot QUAD X wants: **Motor1 = front-right CCW, Motor2 = back-left CCW, Motor3 = front-left CW, Motor4 = back-right CW.**
3. If a wrong motor spins, remap `SERVO9–12_FUNCTION` (33=Motor1, 34=Motor2, 35=Motor3, 36=Motor4).
4. If a motor spins the wrong way, reverse it in **BLHeliSuite32** via Mission Planner passthrough (`SERVO_BLH_AUTO=1`, works on the DShot AUX pins) — or swap any two of that motor's three phase wires at the pads.

---

## 3. Raspberry Pi 5 ↔ Pixhawk (MAVLink link)

Use **TELEM2**, not USB, for flight (USB is fine on the bench but is not robust to disconnects and is awkward to mount).

Both sides are 3.3 V logic — direct wiring, no level shifter:

| Pixhawk TELEM2 (DF13 6-pin) | Raspberry Pi 5 header |
|---|---|
| pin 2 — TX | pin 10 — GPIO15 RXD |
| pin 3 — RX | pin 8 — GPIO14 TXD |
| pin 6 — GND | pin 14 — GND (any ground pin works: 6, 9, 14, 20…) |
| pin 1 — 5 V | **do not use** — cannot power a Pi 5 |

TELEM2 pin 1 is at the red-wire end of the cable; count from there.

### Pi configuration (Raspberry Pi OS Bookworm)
```
sudo raspi-config   # Interface Options → Serial Port:
                    #   login shell over serial → No
                    #   serial port hardware   → Yes
```
On the Pi 5 this maps the header UART (GPIO14/15) to `/dev/ttyAMA0`, with `/dev/serial0` as a stable symlink — equivalent to adding `dtparam=uart0=on` in `/boot/firmware/config.txt`. Reboot, then test:

```
pip install pymavlink
python3 - <<'EOF'
from pymavlink import mavutil
m = mavutil.mavlink_connection('/dev/serial0', baud=921600)
m.wait_heartbeat()
print("Heartbeat from sys %u comp %u" % (m.target_system, m.target_component))
EOF
```

### ArduPilot side
`SERIAL2_PROTOCOL = 2` (MAVLink 2), `SERIAL2_BAUD = 921`. If the link is long/noisy drop to 115200 (`SERIAL2_BAUD=115`) — 57k–921k all work.

### Plugging in your own software
Since your software pushes scripts to the Pi, the clean pattern is to run **mavlink-router** on the Pi as the single owner of the serial port, and have every script connect over UDP — several clients can then talk to the autopilot at once (your scripts + a GCS for monitoring):

```
# /etc/mavlink-router/main.conf
[UartEndpoint pixhawk]
Device = /dev/serial0
Baud = 921600

[UdpEndpoint scripts]
Mode = Server
Address = 0.0.0.0
Port = 14550

[UdpEndpoint gcs]
Mode = Normal
Address = 192.168.1.50   # your PC running Mission Planner, optional
Port = 14551
```
Scripts then use `mavutil.mavlink_connection('udpin:127.0.0.1:14550')` (or DroneKit `connect('127.0.0.1:14550')`). Mission upload = MAVLink `MISSION_COUNT`/`MISSION_ITEM_INT` handshake, which pymavlink's `mav.mission_*` helpers and DroneKit's `vehicle.commands` wrap for you; then set mode AUTO and arm.

---

## 4. Key ArduPilot parameters (Copter, quad X, motors on AUX/DShot)

| Parameter | Value | Meaning |
|---|---|---|
| FRAME_CLASS / FRAME_TYPE | 1 / 1 | Quad, X |
| SERVO1..4_FUNCTION | 0 | free MAIN 1–4 |
| SERVO9..12_FUNCTION | 33 / 34 / 35 / 36 | Motor1–4 on AUX1–4 |
| MOT_PWM_TYPE | 6 | DShot600 |
| SERVO_BLH_AUTO | 1 | BLHeli_32 passthrough + telemetry |
| SERIAL2_PROTOCOL / SERIAL2_BAUD | 2 / 921 | Pi on TELEM2 |
| SERIAL4_PROTOCOL / SERIAL4_BAUD | 16 / 115 | ESC telemetry on SERIAL4 RX (optional) |
| BATT_MONITOR | 9 | battery V/A from ESC telemetry (use 4 + preset "Power Module" if you fit a PM) |
| BRD_SAFETY_DEFLT | 0 (optional) | skip the hardware safety button if you don't install one |

---

## 5. Also needed for autonomous missions

- **GPS + compass** (usually the M8N combo in 2.4.8 kits): GPS port + I2C port (or the single 6-pin+4-pin cable). Missions won't arm without 3D fix.
- **RC receiver (recommended even for scripted flight)**: SBUS/PPM into the `RCIN` pin column — a manual takeover path saves airframes. Flying with no RC at all requires extra failsafe/arming parameter work.
- Safety switch + buzzer (both usually included) into their dedicated ports.

## 6. First power-up checklist

1. Props off. Bench-test the XL4016 alone → set 5.1 V.
2. First battery plug-in through a smoke-stopper / current-limited supply if you have one.
3. Verify: Pixhawk boots (POWER or rail), Pi boots, no hot components.
4. USB → Mission Planner → flash ArduPilot Copter (fmuv3; MP auto-detects), set params above.
5. Motor Test (props still off): order, direction, telemetry visible (`esc*` fields in Status).
6. Pi link test (§3), then a mission upload dry-run on the bench.
7. Calibrate accel + compass, then hover test in Stabilize/AltHold before any AUTO mission.

---

*Diagram: see `VTOL wiring diagram.svg` in this project. Based on your iFlight BLITZ F7/E55 wiring PDF; E55 specs from iFlight; Pixhawk/DShot/companion-computer details from ArduPilot docs; Pi 5 UART/power from Raspberry Pi docs.*

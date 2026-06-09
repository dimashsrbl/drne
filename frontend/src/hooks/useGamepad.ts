import { useEffect, useRef, useState } from 'react'

export type GamepadAxes = {
  roll:     number   // −1000…1000
  pitch:    number   // −1000…1000
  throttle: number   // 0…1000
  yaw:      number   // −1000…1000
}

/**
 * Переключатели Jumper T-Pro V2 в режиме USB HID:
 *   AXIS 4 = SWA (2-поз.): −1 = вниз, 1 = вверх
 *   AXIS 5 = SWB (3-поз.): −1 / 0 / 1
 *   AXIS 6 = SWC (3-поз.): −1 / 0 / 1
 *   AXIS 7 = SWD (2-поз.): −1 = вниз, 1 = вверх
 *
 * Маппинг действий:
 *   SWA вверх (1)  → ARM
 *   SWA вниз (−1)  → DISARM
 *   SWD вверх (1)  → RTL
 */
export type GamepadSwitches = {
  swa: number   // −1 / 0 / 1
  swb: number
  swc: number
  swd: number
  /** ARM: SWA == 1 */
  armSwitch: boolean
  /** RTL: SWD == 1 */
  rtlSwitch: boolean
}

export type GamepadState = {
  connected: boolean
  name: string | null
  axes: GamepadAxes
  switches: GamepadSwitches
}

// ── Маппинг стиков (Jumper T-Pro V2, Mode 2, USB HID) ─────────────
const AXIS_MAP = {
  roll:     { index: 0, invert: false },
  pitch:    { index: 1, invert: true  }, // push forward = positive pitch
  throttle: { index: 2, invert: false }, // −1=min, +1=max
  yaw:      { index: 3, invert: false },
  swa:      4,
  swb:      5,
  swc:      6,
  swd:      7,
}

const DEADZONE = 0.05

function applyDeadzone(v: number, dz: number): number {
  if (Math.abs(v) < dz) return 0
  return v
}

/** Округлить значение оси переключателя до -1 / 0 / 1 */
function snapSwitch(v: number): -1 | 0 | 1 {
  if (v > 0.5)  return  1
  if (v < -0.5) return -1
  return 0
}

function readAxes(gp: Gamepad): GamepadAxes {
  const rawRoll     = applyDeadzone(gp.axes[AXIS_MAP.roll.index]     ?? 0, DEADZONE)
  const rawPitch    = applyDeadzone(gp.axes[AXIS_MAP.pitch.index]    ?? 0, DEADZONE)
  const rawThrottle = applyDeadzone(gp.axes[AXIS_MAP.throttle.index] ?? 0, DEADZONE)
  const rawYaw      = applyDeadzone(gp.axes[AXIS_MAP.yaw.index]      ?? 0, DEADZONE)

  return {
    roll:     Math.round((AXIS_MAP.roll.invert     ? -rawRoll     : rawRoll)     * 1000),
    pitch:    Math.round((AXIS_MAP.pitch.invert    ? -rawPitch    : rawPitch)    * 1000),
    throttle: Math.round(((AXIS_MAP.throttle.invert ? -rawThrottle : rawThrottle) + 1) / 2 * 1000),
    yaw:      Math.round((AXIS_MAP.yaw.invert      ? -rawYaw      : rawYaw)      * 1000),
  }
}

function readSwitches(gp: Gamepad): GamepadSwitches {
  const swa = snapSwitch(gp.axes[AXIS_MAP.swa] ?? 0)
  const swb = snapSwitch(gp.axes[AXIS_MAP.swb] ?? 0)
  const swc = snapSwitch(gp.axes[AXIS_MAP.swc] ?? 0)
  const swd = snapSwitch(gp.axes[AXIS_MAP.swd] ?? 0)
  return {
    swa,
    swb,
    swc,
    swd,
    armSwitch: swa === 1,
    rtlSwitch: swd === 1,
  }
}

const ZERO_AXES: GamepadAxes = { roll: 0, pitch: 0, throttle: 0, yaw: 0 }
const ZERO_SWITCHES: GamepadSwitches = {
  swa: 0, swb: 0, swc: 0, swd: 0,
  armSwitch: false, rtlSwitch: false,
}

export function useGamepad(): GamepadState {
  const [state, setState] = useState<GamepadState>({
    connected: false,
    name: null,
    axes: ZERO_AXES,
    switches: ZERO_SWITCHES,
  })

  const rafRef = useRef<number | null>(null)

  useEffect(() => {
    function poll() {
      const gamepads = navigator.getGamepads()
      const gp = gamepads[0] ?? null

      if (gp && gp.connected) {
        setState({
          connected: true,
          name: gp.id,
          axes:     readAxes(gp),
          switches: readSwitches(gp),
        })
      } else {
        setState({ connected: false, name: null, axes: ZERO_AXES, switches: ZERO_SWITCHES })
      }

      rafRef.current = requestAnimationFrame(poll)
    }

    rafRef.current = requestAnimationFrame(poll)
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current)
    }
  }, [])

  return state
}

/**
 * BOB57 Cinematic 6S — 3D геометрия дрона.
 *
 * Реальные размеры:
 *   - Wheelbase: 255 мм → плечо луча ~90 мм (от центра до мотора)
 *   - Diagonal:  245 мм
 *   - Weight:    620 г
 *   - Props:     6" (152 мм диаметр)
 *   - Body:      ~80×80×30 мм
 *
 * Three.js единицы = метры.
 */

import * as THREE from 'three'

// ── Константы размеров ─────────────────────────────────────────────
const BODY_R    = 0.042   // радиус корпуса, м
const BODY_H    = 0.028   // высота корпуса, м
const ARM_LEN   = 0.092   // длина луча от центра до мотора, м
const ARM_W     = 0.014   // ширина луча
const ARM_H     = 0.008   // высота луча
const MOTOR_R   = 0.012   // радиус мотора
const MOTOR_H   = 0.022   // высота мотора
const PROP_R_IN = 0.022   // внутренний радиус пропа
const PROP_R_OUT= 0.076   // внешний радиус пропа (6" = 152мм / 2)
const PROP_H    = 0.001   // толщина диска пропа

// углы лучей в X-конфигурации (45°, 135°, 225°, 315°)
const ARM_ANGLES = [
  Math.PI / 4,
  3 * Math.PI / 4,
  5 * Math.PI / 4,
  7 * Math.PI / 4,
]

// направление вращения пропов: CW = 1, CCW = -1 (стандартная схема квадро)
const PROP_DIR = [1, -1, 1, -1]

export class DroneModel {
  readonly root: THREE.Group

  private _propGroups: THREE.Group[] = []
  private _motorLights: THREE.PointLight[] = []

  constructor() {
    this.root = new THREE.Group()
    this._build()
  }

  // ── Публичный API ──────────────────────────────────────────────

  /** Обновить анимацию (вызывать каждый кадр) */
  update(dt: number, throttle: number, armed: boolean): void {
    const rpm = armed ? 0.3 + throttle * 0.7 : 0
    // ~1200..4000 RPM для BOB57, переводим в rad/frame
    const rotSpeed = rpm * 40 * dt  // rad/s масштаб

    for (let i = 0; i < this._propGroups.length; i++) {
      this._propGroups[i].rotation.y += PROP_DIR[i] * rotSpeed
    }

    // Яркость LEDs на моторах
    const intensity = armed ? 0.4 + throttle * 1.2 : 0
    for (const light of this._motorLights) {
      light.intensity = intensity
    }
  }

  /** Применить крен/тангаж/курс из телеметрии (градусы) */
  setAttitude(roll: number, pitch: number, heading: number): void {
    // Three.js euler: Y = yaw (вокруг up), X = pitch, Z = roll
    // heading: 0 = север = -Z в Three.js (нам нужно повернуть модель)
    this.root.rotation.order = 'YXZ'
    this.root.rotation.y     = -THREE.MathUtils.degToRad(heading)
    this.root.rotation.x     =  THREE.MathUtils.degToRad(pitch)
    this.root.rotation.z     = -THREE.MathUtils.degToRad(roll)
  }

  // ── Построение геометрии ───────────────────────────────────────

  private _build(): void {
    const g = this.root

    // --- Корпус ---
    const bodyMat = new THREE.MeshStandardMaterial({
      color: 0x1a1a2e,
      roughness: 0.7,
      metalness: 0.4,
    })
    const bodyGeo = new THREE.CylinderGeometry(BODY_R, BODY_R * 0.85, BODY_H, 6)
    const body    = new THREE.Mesh(bodyGeo, bodyMat)
    body.castShadow = true
    g.add(body)

    // Верхняя крышка (пластик)
    const topGeo = new THREE.CylinderGeometry(BODY_R * 0.7, BODY_R * 0.7, 0.006, 6)
    const topMat = new THREE.MeshStandardMaterial({ color: 0x16213e, roughness: 0.5 })
    const top    = new THREE.Mesh(topGeo, topMat)
    top.position.y = BODY_H / 2 + 0.003
    g.add(top)

    // Носовая часть (камера)
    const camMat = new THREE.MeshStandardMaterial({ color: 0x0f3460, roughness: 0.3, metalness: 0.6 })
    const camGeo = new THREE.BoxGeometry(0.018, 0.022, 0.030)
    const cam    = new THREE.Mesh(camGeo, camMat)
    cam.position.set(0, 0.005, -(BODY_R + 0.01))
    cam.rotation.x = -0.2
    g.add(cam)

    // Линза камеры
    const lensGeo = new THREE.CylinderGeometry(0.007, 0.007, 0.006, 16)
    const lensMat = new THREE.MeshStandardMaterial({ color: 0x000010, roughness: 0.1, metalness: 0.9 })
    const lens    = new THREE.Mesh(lensGeo, lensMat)
    lens.rotation.x = Math.PI / 2
    lens.position.set(0, 0.005, -(BODY_R + 0.025))
    g.add(lens)

    // --- Лучи и моторы ---
    const armMat = new THREE.MeshStandardMaterial({
      color: 0x0d0d0d,
      roughness: 0.8,
      metalness: 0.3,
    })
    const motorMat = new THREE.MeshStandardMaterial({
      color: 0xe05c00,
      roughness: 0.4,
      metalness: 0.7,
    })
    const propMat = new THREE.MeshStandardMaterial({
      color: 0x00aaff,
      roughness: 0.2,
      transparent: true,
      opacity: 0.6,
      side: THREE.DoubleSide,
    })

    for (let i = 0; i < 4; i++) {
      const angle = ARM_ANGLES[i]
      const ax = Math.sin(angle) * (ARM_LEN / 2)
      const az = Math.cos(angle) * (ARM_LEN / 2)
      const mx = Math.sin(angle) * ARM_LEN
      const mz = Math.cos(angle) * ARM_LEN

      // Луч
      const armGeo = new THREE.BoxGeometry(ARM_W, ARM_H, ARM_LEN)
      const arm    = new THREE.Mesh(armGeo, armMat)
      arm.position.set(ax, 0, az)
      arm.rotation.y = angle
      arm.castShadow = true
      g.add(arm)

      // Мотор
      const motorGeo = new THREE.CylinderGeometry(MOTOR_R, MOTOR_R * 1.1, MOTOR_H, 12)
      const motor    = new THREE.Mesh(motorGeo, motorMat)
      motor.position.set(mx, MOTOR_H / 2, mz)
      motor.castShadow = true
      g.add(motor)

      // Пропеллер (диск)
      const propGroup = new THREE.Group()
      propGroup.position.set(mx, MOTOR_H + PROP_H / 2 + 0.002, mz)

      // Две лопасти (пластины)
      for (let b = 0; b < 2; b++) {
        const bladeGeo = new THREE.BoxGeometry(PROP_R_OUT * 2, PROP_H, 0.012)
        const blade    = new THREE.Mesh(bladeGeo, propMat)
        blade.rotation.y = (b * Math.PI) / 2
        blade.castShadow = false
        propGroup.add(blade)
      }

      // Полупрозрачный диск (визуализация зоны пропа)
      const discGeo = new THREE.CylinderGeometry(PROP_R_OUT, PROP_R_IN, PROP_H * 3, 24)
      const discMat = new THREE.MeshStandardMaterial({
        color: 0x44bbff,
        transparent: true,
        opacity: 0.15,
        side: THREE.DoubleSide,
      })
      const disc = new THREE.Mesh(discGeo, discMat)
      propGroup.add(disc)

      this._propGroups.push(propGroup)
      g.add(propGroup)

      // LED-фонарь на моторе
      const light = new THREE.PointLight(
        i < 2 ? 0xff2200 : 0x00ff44, // передние красные, задние зелёные
        0,
        0.4,
      )
      light.position.set(mx, MOTOR_H + 0.01, mz)
      this._motorLights.push(light)
      g.add(light)
    }

    // Ножки посадочного шасси
    const legMat = new THREE.MeshStandardMaterial({ color: 0x222222, roughness: 0.9 })
    for (const side of [-1, 1]) {
      const legV = new THREE.BoxGeometry(0.006, 0.025, 0.006)
      const legH = new THREE.BoxGeometry(0.060, 0.006, 0.006)
      const vMesh = new THREE.Mesh(legV, legMat)
      const hMesh = new THREE.Mesh(legH, legMat)
      vMesh.position.set(side * BODY_R * 0.7, -BODY_H / 2 - 0.012, 0)
      hMesh.position.set(side * BODY_R * 0.7, -BODY_H / 2 - 0.024, 0)
      g.add(vMesh)
      g.add(hMesh)
    }
  }
}

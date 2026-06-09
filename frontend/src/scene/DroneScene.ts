/**
 * DroneScene — Three.js сцена для симуляции BOB57.
 *
 * Принимает:
 *   - canvas HTMLCanvasElement
 *   - telemetry через метод update()
 *   - wind параметры через setWind()
 *
 * Координатная система:
 *   three.js: +X = east, +Y = up, +Z = south
 *   heading=0 (north) → модель смотрит в -Z
 */

import * as THREE from 'three'
import { DroneModel } from './DroneModel'

export interface TelemetrySnapshot {
  lat: number | null
  lon: number | null
  alt: number | null
  heading: number | null
  roll: number | null
  pitch: number | null
  speed: number | null
  armed: boolean | null
  battery: number | null
  mode: string | null
}

const ORIGIN_LAT = 51.1801
const ORIGIN_LON = 71.4460
const DEG_TO_M_LAT = 111_000
function degToMLon(lat: number) { return 111_000 * Math.cos((lat * Math.PI) / 180) }

/** Конвертировать lat/lon/alt в Three.js локальные координаты */
function geoToLocal(lat: number, lon: number, alt: number): THREE.Vector3 {
  const north = (lat - ORIGIN_LAT) * DEG_TO_M_LAT
  const east  = (lon - ORIGIN_LON) * degToMLon(ORIGIN_LAT)
  return new THREE.Vector3(east, alt, -north)
}

/** Обратная конвертация — Three.js XZ → lat/lon */
function localToGeo(x: number, z: number): { lat: number; lon: number } {
  const north = -z
  const east  =  x
  return {
    lat: ORIGIN_LAT + north / DEG_TO_M_LAT,
    lon: ORIGIN_LON + east  / degToMLon(ORIGIN_LAT),
  }
}

export interface Waypoint {
  id:  number
  lat: number
  lon: number
}

// ── Количество ветровых частиц ─────────────────────────────────
const WIND_PARTICLE_COUNT = 800

export class DroneScene {
  private _renderer: THREE.WebGLRenderer
  private _scene:    THREE.Scene
  private _camera:   THREE.PerspectiveCamera
  private _drone!:   DroneModel

  // Ветер
  private _windSpeed = 0
  private _windDir   = 0
  private _windParticles: THREE.Points | null = null
  private _windPositions: Float32Array | null = null

  // Анимация
  private _animId   = 0
  private _clock    = new THREE.Clock()
  private _lastTelemetry: TelemetrySnapshot | null = null

  // Управление камерой мышью (3rd-person)
  private _camTheta  = 0
  private _camPhi    = 0.4
  private _camDist   = 12.0
  private _isDragging = false
  private _lastMouse  = { x: 0, y: 0 }
  private _mouseMoved = 0
  private _canvas: HTMLCanvasElement

  // Режим камеры
  private _camMode: 'third' | 'fpv' = 'third'
  private _fpvTiltDeg = 25   // наклон FPV-камеры вниз (типичный 20-45°)

  // Кэш позиции дрона для камеры
  private _droneWorldPos = new THREE.Vector3(0, 2, 0)

  // ── Визуальный маяк над дроном ────────────────────────────
  private _beacon!: THREE.Group
  private _beaconBeam!: THREE.Mesh
  private _beaconLight!: THREE.PointLight
  private _beaconPulse = 0

  // ── Миссионные маршрутные точки ──────────────────────────────
  private _placingMode   = false
  private _raycaster     = new THREE.Raycaster()
  private _groundPlane!:  THREE.Mesh          // невидимая плоскость для raycast
  private _wpGroup       = new THREE.Group()   // маркеры
  private _wpLine:        THREE.Line | null = null
  private _wpLinePending: THREE.Line | null = null  // пунктирная линия до дрона
  private _onGroundClick: ((lat: number, lon: number) => void) | null = null
  private _activeWpIdx   = -1   // подсветка текущего целевого WP

  constructor(canvas: HTMLCanvasElement) {
    this._canvas = canvas

    // ── Renderer ─────────────────────────────────────────────
    this._renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false })
    this._renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    this._renderer.shadowMap.enabled = true
    this._renderer.shadowMap.type = THREE.PCFSoftShadowMap
    this._renderer.toneMapping = THREE.ACESFilmicToneMapping
    this._renderer.toneMappingExposure = 1.2

    // ── Scene ─────────────────────────────────────────────────
    this._scene = new THREE.Scene()
    this._scene.background = new THREE.Color(0x87CEEB)
    this._scene.fog = new THREE.FogExp2(0x87CEEB, 0.003)

    // ── Camera ────────────────────────────────────────────────
    this._camera = new THREE.PerspectiveCamera(60, canvas.clientWidth / canvas.clientHeight, 0.1, 3000)
    this._camera.position.set(0, 4, 6)
    this._camera.lookAt(0, 2, 0)

    this._buildWorld()
    this._buildWindParticles()
    this._buildDrone()
    this._buildBeacon()
    this._scene.add(this._wpGroup)
    this._setupMouseControls()
    this._onResize()
    window.addEventListener('resize', () => this._onResize())
    this._animate()
  }

  // ── Публичный API ────────────────────────────────────────────

  update(telemetry: TelemetrySnapshot): void {
    this._lastTelemetry = telemetry
  }

  setWind(speedMs: number, directionDeg: number): void {
    this._windSpeed = speedMs
    this._windDir   = directionDeg
  }

  /** Включить/выключить режим расстановки точек маршрута */
  setPlacingMode(enabled: boolean): void {
    this._placingMode = enabled
    this._canvas.style.cursor = enabled ? 'crosshair' : 'default'
  }

  /** Подписаться на клик по земле (только в режиме расстановки) */
  onGroundClick(cb: (lat: number, lon: number) => void): void {
    this._onGroundClick = cb
  }

  /** Переключить режим камеры */
  setCameraMode(mode: 'third' | 'fpv'): void {
    this._camMode = mode
    // В FPV режиме скрываем модель дрона и маяк (смотрим изнутри)
    this._drone.root.visible = mode === 'third'
    // Маяк тоже скрываем в FPV
    if (mode === 'fpv') this._beacon.visible = false
    // В FPV не нужна орбита мышью
    this._canvas.style.cursor = mode === 'fpv' ? 'default' : (this._placingMode ? 'crosshair' : 'default')
  }

  getCameraMode(): 'third' | 'fpv' {
    return this._camMode
  }

  setFpvTilt(deg: number): void {
    this._fpvTiltDeg = Math.max(0, Math.min(60, deg))
  }

  /** Сбросить камеру к дрону (3rd-person) */
  resetCamera(): void {
    this._camMode  = 'third'
    this._camTheta = 0
    this._camPhi   = 0.5
    this._camDist  = 12
    this._drone.root.visible = true
  }

  /** Обновить визуализацию маршрутных точек */
  setWaypoints(waypoints: Waypoint[], activeIdx = -1): void {
    this._activeWpIdx = activeIdx
    this._rebuildWpMarkers(waypoints)
  }

  dispose(): void {
    cancelAnimationFrame(this._animId)
    window.removeEventListener('resize', () => this._onResize())
    this._renderer.dispose()
  }

  // ── Построение мира ──────────────────────────────────────────

  private _buildWorld(): void {
    // Солнце
    const sun = new THREE.DirectionalLight(0xfff5e0, 2.5)
    sun.position.set(300, 500, -200)
    sun.castShadow = true
    sun.shadow.mapSize.width  = 2048
    sun.shadow.mapSize.height = 2048
    sun.shadow.camera.near = 1
    sun.shadow.camera.far  = 2000
    sun.shadow.camera.left = sun.shadow.camera.bottom = -500
    sun.shadow.camera.right = sun.shadow.camera.top   = 500
    this._scene.add(sun)

    // Полусфера неба/земли
    const hemi = new THREE.HemisphereLight(0x87CEEB, 0x5a8a30, 0.8)
    this._scene.add(hemi)

    // Ambient
    this._scene.add(new THREE.AmbientLight(0xffffff, 0.3))

    // ── Земля ────────────────────────────────────────────────

    // Основная трава
    const groundGeo = new THREE.PlaneGeometry(2000, 2000, 40, 40)
    // Слегка рандомизируем вершины для рельефа
    const pos = groundGeo.attributes.position
    for (let i = 0; i < pos.count; i++) {
      const x = pos.getX(i)
      const z = pos.getZ(i)
      if (Math.abs(x) > 5 || Math.abs(z) > 5) {
        pos.setY(i, (Math.sin(x * 0.05) * Math.cos(z * 0.05) +
                     Math.sin(x * 0.02) * Math.sin(z * 0.03)) * 2)
      }
    }
    groundGeo.computeVertexNormals()

    const groundMat = new THREE.MeshStandardMaterial({
      color: 0x4a7c2f,
      roughness: 0.95,
      metalness: 0.0,
    })
    const ground = new THREE.Mesh(groundGeo, groundMat)
    ground.rotation.x = -Math.PI / 2
    ground.receiveShadow = true
    this._scene.add(ground)

    // Взлётная площадка (серый круг)
    const padGeo = new THREE.CylinderGeometry(3, 3, 0.05, 32)
    const padMat = new THREE.MeshStandardMaterial({ color: 0x666666, roughness: 0.6 })
    const pad    = new THREE.Mesh(padGeo, padMat)
    pad.position.y = 0.025
    pad.receiveShadow = true
    this._scene.add(pad)

    // H-маркер
    const hMat = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.4 })
    for (const bar of [
      new THREE.BoxGeometry(0.3, 0.06, 1.5),
      new THREE.BoxGeometry(0.3, 0.06, 1.5),
      new THREE.BoxGeometry(1.2, 0.06, 0.3),
    ]) {
      const mesh = new THREE.Mesh(bar, hMat)
      mesh.receiveShadow = true
      this._scene.add(mesh)
    }
    // Горизонтальная черта H
    const hBar = new THREE.Mesh(new THREE.BoxGeometry(1.2, 0.06, 0.3), hMat)
    hBar.position.set(0, 0.06, 0)
    this._scene.add(hBar)
    const leftBar = new THREE.Mesh(new THREE.BoxGeometry(0.3, 0.06, 1.5), hMat)
    leftBar.position.set(-0.45, 0.06, 0)
    this._scene.add(leftBar)
    const rightBar = new THREE.Mesh(new THREE.BoxGeometry(0.3, 0.06, 1.5), hMat)
    rightBar.position.set(0.45, 0.06, 0)
    this._scene.add(rightBar)

    // Несколько деревьев/столбов для ориентации
    this._addLandmarks()

    // Сетка-помощник
    const gridHelper = new THREE.GridHelper(200, 40, 0x333333, 0x333333)
    gridHelper.position.y = 0.1
    gridHelper.material = new THREE.LineBasicMaterial({ color: 0x555555, transparent: true, opacity: 0.25 })
    this._scene.add(gridHelper)

    // Компасные стрелки (N/S/E/W)
    this._addCompassMarkers()
  }

  private _addLandmarks(): void {
    const trunkMat = new THREE.MeshStandardMaterial({ color: 0x5c3d1a, roughness: 0.9 })
    const leafMat  = new THREE.MeshStandardMaterial({ color: 0x2e6b1f, roughness: 0.8 })

    const positions = [
      [30, 0, -50], [-45, 0, 20], [60, 0, 35], [-30, 0, -70], [80, 0, -20],
    ]
    for (const [x, , z] of positions) {
      const h = 4 + Math.random() * 4
      const trunk = new THREE.Mesh(new THREE.CylinderGeometry(0.3, 0.45, h, 8), trunkMat)
      trunk.position.set(x, h / 2, z)
      trunk.castShadow = true
      this._scene.add(trunk)
      const leaves = new THREE.Mesh(new THREE.ConeGeometry(2.5, h * 0.7, 8), leafMat)
      leaves.position.set(x, h + h * 0.2, z)
      leaves.castShadow = true
      this._scene.add(leaves)
    }

    // Здания на горизонте
    const buildMat = new THREE.MeshStandardMaterial({ color: 0x888898, roughness: 0.7, metalness: 0.2 })
    const buildings = [
      [150, 20, -100], [-200, 30, -150], [180, 15, 120], [-120, 25, 200],
    ]
    for (const [x, h, z] of buildings) {
      const b = new THREE.Mesh(new THREE.BoxGeometry(15, h, 15), buildMat)
      b.position.set(x, h / 2, z)
      b.castShadow = true
      this._scene.add(b)
    }

    // Невидимая плоская плоскость для raycast (точное y=0)
    this._groundPlane = new THREE.Mesh(
      new THREE.PlaneGeometry(2000, 2000),
      new THREE.MeshBasicMaterial({ visible: false, side: THREE.DoubleSide }),
    )
    this._groundPlane.rotation.x = -Math.PI / 2
    this._groundPlane.position.y = 0.01
    this._scene.add(this._groundPlane)
  }

  private _addCompassMarkers(): void {
    const dirs: [string, number, number, number][] = [
      ['N', 0, 0, -50],
      ['S', 0, 0,  50],
      ['E', 50, 0, 0],
      ['W', -50, 0, 0],
    ]
    const colors = [0xff4444, 0xffffff, 0x44ff44, 0x4444ff]

    dirs.forEach(([, cx, cy, cz], i) => {
      const sphere = new THREE.Mesh(
        new THREE.SphereGeometry(0.4, 8, 8),
        new THREE.MeshStandardMaterial({ color: colors[i], emissive: colors[i], emissiveIntensity: 0.5 }),
      )
      sphere.position.set(cx, cy + 0.5, cz)
      this._scene.add(sphere)

      // Столб под маркером
      const pole = new THREE.Mesh(
        new THREE.CylinderGeometry(0.05, 0.05, 1, 6),
        new THREE.MeshStandardMaterial({ color: 0xaaaaaa }),
      )
      pole.position.set(cx, cy + 0.0, cz)
      this._scene.add(pole)
    })
  }

  private _rebuildWpMarkers(waypoints: Waypoint[]): void {
    // Очищаем старые маркеры
    while (this._wpGroup.children.length > 0) {
      this._wpGroup.remove(this._wpGroup.children[0])
    }
    if (this._wpLine)        { this._scene.remove(this._wpLine);        this._wpLine = null }
    if (this._wpLinePending) { this._scene.remove(this._wpLinePending); this._wpLinePending = null }
    if (waypoints.length === 0) return

    const COLORS = [0xff6b35, 0xffd700, 0x00e5ff, 0x69ff47, 0xff47c7, 0xff1744]

    // Маркеры точек
    waypoints.forEach((wp, i) => {
      const pos = geoToLocal(wp.lat, wp.lon, 0)
      const isActive = i === this._activeWpIdx
      const color = isActive ? 0xffffff : COLORS[i % COLORS.length]

      const group = new THREE.Group()
      group.position.set(pos.x, 0, pos.z)

      // Столб
      const poleH = isActive ? 6 : 4
      const pole = new THREE.Mesh(
        new THREE.CylinderGeometry(0.06, 0.06, poleH, 8),
        new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: isActive ? 0.8 : 0.4 }),
      )
      pole.position.y = poleH / 2
      group.add(pole)

      // Шар наверху
      const ball = new THREE.Mesh(
        new THREE.SphereGeometry(isActive ? 0.5 : 0.35, 12, 12),
        new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: 0.6 }),
      )
      ball.position.y = poleH + 0.35
      group.add(ball)

      // Кольцо на земле
      const ring = new THREE.Mesh(
        new THREE.RingGeometry(0.8, 1.1, 24),
        new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide, transparent: true, opacity: 0.5 }),
      )
      ring.rotation.x = -Math.PI / 2
      ring.position.y = 0.05
      group.add(ring)

      // Номер (простой: индикатор кол-ва маленьких кубиков)
      for (let d = 0; d < Math.min(i + 1, 5); d++) {
        const dot = new THREE.Mesh(
          new THREE.BoxGeometry(0.12, 0.12, 0.12),
          new THREE.MeshStandardMaterial({ color: 0xffffff }),
        )
        dot.position.set(-0.35 + d * 0.18, poleH + 0.35, 0.45)
        group.add(dot)
      }

      this._wpGroup.add(group)
    })

    // Линия маршрута между точками
    const linePoints: THREE.Vector3[] = []

    // Начало — позиция дрона / домашняя точка
    linePoints.push(new THREE.Vector3(0, 0.5, 0))

    waypoints.forEach((wp) => {
      const p = geoToLocal(wp.lat, wp.lon, 0)
      linePoints.push(new THREE.Vector3(p.x, 0.5, p.z))
    })

    // Возврат домой
    linePoints.push(new THREE.Vector3(0, 0.5, 0))

    const lineGeo = new THREE.BufferGeometry().setFromPoints(linePoints)
    this._wpLine = new THREE.Line(
      lineGeo,
      new THREE.LineBasicMaterial({ color: 0xffd700, linewidth: 2, transparent: true, opacity: 0.7 }),
    )
    this._scene.add(this._wpLine)
  }

  private _buildBeacon(): void {
    this._beacon = new THREE.Group()

    // Вертикальный луч — узкий столбик света высотой 8м
    const beamGeo = new THREE.CylinderGeometry(0.08, 0.3, 8, 8)
    const beamMat = new THREE.MeshBasicMaterial({
      color: 0x00e5ff,
      transparent: true,
      opacity: 0.35,
    })
    this._beaconBeam = new THREE.Mesh(beamGeo, beamMat)
    this._beaconBeam.position.y = 4
    this._beacon.add(this._beaconBeam)

    // Пульсирующий шар на вершине
    const ballGeo = new THREE.SphereGeometry(0.35, 12, 12)
    const ballMat = new THREE.MeshBasicMaterial({ color: 0x00e5ff })
    const ball    = new THREE.Mesh(ballGeo, ballMat)
    ball.position.y = 8.4
    this._beacon.add(ball)

    // Точечный свет
    this._beaconLight = new THREE.PointLight(0x00e5ff, 3, 15)
    this._beaconLight.position.y = 8
    this._beacon.add(this._beaconLight)

    this._scene.add(this._beacon)
  }

  private _buildWindParticles(): void {
    const geo = new THREE.BufferGeometry()
    const positions = new Float32Array(WIND_PARTICLE_COUNT * 3)
    const spread = 80

    for (let i = 0; i < WIND_PARTICLE_COUNT; i++) {
      positions[i * 3 + 0] = (Math.random() - 0.5) * spread
      positions[i * 3 + 1] = Math.random() * 60
      positions[i * 3 + 2] = (Math.random() - 0.5) * spread
    }

    geo.setAttribute('position', new THREE.BufferAttribute(positions, 3))
    this._windPositions = positions

    const mat = new THREE.PointsMaterial({
      color: 0xaaddff,
      size: 0.15,
      transparent: true,
      opacity: 0.6,
      sizeAttenuation: true,
    })

    this._windParticles = new THREE.Points(geo, mat)
    this._windParticles.visible = false
    this._scene.add(this._windParticles)
  }

  private _buildDrone(): void {
    this._drone = new DroneModel()
    this._drone.root.position.set(0, 0.2, 0)
    // Масштаб ×5 для видимости в сцене (симуляция, не физическая точность)
    this._drone.root.scale.setScalar(5)
    this._drone.root.castShadow = true
    this._scene.add(this._drone.root)
  }

  // ── Управление мышью ─────────────────────────────────────────

  private _raycastGround(clientX: number, clientY: number): { lat: number; lon: number } | null {
    const rect = this._canvas.getBoundingClientRect()
    const ndc  = new THREE.Vector2(
      ((clientX - rect.left) / rect.width)  * 2 - 1,
      -((clientY - rect.top) / rect.height) * 2 + 1,
    )
    this._raycaster.setFromCamera(ndc, this._camera)
    const hits = this._raycaster.intersectObject(this._groundPlane)
    if (hits.length === 0) return null
    const pt = hits[0].point
    return localToGeo(pt.x, pt.z)
  }

  private _setupMouseControls(): void {
    const canvas = this._canvas

    canvas.addEventListener('mousedown', (e) => {
      this._isDragging  = true
      this._mouseMoved  = 0
      this._lastMouse   = { x: e.clientX, y: e.clientY }
    })
    canvas.addEventListener('mouseup', (e) => {
      this._isDragging = false
      // Если практически не двигали мышь — считаем кликом
      if (this._mouseMoved < 5 && this._placingMode && this._onGroundClick) {
        const geo = this._raycastGround(e.clientX, e.clientY)
        if (geo) this._onGroundClick(geo.lat, geo.lon)
      }
    })
    canvas.addEventListener('mousemove', (e) => {
      if (!this._isDragging) return
      const dx = e.clientX - this._lastMouse.x
      const dy = e.clientY - this._lastMouse.y
      this._mouseMoved += Math.abs(dx) + Math.abs(dy)
      // Орбита только если не в режиме расстановки или если сдвиг > 5px
      if (!this._placingMode || this._mouseMoved > 5) {
        this._camTheta -= dx * 0.005
        this._camPhi    = Math.max(0.05, Math.min(Math.PI / 2 - 0.05, this._camPhi + dy * 0.005))
      }
      this._lastMouse = { x: e.clientX, y: e.clientY }
    })
    canvas.addEventListener('wheel', (e) => {
      this._camDist = Math.max(2, Math.min(100, this._camDist + e.deltaY * 0.01))
      e.preventDefault()
    }, { passive: false })

    // Touch
    let lastPinchDist = 0
    canvas.addEventListener('touchstart', (e) => {
      if (e.touches.length === 1) {
        this._isDragging = true
        this._lastMouse = { x: e.touches[0].clientX, y: e.touches[0].clientY }
      }
    })
    canvas.addEventListener('touchend', () => { this._isDragging = false })
    canvas.addEventListener('touchmove', (e) => {
      if (e.touches.length === 1 && this._isDragging) {
        const dx = e.touches[0].clientX - this._lastMouse.x
        const dy = e.touches[0].clientY - this._lastMouse.y
        this._camTheta -= dx * 0.005
        this._camPhi    = Math.max(0.05, Math.min(Math.PI / 2 - 0.05, this._camPhi + dy * 0.005))
        this._lastMouse = { x: e.touches[0].clientX, y: e.touches[0].clientY }
      }
      if (e.touches.length === 2) {
        const dx = e.touches[0].clientX - e.touches[1].clientX
        const dy = e.touches[0].clientY - e.touches[1].clientY
        const dist = Math.sqrt(dx * dx + dy * dy)
        if (lastPinchDist > 0) {
          this._camDist = Math.max(2, Math.min(100, this._camDist - (dist - lastPinchDist) * 0.05))
        }
        lastPinchDist = dist
      }
    }, { passive: true })
  }

  // ── Анимационный цикл ─────────────────────────────────────────

  private _animate(): void {
    this._animId = requestAnimationFrame(() => this._animate())
    const dt = this._clock.getDelta()

    this._updateDroneFromTelemetry()
    this._updateCamera(dt)
    this._updateWind(dt)

    this._renderer.render(this._scene, this._camera)
  }

  private _updateDroneFromTelemetry(): void {
    const t = this._lastTelemetry
    if (!t) return

    // Позиция
    const lat    = t.lat ?? ORIGIN_LAT
    const lon    = t.lon ?? ORIGIN_LON
    const alt    = t.alt ?? 0
    const pos    = geoToLocal(lat, lon, alt)
    const droneY = Math.max(0.25, pos.y)

    this._drone.root.position.set(pos.x, droneY, pos.z)
    this._droneWorldPos.set(pos.x, droneY, pos.z)

    // Маяк: всегда на позиции дрона, виден когда дрон низко (< 5 м)
    this._beacon.position.set(pos.x, droneY, pos.z)
    this._beaconPulse += 0.05
    const pulse = 0.5 + 0.5 * Math.sin(this._beaconPulse * 3)
    ;(this._beaconBeam.material as THREE.MeshBasicMaterial).opacity = (t.armed ? 0.5 : 0.25) * (0.6 + 0.4 * pulse)
    this._beaconLight.intensity = (t.armed ? 4 : 2) * (0.7 + 0.3 * pulse)
    // Скрываем маяк когда дрон уже высоко — не нужен
    this._beacon.visible = alt < 25

    // Ориентация
    const heading = t.heading ?? 0
    const roll    = t.roll    ?? 0
    const pitch   = t.pitch   ?? 0
    this._drone.setAttitude(roll, pitch, heading)

    // Анимация пропов
    const throttle = t.armed ? Math.max(0.2, alt > 0.2 ? 0.65 : 0.3) : 0
    this._drone.update(0.016, throttle, t.armed ?? false)
  }

  private _updateCamera(_dt: number): void {
    if (this._camMode === 'fpv') {
      this._updateCameraFPV()
    } else {
      this._updateCameraThird()
    }
  }

  private _updateCameraThird(): void {
    const target = this._droneWorldPos
    const r      = this._camDist
    const phi    = this._camPhi
    const theta  = this._camTheta

    const camX = target.x + r * Math.sin(theta) * Math.cos(phi)
    const camY = target.y + r * Math.sin(phi)
    const camZ = target.z + r * Math.cos(theta) * Math.cos(phi)

    this._camera.position.set(camX, Math.max(0.5, camY), camZ)
    this._camera.lookAt(target.x, target.y + 0.1, target.z)
  }

  private _updateCameraFPV(): void {
    const t = this._lastTelemetry
    const heading = t?.heading ?? 0
    const pitch   = (t?.pitch   ?? 0) * 0.5   // смягчаем тангаж для комфорта
    const roll    = (t?.roll    ?? 0) * 0.7    // смягчаем крен

    const drone = this._droneWorldPos

    // Направление «вперёд» дрона по курсу
    const hRad  = THREE.MathUtils.degToRad(heading)
    const fwdX  = Math.sin(hRad)
    const fwdZ  = -Math.cos(hRad)

    // FPV камера сидит на носу дрона (~0.25 м вперёд, 0.1 м вверх, масштаб ×5 = реальные 1.25м/0.5м)
    const noseDist = 0.35
    const camPos = new THREE.Vector3(
      drone.x + fwdX * noseDist,
      drone.y + 0.12,
      drone.z + fwdZ * noseDist,
    )

    // Смотрим вперёд с наклоном вниз (угол FPV-камеры) + тангаж дрона
    const tiltRad  = THREE.MathUtils.degToRad(this._fpvTiltDeg + pitch)
    const lookDist = 80

    const target = new THREE.Vector3(
      camPos.x + fwdX * lookDist * Math.cos(tiltRad),
      camPos.y - Math.sin(tiltRad) * lookDist,
      camPos.z + fwdZ * lookDist * Math.cos(tiltRad),
    )

    this._camera.position.copy(camPos)
    this._camera.lookAt(target)

    // Крен — наклоняем камеру вправо/влево как у реального FPV
    this._camera.rotation.z = -THREE.MathUtils.degToRad(roll)
  }

  private _updateWind(dt: number): void {
    if (!this._windParticles || !this._windPositions) return

    const hasWind = this._windSpeed > 0.1
    this._windParticles.visible = hasWind
    if (!hasWind) return

    // Направление ветра (куда дует)
    const toRad = ((this._windDir + 180) % 360) * (Math.PI / 180)
    const windVx =  Math.sin(toRad) * this._windSpeed * 0.5  // east component → +X
    const windVz = -Math.cos(toRad) * this._windSpeed * 0.5  // north component → -Z

    const spread = 80
    const pos = this._windPositions
    const dronePos = this._droneWorldPos

    for (let i = 0; i < WIND_PARTICLE_COUNT; i++) {
      pos[i * 3 + 0] += windVx * dt
      pos[i * 3 + 2] += windVz * dt

      // Если частица улетела далеко — спаунить её заново рядом с дроном
      const dx = pos[i * 3 + 0] - dronePos.x
      const dz = pos[i * 3 + 2] - dronePos.z
      if (Math.abs(dx) > spread / 2 || Math.abs(dz) > spread / 2) {
        pos[i * 3 + 0] = dronePos.x + (Math.random() - 0.5) * spread
        pos[i * 3 + 1] = dronePos.y + Math.random() * 30 - 5
        pos[i * 3 + 2] = dronePos.z + (Math.random() - 0.5) * spread
      }
    }

    const geo = this._windParticles.geometry
    geo.attributes.position.needsUpdate = true

    // Размер частиц зависит от силы ветра
    ;(this._windParticles.material as THREE.PointsMaterial).size = 0.1 + this._windSpeed * 0.02
  }

  private _onResize(): void {
    const canvas = this._canvas
    const w = canvas.clientWidth
    const h = canvas.clientHeight
    if (canvas.width !== w || canvas.height !== h) {
      this._renderer.setSize(w, h, false)
      this._camera.aspect = w / h
      this._camera.updateProjectionMatrix()
    }
  }
}

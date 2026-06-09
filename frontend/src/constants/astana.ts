/** Точка отсчёта «Астана» для карты, дома (RTL) и миссий. */
export const ASTANA = {
  lat: 51.169392,
  lon: 71.4491,
  /** грубо AMSL, м (для set_home; уточните под свою модель) */
  altAmsl: 347,
} as const

export const astanaCenter = { lat: ASTANA.lat, lng: ASTANA.lon }

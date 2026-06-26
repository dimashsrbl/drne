from pydantic import BaseModel, Field


class TelemetryResponse(BaseModel):
    lat: float | None = Field(default=None)
    lon: float | None = Field(default=None)
    alt: float | None = Field(default=None, description="Подъём над baseline (AGL), м")
    baro_alt_m: float | None = Field(default=None, description="Сырое значение барометра FC, м")
    baro_baseline_m: float | None = Field(default=None, description="Баро на ARM (=0 для AGL), м")
    battery: float | None = Field(default=None, description="Заряд батареи 0..100 %")
    status: str = Field(default="unknown")
    speed: float | None = Field(default=None, description="Горизонтальная скорость, м/с")
    armed: bool | None = Field(default=None)
    mode: str | None = Field(default=None, description="Режим полёта ArduCopter")
    heading: float | None = Field(default=None, description="Курс 0..360°")
    gps_sats: int | None = Field(default=None, description="Число GPS-спутников")
    gps_fix: int | None = Field(default=None, description="0=нет, 1=2D, 2=3D")
    source: str | None = Field(default=None, description="Источник телеметрии")
    note: str | None = Field(default=None, description="Подсказка/заметка от backend-профиля")

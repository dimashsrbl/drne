using System;
using UnityEngine;

public static class GeoUtil
{
    const double MetersPerDegLat = 111_320.0;

    public static void LocalToGeo(
        float xEastM,
        float zNorthM,
        double originLat,
        double originLon,
        out double lat,
        out double lon)
    {
        double metersPerDegLon = MetersPerDegLat * Math.Cos(originLat * Math.PI / 180.0);
        lat = originLat + (zNorthM / MetersPerDegLat);
        lon = originLon + (xEastM / metersPerDegLon);
    }

    public static Vector3 GeoToLocal(double lat, double lon, double originLat, double originLon)
    {
        double metersPerDegLon = MetersPerDegLat * Math.Cos(originLat * Math.PI / 180.0);
        float north = (float)((lat - originLat) * MetersPerDegLat);
        float east = (float)((lon - originLon) * metersPerDegLon);
        return new Vector3(east, 0f, north);
    }
}

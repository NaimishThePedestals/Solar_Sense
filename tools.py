# """
# tools.py
# --------
# Wrappers around the free APIs used to gather solar-relevant data
# for a given (lat, lon) coordinate pair.

# APIs used:
# 1. PVGIS (European Commission JRC) - solar PV potential & optimal tilt
# 2. Open-Meteo - current weather (temperature, clouds, humidity, wind)
# 3. Open-Meteo Air Quality - PM2.5 / PM10 for dust soiling estimation
# 4. NASA POWER - long-term annual solar radiation averages
# 5. Nominatim (OpenStreetMap) - reverse geocode coordinates to a place name

# All endpoints are FREE and require no API key.
# """

# import time
# from typing import Any

# import requests


# def _get_with_retry(url: str, params: dict | None = None, retries: int = 3, timeout: int = 20) -> dict:
#     last_err: Exception | None = None
#     for attempt in range(retries):
#         try:
#             r = requests.get(url, params=params, timeout=timeout, headers={"User-Agent": "SolarSense/1.0"})
#             r.raise_for_status()
#             return r.json()
#         except Exception as e:
#             last_err = e
#             if attempt < retries - 1:
#                 time.sleep(1.5 * (attempt + 1))
#     raise RuntimeError(f"Request failed after {retries} attempts for {url}: {last_err}")


# # ---------- 1. PVGIS ----------

# def get_pvgis_data(lat: float, lon: float, peak_power_kw: float = 1.0) -> dict[str, Any]:
#     url = "https://re.jrc.ec.europa.eu/api/v5_3/PVcalc"
#     params = {
#         "lat": lat,
#         "lon": lon,
#         "peakpower": peak_power_kw,
#         "loss": 14,
#         "outputformat": "json",
#         "optimalangles": 1,
#         "pvtechchoice": "crystSi",
#     }
#     data = _get_with_retry(url, params=params)
#     inputs = data.get("inputs", {})
#     outputs = data.get("outputs", {})
#     monthly = outputs.get("monthly", {}).get("fixed", [])
#     totals_fixed = outputs.get("totals", {}).get("fixed", {})
#     mounting = inputs.get("mounting_system", {}).get("fixed", {})
#     return {
#         "annual_yield_kwh_per_kwp": totals_fixed.get("E_y"),
#         "annual_irradiation_kwh_per_m2": totals_fixed.get("H(i)_y"),
#         "system_loss_percent": totals_fixed.get("l_total"),
#         "optimal_tilt_deg": mounting.get("slope", {}).get("value"),
#         "optimal_azimuth_deg": mounting.get("azimuth", {}).get("value"),
#         "monthly": [
#             {
#                 "month": m.get("month"),
#                 "energy_kwh": m.get("E_m"),
#                 "irradiation_kwh_per_m2": m.get("H(i)_m"),
#             }
#             for m in monthly
#         ],
#         "elevation_m": inputs.get("location", {}).get("elevation"),
#     }


# # ---------- 2. Open-Meteo current weather ----------

# def get_current_weather(lat: float, lon: float) -> dict[str, Any]:
#     """
#     Returns rich solar-relevant data in ONE Open-Meteo call (still free, no key).

#     NOTE: wind_speed_unit is explicitly set to 'ms'. Open-Meteo defaults to km/h;
#     the physics engine and analyst expect m/s. Without this, a light 15 km/h breeze
#     was being read as a 15 m/s gale, which falsely cooled the panels in the model.
#     """
#     url = "https://api.open-meteo.com/v1/forecast"
#     params = {
#         "latitude": lat,
#         "longitude": lon,
#         "wind_speed_unit": "ms",   # <-- critical: force m/s (default is km/h)
#         "current": (
#             "temperature_2m,apparent_temperature,relative_humidity_2m,dew_point_2m,"
#             "cloud_cover,wind_speed_10m,wind_direction_10m,"
#             "shortwave_radiation,direct_radiation,diffuse_radiation,"
#             "direct_normal_irradiance,uv_index"
#         ),
#         "hourly": (
#             "temperature_2m,cloud_cover,precipitation,precipitation_probability,"
#             "shortwave_radiation,direct_radiation,diffuse_radiation,uv_index"
#         ),
#         "daily": (
#             "temperature_2m_max,temperature_2m_min,sunrise,sunset,daylight_duration,"
#             "uv_index_max,shortwave_radiation_sum,cloud_cover_mean,"
#             "precipitation_sum,precipitation_probability_max"
#         ),
#         "timezone": "auto",
#         "forecast_days": 7,
#         "past_days": 1,
#     }
#     data = _get_with_retry(url, params=params)
#     current = data.get("current", {})
#     daily = data.get("daily", {})
#     hourly = data.get("hourly", {})
#     return {
#         "current_temp_c": current.get("temperature_2m"),
#         "current_apparent_temp_c": current.get("apparent_temperature"),
#         "current_humidity_percent": current.get("relative_humidity_2m"),
#         "current_dew_point_c": current.get("dew_point_2m"),
#         "current_cloud_cover_percent": current.get("cloud_cover"),
#         "current_wind_speed_m_s": current.get("wind_speed_10m"),
#         "current_wind_direction_deg": current.get("wind_direction_10m"),
#         "current_shortwave_radiation_w_m2": current.get("shortwave_radiation"),
#         "current_direct_radiation_w_m2": current.get("direct_radiation"),
#         "current_diffuse_radiation_w_m2": current.get("diffuse_radiation"),
#         "current_dni_w_m2": current.get("direct_normal_irradiance"),
#         "current_uv_index": current.get("uv_index"),
#         "daily": {
#             "dates": daily.get("time", []),
#             "sunrise": daily.get("sunrise", []),
#             "sunset": daily.get("sunset", []),
#             "daylight_hours": [round((s or 0) / 3600, 2) for s in daily.get("daylight_duration", [])],
#             "max_temp_c": daily.get("temperature_2m_max", []),
#             "min_temp_c": daily.get("temperature_2m_min", []),
#             "uv_index_max": daily.get("uv_index_max", []),
#             "radiation_sum_mj_m2": daily.get("shortwave_radiation_sum", []),
#             "avg_cloud_cover_percent": daily.get("cloud_cover_mean", []),
#             "precipitation_sum_mm": daily.get("precipitation_sum", []),
#             "rain_probability_max_percent": daily.get("precipitation_probability_max", []),
#         },
#         "next_24h_hourly": {
#             "times": hourly.get("time", [])[:24],
#             "temp_c": hourly.get("temperature_2m", [])[:24],
#             "cloud_cover_percent": hourly.get("cloud_cover", [])[:24],
#             "precipitation_mm": hourly.get("precipitation", [])[:24],
#             "rain_probability_percent": hourly.get("precipitation_probability", [])[:24],
#             "shortwave_w_m2": hourly.get("shortwave_radiation", [])[:24],
#             "direct_w_m2": hourly.get("direct_radiation", [])[:24],
#             "diffuse_w_m2": hourly.get("diffuse_radiation", [])[:24],
#             "uv_index": hourly.get("uv_index", [])[:24],
#         },
#         "timezone": data.get("timezone"),
#     }


# # ---------- 3. Open-Meteo Air Quality ----------

# def get_air_quality(lat: float, lon: float) -> dict[str, Any]:
#     url = "https://air-quality-api.open-meteo.com/v1/air-quality"
#     params = {"latitude": lat, "longitude": lon, "current": "pm10,pm2_5,dust", "timezone": "auto"}
#     data = _get_with_retry(url, params=params)
#     current = data.get("current", {})
#     return {
#         "pm2_5_ug_m3": current.get("pm2_5"),
#         "pm10_ug_m3": current.get("pm10"),
#         "dust_ug_m3": current.get("dust"),
#     }


# # ---------- 4. Open-Meteo Marine ----------

# def get_marine_data(lat: float, lon: float) -> dict[str, Any] | None:
#     url = "https://marine-api.open-meteo.com/v1/marine"
#     params = {
#         "latitude": lat,
#         "longitude": lon,
#         "current": "wave_height,wave_direction,wave_period,sea_surface_temperature",
#         "daily": "wave_height_max,wind_wave_height_max,swell_wave_height_max",
#         "timezone": "auto",
#         "forecast_days": 3,
#     }
#     try:
#         data = _get_with_retry(url, params=params, retries=2)
#     except Exception:
#         return None
#     current = data.get("current", {})
#     daily = data.get("daily", {})
#     wave_height = current.get("wave_height")
#     if wave_height is None:
#         return None
#     return {
#         "is_coastal": True,
#         "current_wave_height_m": wave_height,
#         "current_wave_direction_deg": current.get("wave_direction"),
#         "current_wave_period_s": current.get("wave_period"),
#         "current_sea_surface_temp_c": current.get("sea_surface_temperature"),
#         "next_3_days_max_wave_height_m": daily.get("wave_height_max", []),
#         "next_3_days_max_swell_m": daily.get("swell_wave_height_max", []),
#         "note": (
#             "High waves + onshore wind can deposit salt aerosols on panels, "
#             "causing salt-residue soiling distinct from ordinary dust. "
#             "Salt requires fresh-water rinse, not just dry brushing."
#         ),
#     }


# # ---------- 5. NASA POWER ----------

# def get_nasa_power_climatology(lat: float, lon: float) -> dict[str, Any]:
#     url = "https://power.larc.nasa.gov/api/temporal/climatology/point"
#     params = {
#         "parameters": "ALLSKY_SFC_SW_DWN,T2M,CLOUD_AMT",
#         "community": "RE",
#         "longitude": lon,
#         "latitude": lat,
#         "format": "JSON",
#     }
#     data = _get_with_retry(url, params=params)
#     props = data.get("properties", {}).get("parameter", {})
#     ghi = props.get("ALLSKY_SFC_SW_DWN", {})
#     temp = props.get("T2M", {})
#     clouds = props.get("CLOUD_AMT", {})
#     return {
#         "annual_avg_ghi_kwh_m2_day": ghi.get("ANN"),
#         "annual_avg_temp_c": temp.get("ANN"),
#         "annual_avg_cloud_percent": clouds.get("ANN"),
#         "monthly_ghi_kwh_m2_day": {k: v for k, v in ghi.items() if k != "ANN"},
#         "monthly_temp_c": {k: v for k, v in temp.items() if k != "ANN"},
#     }


# # ---------- 6. Nominatim reverse geocode ----------

# def geocode_place(query: str, limit: int = 5) -> list[dict[str, Any]]:
#     """
#     Forward geocode a place name / address -> coordinate candidates (Nominatim).

#     Returns a list sorted by relevance (most relevant first). Each item:
#       {display_name, lat, lon, type, importance}
#     Empty list means "no match". Caller should confirm the resolved place with the
#     user when there is ambiguity, so we never silently run on the wrong city.

#     NOTE: Nominatim's free endpoint asks for a real User-Agent (set in _get_with_retry)
#     and ~1 request/second. That is fine here. For heavy use, switch to a self-hosted
#     Nominatim or a keyed provider (LocationIQ/Photon) — same return shape.
#     """
#     if not query or not query.strip():
#         return []
#     url = "https://nominatim.openstreetmap.org/search"
#     params = {"q": query.strip(), "format": "jsonv2", "limit": limit, "addressdetails": 1}
#     try:
#         data = _get_with_retry(url, params=params, retries=2)
#     except Exception:
#         return []
#     results: list[dict[str, Any]] = []
#     for item in data if isinstance(data, list) else []:
#         try:
#             results.append({
#                 "display_name": item.get("display_name", query),
#                 "lat": float(item["lat"]),
#                 "lon": float(item["lon"]),
#                 "type": item.get("type", ""),
#                 "importance": float(item.get("importance", 0) or 0),
#             })
#         except (KeyError, TypeError, ValueError):
#             continue
#     results.sort(key=lambda r: r["importance"], reverse=True)
#     return results


# def reverse_geocode(lat: float, lon: float) -> str:
#     try:
#         url = "https://nominatim.openstreetmap.org/reverse"
#         params = {"lat": lat, "lon": lon, "format": "json", "zoom": 10}
#         data = _get_with_retry(url, params=params, retries=2)
#         return data.get("display_name", f"{lat}, {lon}")
#     except Exception:
#         return f"{lat}, {lon}"





































"""
tools.py
--------
Wrappers around the free APIs used to gather solar-relevant data
for a given (lat, lon) coordinate pair.

APIs used:
1. PVGIS (European Commission JRC) - solar PV potential & optimal tilt
2. Open-Meteo - current weather (temperature, clouds, humidity, wind)
3. Open-Meteo Air Quality - PM2.5 / PM10 for dust soiling estimation
4. NASA POWER - long-term annual solar radiation averages
5. Nominatim (OpenStreetMap) - reverse geocode coordinates to a place name

All endpoints are FREE and require no API key.
"""

import time
from typing import Any

import requests


def _get_with_retry(url: str, params: dict | None = None, retries: int = 3, timeout: int = 20) -> dict:
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout, headers={"User-Agent": "SolarSense/1.0"})
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Request failed after {retries} attempts for {url}: {last_err}")


# ---------- 1. PVGIS ----------

def get_pvgis_data(lat: float, lon: float, peak_power_kw: float = 1.0) -> dict[str, Any]:
    url = "https://re.jrc.ec.europa.eu/api/v5_3/PVcalc"
    params = {
        "lat": lat,
        "lon": lon,
        "peakpower": peak_power_kw,
        "loss": 14,
        "outputformat": "json",
        "optimalangles": 1,
        "pvtechchoice": "crystSi",
    }
    data = _get_with_retry(url, params=params)
    inputs = data.get("inputs", {})
    outputs = data.get("outputs", {})
    monthly = outputs.get("monthly", {}).get("fixed", [])
    totals_fixed = outputs.get("totals", {}).get("fixed", {})
    mounting = inputs.get("mounting_system", {}).get("fixed", {})
    return {
        "annual_yield_kwh_per_kwp": totals_fixed.get("E_y"),
        "annual_irradiation_kwh_per_m2": totals_fixed.get("H(i)_y"),
        "system_loss_percent": totals_fixed.get("l_total"),
        "optimal_tilt_deg": mounting.get("slope", {}).get("value"),
        "optimal_azimuth_deg": mounting.get("azimuth", {}).get("value"),
        "monthly": [
            {
                "month": m.get("month"),
                "energy_kwh": m.get("E_m"),
                "irradiation_kwh_per_m2": m.get("H(i)_m"),
            }
            for m in monthly
        ],
        "elevation_m": inputs.get("location", {}).get("elevation"),
    }


# ---------- 2. Open-Meteo current weather ----------

def get_current_weather(lat: float, lon: float) -> dict[str, Any]:
    """
    Returns rich solar-relevant data in ONE Open-Meteo call (still free, no key).

    NOTE: wind_speed_unit is explicitly set to 'ms'. Open-Meteo defaults to km/h;
    the physics engine and analyst expect m/s. Without this, a light 15 km/h breeze
    was being read as a 15 m/s gale, which falsely cooled the panels in the model.
    """
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "wind_speed_unit": "ms",   # <-- critical: force m/s (default is km/h)
        "current": (
            "temperature_2m,apparent_temperature,relative_humidity_2m,dew_point_2m,"
            "cloud_cover,wind_speed_10m,wind_direction_10m,"
            "shortwave_radiation,direct_radiation,diffuse_radiation,"
            "direct_normal_irradiance,uv_index"
        ),
        "hourly": (
            "temperature_2m,cloud_cover,precipitation,precipitation_probability,"
            "shortwave_radiation,direct_radiation,diffuse_radiation,uv_index"
        ),
        "daily": (
            "temperature_2m_max,temperature_2m_min,sunrise,sunset,daylight_duration,"
            "uv_index_max,shortwave_radiation_sum,cloud_cover_mean,"
            "precipitation_sum,precipitation_probability_max"
        ),
        "timezone": "auto",
        "forecast_days": 7,
        "past_days": 1,
    }
    data = _get_with_retry(url, params=params)
    current = data.get("current", {})
    daily = data.get("daily", {})
    hourly = data.get("hourly", {})
    return {
        "current_temp_c": current.get("temperature_2m"),
        "current_apparent_temp_c": current.get("apparent_temperature"),
        "current_humidity_percent": current.get("relative_humidity_2m"),
        "current_dew_point_c": current.get("dew_point_2m"),
        "current_cloud_cover_percent": current.get("cloud_cover"),
        "current_wind_speed_m_s": current.get("wind_speed_10m"),
        "current_wind_direction_deg": current.get("wind_direction_10m"),
        "current_shortwave_radiation_w_m2": current.get("shortwave_radiation"),
        "current_direct_radiation_w_m2": current.get("direct_radiation"),
        "current_diffuse_radiation_w_m2": current.get("diffuse_radiation"),
        "current_dni_w_m2": current.get("direct_normal_irradiance"),
        "current_uv_index": current.get("uv_index"),
        "daily": {
            "dates": daily.get("time", []),
            "sunrise": daily.get("sunrise", []),
            "sunset": daily.get("sunset", []),
            "daylight_hours": [round((s or 0) / 3600, 2) for s in daily.get("daylight_duration", [])],
            "max_temp_c": daily.get("temperature_2m_max", []),
            "min_temp_c": daily.get("temperature_2m_min", []),
            "uv_index_max": daily.get("uv_index_max", []),
            "radiation_sum_mj_m2": daily.get("shortwave_radiation_sum", []),
            "avg_cloud_cover_percent": daily.get("cloud_cover_mean", []),
            "precipitation_sum_mm": daily.get("precipitation_sum", []),
            "rain_probability_max_percent": daily.get("precipitation_probability_max", []),
        },
        "next_24h_hourly": {
            "times": hourly.get("time", [])[:24],
            "temp_c": hourly.get("temperature_2m", [])[:24],
            "cloud_cover_percent": hourly.get("cloud_cover", [])[:24],
            "precipitation_mm": hourly.get("precipitation", [])[:24],
            "rain_probability_percent": hourly.get("precipitation_probability", [])[:24],
            "shortwave_w_m2": hourly.get("shortwave_radiation", [])[:24],
            "direct_w_m2": hourly.get("direct_radiation", [])[:24],
            "diffuse_w_m2": hourly.get("diffuse_radiation", [])[:24],
            "uv_index": hourly.get("uv_index", [])[:24],
        },
        "timezone": data.get("timezone"),
    }


# ---------- 3. Open-Meteo Air Quality ----------

def get_air_quality(lat: float, lon: float) -> dict[str, Any]:
    url = "https://air-quality-api.open-meteo.com/v1/air-quality"
    params = {"latitude": lat, "longitude": lon, "current": "pm10,pm2_5,dust", "timezone": "auto"}
    data = _get_with_retry(url, params=params)
    current = data.get("current", {})
    return {
        "pm2_5_ug_m3": current.get("pm2_5"),
        "pm10_ug_m3": current.get("pm10"),
        "dust_ug_m3": current.get("dust"),
    }


# ---------- 4. Open-Meteo Marine ----------

def get_marine_data(lat: float, lon: float) -> dict[str, Any] | None:
    url = "https://marine-api.open-meteo.com/v1/marine"
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "wave_height,wave_direction,wave_period,sea_surface_temperature",
        "daily": "wave_height_max,wind_wave_height_max,swell_wave_height_max",
        "timezone": "auto",
        "forecast_days": 3,
    }
    try:
        data = _get_with_retry(url, params=params, retries=2)
    except Exception:
        return None
    current = data.get("current", {})
    daily = data.get("daily", {})
    wave_height = current.get("wave_height")
    if wave_height is None:
        return None
    return {
        "is_coastal": True,
        "current_wave_height_m": wave_height,
        "current_wave_direction_deg": current.get("wave_direction"),
        "current_wave_period_s": current.get("wave_period"),
        "current_sea_surface_temp_c": current.get("sea_surface_temperature"),
        "next_3_days_max_wave_height_m": daily.get("wave_height_max", []),
        "next_3_days_max_swell_m": daily.get("swell_wave_height_max", []),
        "note": (
            "High waves + onshore wind can deposit salt aerosols on panels, "
            "causing salt-residue soiling distinct from ordinary dust. "
            "Salt requires fresh-water rinse, not just dry brushing."
        ),
    }


# ---------- 5. NASA POWER ----------

def get_nasa_power_climatology(lat: float, lon: float) -> dict[str, Any]:
    url = "https://power.larc.nasa.gov/api/temporal/climatology/point"
    params = {
        "parameters": "ALLSKY_SFC_SW_DWN,T2M,CLOUD_AMT",
        "community": "RE",
        "longitude": lon,
        "latitude": lat,
        "format": "JSON",
    }
    data = _get_with_retry(url, params=params)
    props = data.get("properties", {}).get("parameter", {})
    ghi = props.get("ALLSKY_SFC_SW_DWN", {})
    temp = props.get("T2M", {})
    clouds = props.get("CLOUD_AMT", {})
    return {
        "annual_avg_ghi_kwh_m2_day": ghi.get("ANN"),
        "annual_avg_temp_c": temp.get("ANN"),
        "annual_avg_cloud_percent": clouds.get("ANN"),
        "monthly_ghi_kwh_m2_day": {k: v for k, v in ghi.items() if k != "ANN"},
        "monthly_temp_c": {k: v for k, v in temp.items() if k != "ANN"},
    }


# ---------- 6. Nominatim reverse geocode ----------

def geocode_place(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """
    Forward geocode a place name / address -> coordinate candidates (Nominatim).

    Returns a list sorted by relevance (most relevant first). Each item:
      {display_name, lat, lon, type, importance}
    Empty list means "no match". Caller should confirm the resolved place with the
    user when there is ambiguity, so we never silently run on the wrong city.

    NOTE: Nominatim's free endpoint asks for a real User-Agent (set in _get_with_retry)
    and ~1 request/second. That is fine here. For heavy use, switch to a self-hosted
    Nominatim or a keyed provider (LocationIQ/Photon) — same return shape.
    """
    if not query or not query.strip():
        return []
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": query.strip(), "format": "jsonv2", "limit": limit, "addressdetails": 1}
    data = _get_with_retry(url, params=params, retries=2)   # network errors propagate
    results: list[dict[str, Any]] = []
    for item in data if isinstance(data, list) else []:
        try:
            results.append({
                "display_name": item.get("display_name", query),
                "lat": float(item["lat"]),
                "lon": float(item["lon"]),
                "type": item.get("type", ""),
                "importance": float(item.get("importance", 0) or 0),
            })
        except (KeyError, TypeError, ValueError):
            continue
    results.sort(key=lambda r: r["importance"], reverse=True)
    return results


def reverse_geocode(lat: float, lon: float) -> str:
    try:
        url = "https://nominatim.openstreetmap.org/reverse"
        params = {"lat": lat, "lon": lon, "format": "json", "zoom": 10}
        data = _get_with_retry(url, params=params, retries=2)
        return data.get("display_name", f"{lat}, {lon}")
    except Exception:
        return f"{lat}, {lon}"
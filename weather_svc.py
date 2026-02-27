# =========================================================================
#  Weather Service - OpenWeather API integration for BMO face display
#  Fetches weather and returns appropriate idle face image name
# =========================================================================

import os
import json
import time
import threading
import datetime
import requests
from dotenv import load_dotenv

load_dotenv()

CONFIG_FILE = "config.json"
DEFAULT_LAT = "24.801"
DEFAULT_LON = "120.971"
UPDATE_INTERVAL = 3600  # seconds (1 hour)

# Thread-safe storage
_lock = threading.Lock()
_current_main_weather = "Clear"
_current_wind_speed = 0.0
_current_temp = None
_current_idle_image = "idle_default.png"
_last_update = 0.0


def _load_config():
    """Load API key from .env (dotenv) or environment. Lat/lon from .env or config.json."""
    api_key = os.environ.get("openweather_api_key") or os.environ.get("OPENWEATHER_API_KEY")
    lat = os.environ.get("weather_lat") or os.environ.get("WEATHER_LAT") or DEFAULT_LAT
    lon = os.environ.get("weather_lon") or os.environ.get("WEATHER_LON") or DEFAULT_LON

    if not api_key and os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                cfg = json.load(f)
            api_key = api_key or cfg.get("openweather_api_key", "")
            lat = cfg.get("weather_lat", lat)
            lon = cfg.get("weather_lon", lon)
        except Exception as e:
            print(f"[weather_svc] Config load error: {e}")
    return api_key or "", lat, lon


def _weather_to_idle_image(main_weather: str, wind_speed: float) -> str:
    """Map weather to idle face image filename."""
    # 九降風: wind > 8 m/s
    if wind_speed > 8.0:
        return "idle_windy.png"
    if main_weather == "Clear":
        return "idle_sunny.png"
    if main_weather in ["Rain", "Drizzle", "Thunderstorm"]:
        return "idle_rainy.png"
    if main_weather == "Clouds":
        return "idle_cloudy.png"
    return "idle_default.png"


def fetch_weather() -> bool:
    """Fetch weather from OpenWeather API. Returns True if successful."""
    global _current_main_weather, _current_wind_speed, _current_idle_image, _last_update

    api_key, lat, lon = _load_config()
    if not api_key or api_key == "你的_OPENWEATHER_API_KEY":
        return False

    # Try One Call API 3.0 first, fallback to Current Weather 2.5 (free tier)
    endpoints = [
        (f"https://api.openweathermap.org/data/3.0/onecall?lat={lat}&lon={lon}&exclude=minutely,hourly,daily,alerts&appid={api_key}&units=metric", "current"),
        (f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={api_key}&units=metric", None),
    ]
    data = None
    last_err = None
    for url, data_key in endpoints:
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data_key:
                data = data.get(data_key, data)
            break
        except requests.exceptions.HTTPError as e:
            last_err = e
            if e.response.status_code == 401:
                continue  # try next endpoint
            raise
        except Exception as e:
            last_err = e
            continue

    if data is None:
        print(f"[weather_svc] Fetch failed: {last_err}", flush=True)
        return False

    try:
        main_weather = data.get("weather", [{}])[0].get("main", "Clear")
        wind_speed = float(data.get("wind", {}).get("speed", 0))
        temp = data.get("temp") if "temp" in data else data.get("main", {}).get("temp")

        with _lock:
            _current_main_weather = main_weather
            _current_wind_speed = wind_speed
            _current_temp = temp
            _current_idle_image = _weather_to_idle_image(main_weather, wind_speed)
            _last_update = time.time()

        print(f"[weather_svc] {main_weather}, wind={wind_speed}m/s -> {_current_idle_image}", flush=True)
        return True
    except Exception as e:
        print(f"[weather_svc] Fetch failed: {e}", flush=True)
        return False


def get_current_idle_image() -> str:
    """Return current weather-based idle image filename. Thread-safe."""
    with _lock:
        return _current_idle_image


def get_weather_info() -> dict:
    """Return current weather info for display or agent use."""
    with _lock:
        return {
            "main": _current_main_weather,
            "wind_speed": _current_wind_speed,
            "temp": _current_temp,
            "idle_image": _current_idle_image,
            "last_update": _last_update,
        }


def _seconds_until_next_hour():
    """Return seconds until the next whole hour (整點)."""
    now = datetime.datetime.now()
    next_hour = (now + datetime.timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    return (next_hour - now).total_seconds()


def _background_loop():
    """Background thread: fetch weather at the top of each hour (整點)."""
    while True:
        fetch_weather()
        sleep_secs = _seconds_until_next_hour()
        next_time = datetime.datetime.now() + datetime.timedelta(seconds=sleep_secs)
        print(f"[weather_svc] Next update at {next_time.strftime('%H:%M')}", flush=True)
        time.sleep(sleep_secs)


def start_background_thread():
    """Start background weather update thread (daemon)."""
    t = threading.Thread(target=_background_loop, daemon=True)
    t.start()
    print("[weather_svc] Background thread started.", flush=True)


def init():
    """Initial fetch (blocking). Call before start_background_thread if you want immediate data."""
    fetch_weather()


if __name__ == "__main__":
    # Quick test: cd be-more-agent && python weather_svc.py
    print("=== weather_svc test ===")
    ok = fetch_weather()
    if ok:
        info = get_weather_info()
        print(f"main: {info['main']}, temp: {info['temp']}°C, wind: {info['wind_speed']} m/s")
        print(f"idle_image: {get_current_idle_image()}")
    else:
        print("Fetch failed. Check openweather_api_key in config.json")

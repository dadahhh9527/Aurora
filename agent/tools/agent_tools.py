import json
import os
import random
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from langchain_core.tools import tool

from rag.rag_service import RagSummarizeService
from utils.config_handler import agent_conf
from utils.logger_handler import logger
from utils.path_tool import get_abs_path

_rag = None


def _get_rag() -> RagSummarizeService:
    # Lazy singleton so importing this module doesn't immediately connect to the vector store.
    global _rag
    if _rag is None:
        _rag = RagSummarizeService()
    return _rag


user_ids = ["1001", "1002", "1003", "1004", "1005", "1006", "1007", "1008", "1009", "1010"]
month_arr = ["2025-01", "2025-02", "2025-03", "2025-04", "2025-05", "2025-06",
             "2025-07", "2025-08", "2025-09", "2025-10", "2025-11", "2025-12"]
external_data = {}

# ---------------------------------------------------------------------------
# Weather & geolocation via international, key-portable services:
#   - Weather:   OpenWeatherMap (needs OPENWEATHER_API_KEY)
#   - Location:  ip-api.com (free, no key required)
# ---------------------------------------------------------------------------
OPENWEATHER_API_KEY = (os.environ.get("OPENWEATHER_API_KEY") or "").strip()
OPENWEATHER_BASE_URL = agent_conf.get("openweather_base_url", "https://api.openweathermap.org")
WEATHER_TIMEOUT = float(agent_conf.get("weather_timeout", 5))
LOCATION_TIMEOUT = float(agent_conf.get("location_timeout", 5))


def _http_get_json(url: str, timeout: float) -> dict:
    try:
        with urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        raise RuntimeError(f"HTTP error: {e.code}") from e
    except URLError as e:
        raise RuntimeError(f"Network error: {e.reason}") from e
    except Exception as e:
        raise RuntimeError(f"Request failed: {str(e)}") from e


@tool(description="Get the current weather for a given city, returned as a plain-text string.")
def get_weather(city: str) -> str:
    if not city or not city.strip():
        return "No city was provided, unable to look up the weather."

    if not OPENWEATHER_API_KEY:
        return "Weather service is not configured (missing OPENWEATHER_API_KEY)."

    try:
        params = urlencode({
            "q": city.strip(),
            "appid": OPENWEATHER_API_KEY,
            "units": "metric",
            "lang": "en",
        })
        data = _http_get_json(f"{OPENWEATHER_BASE_URL}/data/2.5/weather?{params}", WEATHER_TIMEOUT)

        if str(data.get("cod")) != "200":
            return f"Could not get the weather for {city}: {data.get('message', 'unknown error')}"

        description = data["weather"][0]["description"]
        main = data.get("main", {})
        wind = data.get("wind", {})
        return (
            f"Weather in {data.get('name', city)}: {description}, "
            f"temperature {main.get('temp')}°C (feels like {main.get('feels_like')}°C), "
            f"humidity {main.get('humidity')}%, wind {wind.get('speed')} m/s."
        )
    except Exception as e:
        logger.error(f"[get_weather] lookup failed city={city} err={str(e)}")
        return f"Failed to fetch the weather for {city}, please try again later."


@tool(description="Get the city where the current user is located, returned as a plain-text string.")
def get_user_location() -> str:
    try:
        data = _http_get_json(
            "http://ip-api.com/json/?fields=status,message,city,regionName,country",
            LOCATION_TIMEOUT,
        )
        if data.get("status") != "success":
            logger.warning(f"[get_user_location] geolocation failed: {data.get('message')}")
            return "Unknown city"

        return data.get("city") or data.get("regionName") or "Unknown city"
    except Exception as e:
        logger.error(f"[get_user_location] failed err={str(e)}")
        return "Unknown city"


@tool(description="Retrieve reference material from the knowledge base for a given query.")
def rag_summarize(query: str) -> str:
    return _get_rag().rag_summarize(query)


@tool(description="Get the current user's ID as a plain-text string.")
def get_user_id() -> str:
    return random.choice(user_ids)


@tool(description="Get the current month as a plain-text string in YYYY-MM format.")
def get_current_month() -> str:
    return random.choice(month_arr)


def generate_external_data():
    """
    Load per-user monthly usage records into memory:
    {
        "<user_id>": {
            "<month>": {"profile": ..., "cleaning_efficiency": ..., "consumables": ..., "comparison": ...},
            ...
        },
        ...
    }
    """
    if external_data:
        return

    external_data_path = get_abs_path(agent_conf["external_data_path"])
    if not os.path.exists(external_data_path):
        raise FileNotFoundError(f"External data file not found: {external_data_path}")

    with open(external_data_path, "r", encoding="utf-8") as f:
        for line in f.readlines()[1:]:
            arr = [c.replace('"', "") for c in line.strip().split(",")]
            if len(arr) < 6:
                continue

            user_id, profile, efficiency, consumables, comparison, month = arr[:6]

            external_data.setdefault(user_id, {})
            external_data[user_id][month] = {
                "profile": profile,
                "cleaning_efficiency": efficiency,
                "consumables": consumables,
                "comparison": comparison,
            }


@tool(description="Fetch a user's usage record for a given month. Returns an empty string if not found.")
def fetch_external_data(user_id: str, month: str) -> str:
    generate_external_data()

    try:
        record = external_data[user_id][month]
        return json.dumps(record, ensure_ascii=False)
    except KeyError:
        logger.warning(f"[fetch_external_data] no usage record for user={user_id} month={month}")
        return ""


@tool(description="No input, no return value. Calling this triggers the middleware to switch to the "
                  "report-generation prompt for subsequent turns.")
def fill_context_for_report():
    return "fill_context_for_report called"

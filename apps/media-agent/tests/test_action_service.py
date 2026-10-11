import httpx
import respx

from app.actions import registry
from app.actions.action_service import execute_action_payload
from app.core.action_catalog import ACTION_BY_NAME, ROUTER_ACTION_NAMES
from app.core.config import get_settings
from app.router.parser import ROUTER_SCHEMA


@respx.mock
def test_action_service_executes_search_without_route_dispatch() -> None:
    respx.get("http://sonarr.test/son/api/v3/series/lookup?term=sample").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "title": "Sample Show",
                    "year": 2020,
                    "overview": "A show.",
                    "tvdbId": 111,
                }
            ],
        )
    )
    respx.get("http://sonarr.test/son/api/v3/series").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.get("http://radarr.test/rad/api/v3/movie").mock(
        return_value=httpx.Response(200, json=[])
    )

    with httpx.Client() as client:
        result = execute_action_payload(
            client,
            get_settings(),
            {"action": "search", "type": "tv", "query": "sample"},
        )

    assert result["ok"] is True
    assert result["type"] == "tv"
    assert result["results"][0]["title"] == "Sample Show"


def test_action_catalog_drives_router_schema() -> None:
    assert ACTION_BY_NAME["indexer_grab"].model_name == "ActionIndexerGrab"
    enum = ROUTER_SCHEMA["properties"]["action"]["enum"]
    assert set(enum) == set(ROUTER_ACTION_NAMES)
    assert tuple(enum) == registry.router_emittable_names()

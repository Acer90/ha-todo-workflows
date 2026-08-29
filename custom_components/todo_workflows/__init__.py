from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace.const import LOVELACE_DATA, MODE_STORAGE
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.loader import async_get_integration

from .const import (
    ATTR_BADGE,
    ATTR_CLEANUP_HOURS,
    ATTR_COMPLETED_AT,
    ATTR_COLOR,
    ATTR_DESCRIPTION,
    ATTR_DUE,
    ATTR_ICON,
    ATTR_ICON_BACKGROUND_COLOR,
    ATTR_ICON_COLOR,
    ATTR_IDENT,
    ATTR_PERSISTENT,
    ATTR_PRIORITY,
    ATTR_RESOLVED_TEXT,
    ATTR_SECOND_COLOR,
    ATTR_TEXT_COLOR,
    ATTR_TITLE,
    DEFAULT_TODO_ENTITY_ID,
    DOMAIN,
    SERVICE_COMPLETE_ITEM,
    SERVICE_COMPLETE_ITEM_V2,
    SERVICE_RELOAD,
    SERVICE_UPSERT_ITEM,
)

_LOGGER = logging.getLogger(__name__)

DATA_SERVICES_REGISTERED = f"{DOMAIN}_services_registered"
DATA_FRONTEND_REGISTERED = f"{DOMAIN}_frontend_registered"
DATA_LOVELACE_RESOURCE_REGISTERED = f"{DOMAIN}_lovelace_resource_registered"
CARD_URL = "/todo_workflows_frontend/todo-workflows-card.js"
PLATFORMS = ("todo",)

SERVICE_UPSERT_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_IDENT): cv.string,
        vol.Required(ATTR_TITLE): cv.string,
        vol.Optional(ATTR_DESCRIPTION, default=""): cv.string,
        vol.Optional(ATTR_BADGE, default=""): cv.string,
        vol.Optional(ATTR_DUE, default=""): cv.string,
        vol.Optional(ATTR_PRIORITY, default=0): vol.Coerce(int),
        vol.Optional(ATTR_ICON, default=""): cv.string,
        vol.Optional(ATTR_COLOR, default=""): cv.string,
        vol.Optional(ATTR_SECOND_COLOR, default=""): cv.string,
        vol.Optional(ATTR_ICON_BACKGROUND_COLOR, default=""): cv.string,
        vol.Optional(ATTR_ICON_COLOR, default=""): cv.string,
        vol.Optional(ATTR_TEXT_COLOR, default=""): cv.string,
        vol.Optional(ATTR_PERSISTENT, default=False): cv.boolean,
        vol.Optional(ATTR_RESOLVED_TEXT, default=""): cv.string,
        vol.Optional(ATTR_CLEANUP_HOURS): vol.Coerce(int),
        vol.Optional("item_id"): cv.string,
        vol.Optional("uid"): cv.string,
    }
)

SERVICE_COMPLETE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_IDENT): cv.string,
        vol.Optional(ATTR_TITLE): cv.string,
        vol.Optional(ATTR_PERSISTENT): cv.boolean,
        vol.Optional("item_id"): cv.string,
        vol.Optional("uid"): cv.string,
    }
)

WS_LIST_ITEMS = {
    vol.Required("type"): "todo_workflows/list_items",
}
WS_SUBSCRIBE_ITEMS = {
    vol.Required("type"): "todo_workflows/subscribe_items",
}


def _extract_item_id(item: dict[str, Any]) -> str | None:
    for key in ("uid", "id", "item_id"):
        value = item.get(key)
        if value:
            return str(value)
    return None


def _extract_item_title(item: dict[str, Any]) -> str:
    for key in ("summary", "title", "item", "name"):
        value = item.get(key)
        if value:
            return str(value)
    return ""


def _normalize_lookup_key(value: Any) -> str:
    if value is None:
        return ""
    normalized = unicodedata.normalize("NFKC", str(value))
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized.casefold()


def _extract_item_description(item: dict[str, Any]) -> str:
    value = item.get("description")
    if value is None:
        return ""
    if isinstance(value, dict):
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return ""
    return str(value)


def _parse_description_json(description: str) -> dict[str, Any] | None:
    if not description:
        return None
    try:
        data = json.loads(description)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", description)
        if match:
            try:
                data = json.loads(match.group(0))
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                return None
    return None


def _item_lookup_keys(item: dict[str, Any]) -> set[str]:
    description = _extract_item_description(item)
    data = _parse_description_json(description) or {}
    candidates = {
        _normalize_lookup_key(data.get("ident")),
        _normalize_lookup_key(data.get("title")),
        _normalize_lookup_key(_extract_item_title(item)),
    }
    candidates.discard("")
    return candidates


def _is_item_completed(item: dict[str, Any]) -> bool:
    status = str(item.get("status") or "").strip().casefold()
    if status in {"completed", "complete", "done"}:
        return True
    if status in {"needs_action", "needs-action", "open", "todo", "pending"}:
        return False

    parsed = _parse_description_json(_extract_item_description(item)) or {}
    if parsed.get("completed_at"):
        return True

    parsed_status = str(parsed.get("status") or "").strip().casefold()
    return parsed_status in {"completed", "complete", "done"}


def _find_item_by_ident_in_states(
    hass: HomeAssistant, entity_id: str, ident: str, title: str | None = None
) -> dict[str, Any] | None:
    lookup_ident = _normalize_lookup_key(ident)
    lookup_title = _normalize_lookup_key(title)
    if not lookup_ident and not lookup_title:
        return None

    todo_slug = entity_id.split(".", 1)[1] if "." in entity_id else entity_id
    todo_name = (
        _normalize_lookup_key(hass.states.get(entity_id).attributes.get("friendly_name"))
        if hass.states.get(entity_id)
        else ""
    )

    candidate_states = [state for state in hass.states.async_all() if state.entity_id == entity_id]
    for state in hass.states.async_all():
        if state.entity_id == entity_id:
            continue
        if not state.entity_id.startswith("sensor."):
            continue
        tasks = state.attributes.get("tasks") or state.attributes.get("items")
        if not isinstance(tasks, list):
            continue
        state_name = _normalize_lookup_key(state.attributes.get("friendly_name"))
        if state.entity_id in {
            f"sensor.{todo_slug}_eintrage",
            f"sensor.{todo_slug}_entries",
            f"sensor.{todo_slug}_items",
        }:
            candidate_states.append(state)
            continue
        if todo_name and state_name and todo_name in state_name:
            candidate_states.append(state)

    for state in candidate_states:
        tasks = state.attributes.get("tasks") or state.attributes.get("items")
        if not isinstance(tasks, list):
            continue
        for task in tasks:
            keys = _item_lookup_keys(task)
            if lookup_ident and lookup_ident in keys:
                return task
            if lookup_title and lookup_title in keys:
                return task
    return None


def _find_item_by_ident_in_entity(
    hass: HomeAssistant, entity_id: str, ident: str, title: str | None = None
) -> dict[str, Any] | None:
    """Find an item from a registered Todo entity without a service call."""
    lookup_ident = _normalize_lookup_key(ident)
    lookup_title = _normalize_lookup_key(title)
    if not lookup_ident and not lookup_title:
        return None

    todo_component = hass.data.get("todo")
    entity = todo_component.get_entity(entity_id) if todo_component else None
    for todo_item in getattr(entity, "todo_items", None) or []:
        item = {
            "uid": todo_item.uid,
            "summary": todo_item.summary,
            "status": todo_item.status.value if todo_item.status else None,
            "description": todo_item.description,
        }
        keys = _item_lookup_keys(item)
        if lookup_ident and lookup_ident in keys:
            return item
        if lookup_title and lookup_title in keys:
            return item
    return None


async def _call_todo_service(
    hass: HomeAssistant,
    service: str,
    data: dict[str, Any],
    return_response: bool = False,
    target_entity_id: str | None = None,
) -> dict[str, Any] | None:
    target = None
    entity_id = data.get(ATTR_ENTITY_ID)
    if target_entity_id:
        target = {ATTR_ENTITY_ID: target_entity_id}
    elif entity_id:
        target = {ATTR_ENTITY_ID: entity_id}
    try:
        _LOGGER.debug(
            "Calling todo.%s with data=%s target=%s return_response=%s",
            service,
            data,
            target,
            return_response,
        )
        response = await hass.services.async_call(
            "todo",
            service,
            data,
            blocking=True,
            target=target,
            return_response=return_response,
        )
        if return_response:
            return response or {}
    except TypeError:
        await hass.services.async_call(
            "todo",
            service,
            data,
            blocking=True,
            target=target,
        )
    return None


def _extract_tasks_from_state(state) -> list[dict[str, Any]]:
    if not state:
        return []
    tasks = state.attributes.get("tasks") or state.attributes.get("items")
    if isinstance(tasks, dict):
        tasks = tasks.get("items")
    if not isinstance(tasks, list):
        return []
    return tasks


def _get_items_from_entity(hass: HomeAssistant, entity_id: str) -> list[dict[str, Any]] | None:
    """Read items directly from a registered Todo entity when available."""
    todo_component = hass.data.get("todo")
    entity = todo_component.get_entity(entity_id) if todo_component else None
    todo_items = getattr(entity, "todo_items", None)
    if todo_items is None:
        return None
    return [
        {
            "uid": item.uid,
            "summary": item.summary,
            "status": item.status.value if item.status else None,
            "description": item.description,
        }
        for item in todo_items
    ]


async def _get_items(
    hass: HomeAssistant, entity_id: str, items_entity: str | None = None
) -> list[dict[str, Any]]:
    if entity_id == DEFAULT_TODO_ENTITY_ID:
        items = _get_items_from_entity(hass, entity_id)
        if items is not None:
            return items

    if not hass.states.get(entity_id):
        _LOGGER.debug("Todo-Speicher ist noch nicht verfuegbar: %s", entity_id)
        return []

    response = await _call_todo_service(
        hass,
        "get_items",
        {ATTR_ENTITY_ID: entity_id},
        return_response=True,
        target_entity_id=entity_id,
    )
    if response:
        entity_response = response.get(entity_id)
        if isinstance(entity_response, dict) and isinstance(
            entity_response.get("items"), list
        ):
            return entity_response["items"]
        if isinstance(response.get("items"), list):
            return response["items"]
        if isinstance(response.get("response"), dict) and isinstance(
            response["response"].get("items"), list
        ):
            return response["response"]["items"]
        if isinstance(response.get("result"), dict) and isinstance(
            response["result"].get("items"), list
        ):
            return response["result"]["items"]
    state = hass.states.get(entity_id)
    items = _extract_tasks_from_state(state)
    if items:
        return items
    if items_entity:
        items = _extract_tasks_from_state(hass.states.get(items_entity))
        if items:
            return items
    return []


def _build_description_json(data: dict[str, Any]) -> str:
    payload = {
        "title": str(data.get(ATTR_TITLE) or "").strip(),
        "ident": str(data.get(ATTR_IDENT) or data.get(ATTR_TITLE) or "").strip(),
        "description": data.get(ATTR_DESCRIPTION, ""),
        "badge": data.get(ATTR_BADGE, ""),
        "due": data.get(ATTR_DUE, ""),
        "priority": data.get(ATTR_PRIORITY, 0),
        "icon": data.get(ATTR_ICON, ""),
        "color": data.get(ATTR_COLOR, ""),
        "second_color": data.get(ATTR_SECOND_COLOR, ""),
        "icon_background_color": data.get(ATTR_ICON_BACKGROUND_COLOR, ""),
        "icon_color": data.get(ATTR_ICON_COLOR, ""),
        "text_color": data.get(ATTR_TEXT_COLOR, ""),
        "persistent": data.get(ATTR_PERSISTENT, False),
        "resolved_text": data.get(ATTR_RESOLVED_TEXT, ""),
        "cleanup_hours": data.get(ATTR_CLEANUP_HOURS, 0),
        "completed_at": data.get(ATTR_COMPLETED_AT, ""),
    }
    return json.dumps(payload, ensure_ascii=False)


def _should_cleanup(parsed: dict[str, Any]) -> bool:
    try:
        hours = int(parsed.get("cleanup_hours", 0))
    except (TypeError, ValueError):
        return False
    if hours <= 0:
        return False
    completed_at = parsed.get("completed_at")
    if not completed_at:
        return False
    try:
        completed_dt = datetime.fromisoformat(completed_at)
    except ValueError:
        return False
    if completed_dt.tzinfo is None:
        completed_dt = completed_dt.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - completed_dt >= timedelta(hours=hours)


def _due_fields(due: str) -> dict[str, str]:
    if not due:
        return {}
    if "T" in due:
        return {"due_datetime": due}
    return {"due_date": due}


async def _find_item_by_ident(
    hass: HomeAssistant, entity_id: str, ident: str
) -> dict[str, Any] | None:
    lookup_ident = _normalize_lookup_key(ident)
    if not lookup_ident:
        return None
    for item in await _get_items(hass, entity_id):
        if lookup_ident in _item_lookup_keys(item):
            return item
    return None


async def _find_item_by_id(
    hass: HomeAssistant, entity_id: str, item_id: str
) -> dict[str, Any] | None:
    for item in await _get_items(hass, entity_id):
        current_item_id = _extract_item_id(item)
        if current_item_id and current_item_id == str(item_id):
            return item
    return None


async def _find_item_by_title(
    hass: HomeAssistant, entity_id: str, title: str
) -> dict[str, Any] | None:
    lookup_title = _normalize_lookup_key(title)
    if not lookup_title:
        return None
    for item in await _get_items(hass, entity_id):
        if lookup_title in _item_lookup_keys(item):
            return item
    return None


async def _handle_upsert(call: ServiceCall) -> None:
    hass = call.hass
    data = dict(call.data)
    entity_id = DEFAULT_TODO_ENTITY_ID
    title = data[ATTR_TITLE]
    ident = _normalize_lookup_key(data.get(ATTR_IDENT) or title)
    data[ATTR_IDENT] = ident
    if ATTR_CLEANUP_HOURS not in data:
        default_hours = hass.data.get(DOMAIN, {}).get(ATTR_CLEANUP_HOURS, 0)
        data[ATTR_CLEANUP_HOURS] = default_hours
    description = _build_description_json(data)
    existing = await _find_item_by_ident(hass, entity_id, ident)
    if not existing:
        existing = await _find_item_by_title(hass, entity_id, title)
    if not existing:
        existing = _find_item_by_ident_in_states(hass, entity_id, ident, title)

    service_data = {
        ATTR_ENTITY_ID: entity_id,
        "item": title,
        "description": description,
        **_due_fields(data.get(ATTR_DUE, "")),
    }

    if existing:
        item_id = _extract_item_id(existing)
        if item_id:
            await _call_todo_service(
                hass,
                "update_item",
                {**service_data, "item_id": item_id, "status": "needs_action"},
            )
        else:
            existing_title = _extract_item_title(existing)
            if not existing_title:
                _LOGGER.warning("Todo item found but no item_id/title: %s", existing)
                return
            await _call_todo_service(
                hass,
                "update_item",
                {
                    ATTR_ENTITY_ID: entity_id,
                    "item": existing_title,
                    "rename": title,
                    "description": description,
                    "status": "needs_action",
                    **_due_fields(data.get(ATTR_DUE, "")),
                },
            )
        await _publish_items(hass)
        return

    await _call_todo_service(hass, "add_item", service_data)
    await _publish_items(hass)


async def _handle_complete(call: ServiceCall) -> None:
    hass = call.hass
    data = call.data
    entity_id = DEFAULT_TODO_ENTITY_ID
    title_hint = data.get(ATTR_TITLE)
    ident = str(data.get(ATTR_IDENT) or title_hint or "").strip()
    _LOGGER.debug("complete_item called with data=%s", data)
    if not ident:
        _LOGGER.warning("complete_item missing title/ident: %s", data)
        return
    persistent_override = data.get(ATTR_PERSISTENT)
    item_id = data.get("item_id")

    item = None
    parsed = {}
    item_title = None
    if item_id:
        item = await _find_item_by_id(hass, entity_id, str(item_id))
        if not item:
            _LOGGER.debug("Todo item with item_id '%s' not found, falling back", item_id)

    if not item:
        item = await _find_item_by_ident(hass, entity_id, ident)
    if not item and title_hint:
        item = await _find_item_by_title(hass, entity_id, str(title_hint))
    if not item:
        if title_hint:
            persistent = bool(persistent_override)
            if persistent:
                description = _build_description_json(
                    {
                        ATTR_TITLE: str(title_hint),
                        ATTR_IDENT: ident,
                        ATTR_DESCRIPTION: data.get(ATTR_DESCRIPTION, ""),
                        ATTR_BADGE: data.get(ATTR_BADGE, ""),
                        ATTR_DUE: data.get(ATTR_DUE, ""),
                        ATTR_PRIORITY: data.get(ATTR_PRIORITY, 0),
                        ATTR_ICON: data.get(ATTR_ICON, ""),
                        ATTR_COLOR: data.get(ATTR_COLOR, ""),
                        ATTR_SECOND_COLOR: data.get(ATTR_SECOND_COLOR, ""),
                        ATTR_ICON_BACKGROUND_COLOR: data.get(ATTR_ICON_BACKGROUND_COLOR, ""),
                        ATTR_ICON_COLOR: data.get(ATTR_ICON_COLOR, ""),
                        ATTR_TEXT_COLOR: data.get(ATTR_TEXT_COLOR, ""),
                        ATTR_PERSISTENT: True,
                        ATTR_RESOLVED_TEXT: data.get(ATTR_RESOLVED_TEXT, ""),
                        ATTR_CLEANUP_HOURS: data.get(ATTR_CLEANUP_HOURS, 0),
                        ATTR_COMPLETED_AT: datetime.now(timezone.utc).isoformat(),
                    }
                )
                await _call_todo_service(
                    hass,
                    "update_item",
                    {
                        ATTR_ENTITY_ID: entity_id,
                        "item": str(title_hint),
                        "status": "completed",
                        "description": description,
                    },
                    target_entity_id=entity_id,
                )
                await _publish_items(hass)
            else:
                await _call_todo_service(
                    hass,
                    "remove_item",
                    {ATTR_ENTITY_ID: entity_id, "item": str(title_hint)},
                    target_entity_id=entity_id,
                )
                await _publish_items(hass)
            return
        if item_id:
            _LOGGER.warning(
                "Todo item not found for item_id '%s' or ident '%s'", item_id, ident
            )
        else:
            _LOGGER.warning("Todo item with ident '%s' not found", ident)
        return

    item_id = _extract_item_id(item)
    description = _extract_item_description(item)
    item_title = _extract_item_title(item)
    _LOGGER.debug("Found item for ident '%s': %s", ident, item)
    parsed = _parse_description_json(description) or {}
    persistent = (
        persistent_override
        if persistent_override is not None
        else bool(parsed.get("persistent", False))
    )

    if not item_title:
        _LOGGER.warning("Todo item found but no title: %s", item)
        return

    if persistent:
        if not parsed.get("completed_at"):
            parsed["completed_at"] = datetime.now(timezone.utc).isoformat()
        description = _build_description_json(
            {
                ATTR_TITLE: item_title,
                ATTR_IDENT: ident,
                ATTR_DESCRIPTION: parsed.get("description", ""),
                ATTR_BADGE: parsed.get("badge", ""),
                ATTR_DUE: parsed.get("due", ""),
                ATTR_PRIORITY: parsed.get("priority", 0),
                ATTR_ICON: parsed.get("icon", ""),
                ATTR_COLOR: parsed.get("color", ""),
                ATTR_SECOND_COLOR: parsed.get("second_color", ""),
                ATTR_ICON_BACKGROUND_COLOR: parsed.get("icon_background_color", ""),
                ATTR_ICON_COLOR: parsed.get("icon_color", ""),
                ATTR_TEXT_COLOR: parsed.get("text_color", ""),
                ATTR_PERSISTENT: True,
                ATTR_RESOLVED_TEXT: parsed.get("resolved_text", ""),
                ATTR_CLEANUP_HOURS: parsed.get("cleanup_hours", 0),
                ATTR_COMPLETED_AT: parsed.get("completed_at", ""),
            }
        )
        await _call_todo_service(
            hass,
            "update_item",
            {
                ATTR_ENTITY_ID: entity_id,
                "item": item_title,
                "status": "completed",
                "description": description,
            },
            target_entity_id=entity_id,
        )
    else:
        await _call_todo_service(
            hass,
            "remove_item",
            {ATTR_ENTITY_ID: entity_id, "item": item_title},
            target_entity_id=entity_id,
        )
    await _publish_items(hass)


async def _cleanup_completed_items(
    hass: HomeAssistant, entity_id: str, items: list[dict[str, Any]]
) -> None:
    for item in items:
        if item.get("status") != "completed":
            continue
        description = _extract_item_description(item)
        parsed = _parse_description_json(description) or {}
        if not parsed.get("persistent", False):
            continue
        if not _should_cleanup(parsed):
            continue
        title = _extract_item_title(item)
        if not title:
            continue
        _LOGGER.debug("Auto-cleanup completed item: %s", title)
        await _call_todo_service(
            hass,
            "remove_item",
            {ATTR_ENTITY_ID: entity_id, "item": title},
            target_entity_id=entity_id,
        )


def _normalize_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert stored Todo items to the card's public data structure."""
    normalized = []
    for item in items:
        description = _extract_item_description(item)
        data = _parse_description_json(description) or {}
        normalized.append(
            {
                "id": _extract_item_id(item),
                "title": data.get("title") or _extract_item_title(item),
                "status": item.get("status"),
                "description": data.get("description", ""),
                "badge": data.get("badge", ""),
                "due": data.get("due", ""),
                "priority": data.get("priority", 0),
                "icon": data.get("icon", ""),
                "color": data.get("color", ""),
                "second_color": data.get("second_color", ""),
                "icon_background_color": data.get("icon_background_color", ""),
                "icon_color": data.get("icon_color", ""),
                "text_color": data.get("text_color", ""),
                "ident": data.get("ident") or _extract_item_title(item),
                "persistent": data.get("persistent", False),
                "resolved_text": data.get("resolved_text", ""),
                "cleanup_hours": data.get("cleanup_hours", 0),
                "completed_at": data.get("completed_at", ""),
            }
        )
    return normalized


async def _publish_items(hass: HomeAssistant) -> None:
    """Publish the current list to all Todo Workflows card subscribers."""
    items = await _get_items(hass, DEFAULT_TODO_ENTITY_ID)
    for connection, message_id in hass.data.get(f"{DOMAIN}_subscribers", []):
        connection.send_message(
            websocket_api.event_message(message_id, {"items": _normalize_items(items)})
        )


async def _handle_complete_v2(call: ServiceCall) -> None:
    await _handle_complete(call)


async def _handle_reload(call: ServiceCall) -> None:
    """Reload the Todo Workflows config entry."""
    entries = call.hass.config_entries.async_entries(DOMAIN)
    if not entries:
        _LOGGER.warning("Todo Workflows kann nicht neu geladen werden: kein Config-Entry")
        return

    await call.hass.config_entries.async_reload(entries[0].entry_id)


@websocket_api.websocket_command(WS_LIST_ITEMS)
@websocket_api.async_response
async def _ws_list_items(hass: HomeAssistant, connection, msg) -> None:
    entity_id = DEFAULT_TODO_ENTITY_ID
    items = await _get_items(hass, entity_id)
    await _cleanup_completed_items(hass, entity_id, items)
    connection.send_result(msg["id"], {"items": _normalize_items(items)})


@websocket_api.websocket_command(WS_SUBSCRIBE_ITEMS)
@websocket_api.async_response
async def _ws_subscribe_items(hass: HomeAssistant, connection, msg) -> None:
    """Subscribe a card to Todo Workflows item updates."""
    subscribers = hass.data.setdefault(f"{DOMAIN}_subscribers", [])
    subscriber = (connection, msg["id"])
    subscribers.append(subscriber)

    def unsubscribe() -> None:
        if subscriber in subscribers:
            subscribers.remove(subscriber)

    connection.subscriptions[msg["id"]] = unsubscribe
    connection.send_result(msg["id"])


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_UPSERT_ITEM):
        hass.services.async_remove(DOMAIN, SERVICE_UPSERT_ITEM)
    if hass.services.has_service(DOMAIN, SERVICE_COMPLETE_ITEM):
        hass.services.async_remove(DOMAIN, SERVICE_COMPLETE_ITEM)
    if hass.services.has_service(DOMAIN, SERVICE_COMPLETE_ITEM_V2):
        hass.services.async_remove(DOMAIN, SERVICE_COMPLETE_ITEM_V2)
    if hass.services.has_service(DOMAIN, SERVICE_RELOAD):
        hass.services.async_remove(DOMAIN, SERVICE_RELOAD)
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPSERT_ITEM,
        _handle_upsert,
        schema=SERVICE_UPSERT_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_COMPLETE_ITEM_V2,
        _handle_complete_v2,
        schema=SERVICE_COMPLETE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RELOAD,
        _handle_reload,
        schema=vol.Schema({}),
    )
    websocket_api.async_register_command(hass, _ws_list_items)
    websocket_api.async_register_command(hass, _ws_subscribe_items)
    hass.data[DATA_SERVICES_REGISTERED] = True


async def _register_frontend(hass: HomeAssistant) -> None:
    if hass.data.get(DATA_FRONTEND_REGISTERED):
        return
    if not getattr(hass, "http", None):
        _LOGGER.warning("HTTP-Komponente noch nicht bereit, Todo-Workflows-Card wird nicht registriert")
        return
    file_path = os.path.join(os.path.dirname(__file__), "frontend", "todo-workflows-card.js")
    try:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(CARD_URL, file_path, False)]
        )
    except Exception:
        _LOGGER.exception("Registrieren der Todo-Workflows-Card ist fehlgeschlagen")
        return
    hass.data[DATA_FRONTEND_REGISTERED] = True
    _LOGGER.debug("Todo-Workflows-Card registriert unter %s", CARD_URL)


async def _register_lovelace_resource(hass: HomeAssistant) -> None:
    """Add the card as a persistent Lovelace resource when storage mode is used."""
    if hass.data.get(DATA_LOVELACE_RESOURCE_REGISTERED):
        return

    integration = await async_get_integration(hass, DOMAIN)
    card_resource_url = f"{CARD_URL}?v={integration.version}"

    lovelace_data = hass.data.get(LOVELACE_DATA)
    if not lovelace_data:
        _LOGGER.warning("Lovelace ist nicht bereit, Card-Resource wird nicht angelegt")
        return
    if lovelace_data.resource_mode != MODE_STORAGE:
        _LOGGER.warning(
            "Lovelace-Resources laufen im YAML-Modus; %s muss in configuration.yaml eingetragen werden",
            card_resource_url,
        )
        return

    resources = lovelace_data.resources
    await resources.async_get_info()
    existing_resource = next(
        (
            resource
            for resource in resources.async_items()
            if urlsplit(resource.get("url", "")).path == CARD_URL
        ),
        None,
    )
    if existing_resource:
        if existing_resource.get("url") != card_resource_url:
            await resources.async_update_item(
                existing_resource["id"],
                {"url": card_resource_url, "res_type": "module"},
            )
            _LOGGER.info(
                "Todo-Workflows-Card-Resource aktualisiert: %s", card_resource_url
            )
    else:
        await resources.async_create_item(
            {"url": card_resource_url, "res_type": "module"}
        )
        _LOGGER.info(
            "Todo-Workflows-Card als Lovelace-Resource angelegt: %s",
            card_resource_url,
        )
    hass.data[DATA_LOVELACE_RESOURCE_REGISTERED] = True


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    hass.data.setdefault(DOMAIN, {})[ATTR_CLEANUP_HOURS] = 0
    return True


async def async_setup_entry(hass: HomeAssistant, entry) -> bool:
    hass.data.setdefault(DOMAIN, {})[ATTR_CLEANUP_HOURS] = entry.options.get(
        ATTR_CLEANUP_HOURS, 0
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _register_services(hass)
    await _register_frontend(hass)
    await _register_lovelace_resource(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry) -> bool:
    """Unload the Todo Workflows config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

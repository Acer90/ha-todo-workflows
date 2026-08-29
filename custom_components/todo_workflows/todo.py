from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import uuid4

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DEFAULT_TODO_ENTITY_ID, DOMAIN

_STORAGE_VERSION = 1
_STORAGE_KEY = f"{DOMAIN}.items"


class TodoWorkflowsList(TodoListEntity):
    """Persistent Todo list managed by the Todo Workflows integration."""

    _attr_has_entity_name = True
    _attr_name = "Tasks"
    _attr_unique_id = DOMAIN
    _attr_entity_id = DEFAULT_TODO_ENTITY_ID
    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
        | TodoListEntityFeature.SET_DUE_DATE_ON_ITEM
        | TodoListEntityFeature.SET_DUE_DATETIME_ON_ITEM
        | TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM
    )

    def __init__(self, hass: HomeAssistant) -> None:
        self._store = Store(hass, _STORAGE_VERSION, _STORAGE_KEY)
        self._items: dict[str, TodoItem] = {}

    async def async_load(self) -> None:
        stored_items = await self._store.async_load() or []
        self._items = {
            item.uid: item
            for data in stored_items
            if (item := self._item_from_data(data)) is not None
        }
        self._attr_todo_items = list(self._items.values())

    async def async_create_todo_item(self, item: TodoItem) -> None:
        item.uid = item.uid or uuid4().hex
        self._items[item.uid] = item
        await self._async_save()

    async def async_update_todo_item(self, item: TodoItem) -> None:
        if item.uid not in self._items:
            return
        self._items[item.uid] = item
        await self._async_save()

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        for uid in uids:
            self._items.pop(uid, None)
        await self._async_save()

    async def _async_save(self) -> None:
        self._attr_todo_items = list(self._items.values())
        await self._store.async_save(
            [self._item_to_data(item) for item in self._items.values()]
        )
        self.async_write_ha_state()

    @staticmethod
    def _item_to_data(item: TodoItem) -> dict[str, Any]:
        return {
            "uid": item.uid,
            "summary": item.summary,
            "status": item.status.value,
            "due": item.due.isoformat() if item.due else None,
            "description": item.description,
        }

    @staticmethod
    def _item_from_data(data: Any) -> TodoItem | None:
        if not isinstance(data, dict):
            return None
        uid = data.get("uid")
        summary = data.get("summary")
        if not isinstance(uid, str) or not isinstance(summary, str):
            return None
        try:
            due_value = data.get("due")
            due = (
                datetime.fromisoformat(due_value)
                if isinstance(due_value, str) and "T" in due_value
                else date.fromisoformat(due_value)
                if isinstance(due_value, str)
                else None
            )
            return TodoItem(
                uid=uid,
                summary=summary,
                status=TodoItemStatus(data.get("status", TodoItemStatus.NEEDS_ACTION)),
                due=due,
                description=data.get("description"),
            )
        except (TypeError, ValueError):
            return None


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """Set up the Todo Workflows todo list."""
    todo_list = TodoWorkflowsList(hass)
    await todo_list.async_load()
    async_add_entities([todo_list])
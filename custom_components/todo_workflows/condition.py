"""Provides automation conditions for todo_workflows."""

from __future__ import annotations

from typing import Any, Unpack, cast

import voluptuous as vol

from homeassistant.const import CONF_OPTIONS
from homeassistant.core import HomeAssistant
from homeassistant.helpers.condition import Condition, ConditionCheckParams, ConditionConfig
from homeassistant.helpers.typing import ConfigType

from . import (
    _find_item_by_ident_in_entity,
    _find_item_by_ident_in_states,
    _is_item_completed,
)
from .const import DEFAULT_TODO_ENTITY_ID

ATTR_IDENT = "ident"
ATTR_COMPLETED = "completed"

_OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_IDENT): cv.string,
        vol.Optional(ATTR_COMPLETED): cv.boolean,
    }
)

_CONDITION_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_OPTIONS): _OPTIONS_SCHEMA,
    }
)


class HasIdentCondition(Condition):
    """Check if a todo item with a specific ident exists."""

    @classmethod
    async def async_validate_complete_config(
        cls, hass: HomeAssistant, complete_config: ConfigType
    ) -> ConfigType:
        """Validate and migrate top-level fields into target/options."""
        complete_config = dict(complete_config)
        options = dict(complete_config.get(CONF_OPTIONS) or {})

        if ATTR_IDENT in complete_config and ATTR_IDENT not in options:
            options[ATTR_IDENT] = complete_config.pop(ATTR_IDENT)

        if ATTR_COMPLETED in complete_config and ATTR_COMPLETED not in options:
            options[ATTR_COMPLETED] = complete_config.pop(ATTR_COMPLETED)

        complete_config[CONF_OPTIONS] = options
        return await super().async_validate_complete_config(hass, complete_config)

    @classmethod
    async def async_validate_config(
        cls, hass: HomeAssistant, config: ConfigType
    ) -> ConfigType:
        """Validate condition-specific config."""
        return cast(ConfigType, _CONDITION_SCHEMA(config))

    def __init__(self, hass: HomeAssistant, config: ConditionConfig) -> None:
        """Initialize condition."""
        super().__init__(hass, config)
        options = config.options or {}

        self._ident = str(options[ATTR_IDENT]).strip()
        self._expected_completed = options.get(ATTR_COMPLETED)

    def _async_check(self, **kwargs: Unpack[ConditionCheckParams]) -> bool:
        """Return true when the ident exists and optional completion matches."""
        if not self._ident:
            return False

        item = _find_item_by_ident_in_entity(
            self._hass, DEFAULT_TODO_ENTITY_ID, self._ident
        ) or _find_item_by_ident_in_states(
            self._hass, DEFAULT_TODO_ENTITY_ID, self._ident
        )
        if not item:
            return False

        return self._expected_completed is None or _is_item_completed(item) == bool(
            self._expected_completed
        )


CONDITIONS: dict[str, type[Condition]] = {
    "has_ident": HasIdentCondition,
}


async def async_get_conditions(hass: HomeAssistant) -> dict[str, type[Condition]]:
    """Return all todo_workflows conditions."""
    return CONDITIONS

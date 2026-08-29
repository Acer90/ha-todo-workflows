"""Provides automation conditions for todo_workflows."""

from __future__ import annotations

from typing import Any, Unpack, cast

import voluptuous as vol

from homeassistant.const import CONF_ENTITY_ID, CONF_OPTIONS, CONF_TARGET
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.condition import Condition, ConditionCheckParams, ConditionConfig
from homeassistant.helpers.typing import ConfigType

from . import _find_item_by_ident_in_states, _is_item_completed

ATTR_IDENT = "ident"
ATTR_COMPLETED = "completed"

_OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_IDENT): cv.string,
        vol.Optional(ATTR_COMPLETED): cv.boolean,
    }
)

_TARGET_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ENTITY_ID): vol.All(cv.ensure_list, [cv.entity_id]),
    }
)

_CONDITION_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TARGET): _TARGET_SCHEMA,
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
        target = dict(complete_config.get(CONF_TARGET) or {})
        options = dict(complete_config.get(CONF_OPTIONS) or {})

        if CONF_ENTITY_ID in complete_config and CONF_ENTITY_ID not in target:
            target[CONF_ENTITY_ID] = complete_config.pop(CONF_ENTITY_ID)

        if ATTR_IDENT in complete_config and ATTR_IDENT not in options:
            options[ATTR_IDENT] = complete_config.pop(ATTR_IDENT)

        if ATTR_COMPLETED in complete_config and ATTR_COMPLETED not in options:
            options[ATTR_COMPLETED] = complete_config.pop(ATTR_COMPLETED)

        complete_config[CONF_TARGET] = target
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
        target = config.target or {}
        options = config.options or {}

        self._entity_ids = [str(entity_id) for entity_id in target.get(CONF_ENTITY_ID, [])]
        self._ident = str(options[ATTR_IDENT]).strip()
        self._expected_completed = options.get(ATTR_COMPLETED)

    def _async_check(self, **kwargs: Unpack[ConditionCheckParams]) -> bool:
        """Return true when the ident exists and optional completion matches."""
        if not self._ident:
            return False

        for entity_id in self._entity_ids:
            item = _find_item_by_ident_in_states(self._hass, entity_id, self._ident)
            if not item:
                continue

            if self._expected_completed is None:
                return True

            if _is_item_completed(item) == bool(self._expected_completed):
                return True

        return False


CONDITIONS: dict[str, type[Condition]] = {
    "has_ident": HasIdentCondition,
}


async def async_get_conditions(hass: HomeAssistant) -> dict[str, type[Condition]]:
    """Return all todo_workflows conditions."""
    return CONDITIONS

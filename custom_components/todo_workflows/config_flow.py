from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries

from .const import ATTR_CLEANUP_HOURS, DOMAIN


@config_entries.HANDLERS.register(DOMAIN)
class TodoWorkflowsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry):
        return TodoWorkflowsOptionsFlow(config_entry)

    async def async_step_user(self, user_input=None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            return self.async_create_entry(title="Todo Workflows", data={})

        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))


class TodoWorkflowsOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self._config_entry.options.get(ATTR_CLEANUP_HOURS, 0)
        schema = vol.Schema(
            {
                vol.Optional(ATTR_CLEANUP_HOURS, default=current): vol.Coerce(int)
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

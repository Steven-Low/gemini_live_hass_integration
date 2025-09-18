import numbers
import voluptuous as vol
from typing import Any, Dict, List, Iterable
from homeassistant.helpers import (
    area_registry,
    entity_registry,
    device_registry as dr, intent, llm, template
)

from homeassistant.core import HomeAssistant
from collections.abc import Callable
from typing import Any, Literal
from openai.types.chat import (
    ChatCompletionToolParam
)
from openai.types.shared_params import FunctionDefinition
from voluptuous_openapi import convert

from ..models.exposed_entity import ExposedEntity

def _format_tool(tool: llm.Tool, custom_serializer: Callable[[Any], Any] | None ) -> ChatCompletionToolParam:
    """Format tool specification."""
    tool_spec = FunctionDefinition(
        name=tool.name,
        parameters=convert(tool.parameters, custom_serializer=custom_serializer),
    )
    if tool.description:
        tool_spec["description"] = tool.description
    return ChatCompletionToolParam(type="function", function=tool_spec)

def _sanitize_attributes(attrs: dict) -> dict:
    clean = {}
    for k, v in attrs.items():
        # Drop noisy keys
        if k in ("friendly_name", "supported_features", "attribution", "editable", "icon"):
            continue

        # Convert Enums like <UnitOfTemperature.CELSIUS: '°C'> → "°C"
        if hasattr(v, "value"):
            clean[k] = v.value
        # Keep only basic JSON-friendly values
        elif isinstance(v, (str, numbers.Number, bool)):
            clean[k] = v
    return clean

async def get_exposed_entities(hass: HomeAssistant):
    er = entity_registry.async_get(hass)
    ar = area_registry.async_get(hass)

    entities = []
    for entity in er.entities.values():
        opts = entity.options.get("conversation") if entity.options else {}
        if opts and opts.get("should_expose") is True and entity.disabled_by is None:
            state_obj = hass.states.get(entity.entity_id)
            if not state_obj:
                continue

            area_name = None
            if entity.area_id:
                area = ar.areas.get(entity.area_id)
                if area:
                    area_name = area.name

            entities.append(
                {
                    "names": state_obj.name or entity.entity_id,
                    "domain": entity.domain,
                    "state": state_obj.state,
                    "areas": area_name,
                    "attributes": _sanitize_attributes(state_obj.attributes),
                }
            )

    return entities

def convert_entities_to_prompt(entities: list[ExposedEntity]) -> str:
    lines = []
    for e in entities:
        lines.append(f"- names: {e['names']}")
        lines.append(f"  domain: {e['domain']}")
        lines.append(f"  state: {e['state']!r}")
        if e.get("areas"):
            lines.append(f"  areas: {e['areas']}")
        if attr := e.get("attributes"):
            if len(attr) > 0:
                lines.append("  attributes:")
                for k, v in e["attributes"].items():
                    lines.append(f"    {k}: {v}")
    return "\n".join(lines)



def _sanitize_schema(schema: Any) -> Any:
    """Recursively sanitize a JSON Schema dict so values are JSON-serializable
    and enums are plain strings. Leaves other values as-is."""
    if isinstance(schema, dict):
        out: Dict[str, Any] = {}
        for k, v in schema.items():
            if k == "enum" and isinstance(v, (list, tuple)):
                out[k] = [str(x) for x in v]
            elif k in ("properties",):
                out[k] = {pk: _sanitize_schema(pv) for pk, pv in v.items()}
            elif k == "items":
                out[k] = _sanitize_schema(v)
            else:
                out[k] = _sanitize_schema(v) if isinstance(v, (dict, list)) else v
        return out
    if isinstance(schema, list):
        return [_sanitize_schema(x) for x in schema]
    return schema


def convert_openai_tools_to_gemini(openai_tools: Iterable[dict]) -> List[dict]:
    """
    Convert OpenAI-style tool definitions into Gemini-compatible function_declarations.
    No renaming is applied.
    """
    gemini_funcs: List[dict] = []

    for t in openai_tools:
        if not isinstance(t, dict):
            continue
        func = t.get("function") or {}
        if not func or not isinstance(func, dict):
            continue

        name = func.get("name")
        if not name:
            continue

        decl: dict = {"name": name}
        if desc := func.get("description"):
            decl["description"] = desc

        params = func.get("parameters")
        if params:
            decl["parameters"] = _sanitize_schema(params)

        gemini_funcs.append(decl)

    return gemini_funcs

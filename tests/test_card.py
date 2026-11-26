"""The dashboard card: served, loaded on every page and kept as a dashboard resource.

Skeleton from the repository template (STANDARD §7, the dashboard card). The tests
of the resource need nothing of the integration but frontend.py; the ones of the
script read it as text.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL
from homeassistant.components.lovelace.const import LOVELACE_DATA, MODE_STORAGE, MODE_YAML
from homeassistant.components.lovelace.resources import ResourceStorageCollection
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.network_plus import frontend
from custom_components.network_plus.frontend import CARD_FILENAME, CARD_PATH, DATA_CARD

CARD = (
    Path(__file__).resolve().parent.parent
    / "custom_components"
    / "network_plus"
    / "frontend"
    / CARD_FILENAME
)


def _lovelace(mode: str, resources: Any) -> MagicMock:
    data = MagicMock()
    data.resource_mode = mode
    data.resources = resources
    return data


def _collection(items: list[dict[str, Any]]) -> MagicMock:
    collection = MagicMock(spec=ResourceStorageCollection)
    collection.async_get_info = AsyncMock()
    collection.async_items = MagicMock(return_value=items)
    collection.async_create_item = AsyncMock()
    collection.async_update_item = AsyncMock()
    collection.async_delete_item = AsyncMock()
    return collection


async def test_card_loaded_and_resource_created(hass: HomeAssistant) -> None:
    """With the frontend there the card is loaded on every page and added to the resources."""
    assert await async_setup_component(hass, "http", {})
    collection = _collection([{"id": "1", "url": "/local/other.js", "type": "module"}])
    hass.data[LOVELACE_DATA] = _lovelace(MODE_STORAGE, collection)
    hass.data[DATA_EXTRA_MODULE_URL] = MagicMock()
    with patch(f"{frontend.__name__}.add_extra_js_url") as add_url:
        await frontend.async_register_card(hass)
    url = hass.data[DATA_CARD]
    assert url.startswith(f"{CARD_PATH}?v=")
    add_url.assert_called_once_with(hass, url)
    collection.async_create_item.assert_awaited_once_with({"res_type": "module", "url": url})
    collection.async_update_item.assert_not_awaited()

    # Loaded once: a second setup adds nothing to the pages
    with patch(f"{frontend.__name__}.add_extra_js_url") as add_url:
        await frontend.async_register_card(hass)
    add_url.assert_not_called()


async def test_card_unloaded_and_loaded_again(hass: HomeAssistant) -> None:
    """Removing the last entry takes the script off the pages; a new entry loads it again."""
    assert await async_setup_component(hass, "http", {})
    loaded: set[str] = set()
    hass.data[DATA_EXTRA_MODULE_URL] = loaded  # type: ignore[assignment]
    await frontend.async_register_card(hass)
    url = hass.data[DATA_CARD]
    assert loaded == {url}

    frontend.async_unload_card(hass)
    assert loaded == set()
    # Nothing to take back a second time
    frontend.async_unload_card(hass)

    await frontend.async_register_card(hass)
    assert loaded == {url}


async def test_card_resource_updated_and_removed(hass: HomeAssistant) -> None:
    """An entry of an earlier version is pointed to this one; removing takes it away."""
    assert await async_setup_component(hass, "http", {})
    await frontend.async_register_card(hass)
    url = hass.data[DATA_CARD]
    collection = _collection([{"id": "7", "url": f"{CARD_PATH}?v=old", "type": "module"}])
    hass.data[LOVELACE_DATA] = _lovelace(MODE_STORAGE, collection)
    await frontend.async_register_card(hass)
    collection.async_update_item.assert_awaited_once_with("7", {"url": url})
    collection.async_create_item.assert_not_awaited()

    await frontend.async_remove_card_resource(hass)
    collection.async_delete_item.assert_awaited_once_with("7")


async def test_card_resource_left_alone(hass: HomeAssistant) -> None:
    """Resources in YAML are the user's; a collection of another kind is left alone."""
    assert await async_setup_component(hass, "http", {})
    await frontend.async_register_card(hass)
    collection = _collection([{"id": "3", "url": hass.data[DATA_CARD], "type": "module"}])
    hass.data[LOVELACE_DATA] = _lovelace(MODE_YAML, collection)
    await frontend.async_register_card(hass)
    await frontend.async_remove_card_resource(hass)
    collection.async_create_item.assert_not_awaited()
    collection.async_delete_item.assert_not_awaited()

    hass.data[LOVELACE_DATA] = _lovelace(MODE_STORAGE, MagicMock())
    await frontend.async_register_card(hass)
    await frontend.async_remove_card_resource(hass)


def _language_keys(code: str, language: str) -> list[str]:
    """The keys of one language of the card's texts, in order."""
    start = code.index(f"\n    {language}: {{\n")
    end = code.index("\n    },", start)
    return re.findall(r"^\s+([A-Za-z_][A-Za-z0-9_]*): \"", code[start:end], re.MULTILINE)


def test_card_texts_have_each_key_once_in_both_languages() -> None:
    """A key written twice silently takes the later text; both languages have the same keys."""
    code = CARD.read_text(encoding="utf-8")
    english = _language_keys(code, "en")
    german = _language_keys(code, "de")
    assert english
    for keys in (english, german):
        twice = sorted({key for key in keys if keys.count(key) > 1})
        assert not twice, twice
    assert english == german


# The actions the card's controls may call, as "domain.action" (STANDARD §7: only
# what every user may operate on the card's entities anyway, such as media
# control). Empty for a card that only reads.
ACTIONS: set[str] = set()
# The subscriptions a view of the card may open, by type (STANDARD §7: a view with
# live data no entity holds). Empty for a card that reads entities only.
SUBSCRIPTIONS: set[str] = set()


def test_card_reads_and_calls_only_what_is_listed() -> None:
    """The card reads entities: no command, no fetch, no action and no subscription outside its lists."""
    card = CARD.read_text(encoding="utf-8")
    # With the modules it loads: a view with live data keeps its code in one of its own
    code = card
    for name in re.findall(r'"\./([\w-]+\.js)(?:\?[^"]*)?"', card):
        code += (CARD.parent / name).read_text(encoding="utf-8")
    for call in ("callWS", "callApi", "fetch(", "sendMessage"):
        assert call not in code, call
    listed = re.search(r"const ACTIONS = \[(.*?)\];", code, re.DOTALL)
    assert set(re.findall(r'"([a-z_]+\.[a-z_]+)"', listed.group(1)) if listed else []) == ACTIONS
    # One place calls, with an entry of the list; a card without controls calls nothing
    assert code.count("callService") == (1 if ACTIONS else 0)
    # Every subscription is one of the list; a card without a live view opens none
    subscribed = re.findall(r'subscribeMessage\([^;]*?\{ type: "([^"]+)" \}', code, re.DOTALL)
    assert code.count("subscribeMessage(") == len(subscribed)
    assert set(subscribed) == SUBSCRIPTIONS
    # The editor fills in the defaults: Home Assistant's own form editor would show
    # an option that is not in the configuration as off
    assert "static getConfigElement()" in card
    assert "{ ...DEFAULTS, ...this._config }" in card
    # Every option the card reads has a field in its editor
    options = set(re.findall(r"\bc\.(\w+)", card))
    for option in options - {"title", "size"}:
        assert f'"{option}"' in card, option

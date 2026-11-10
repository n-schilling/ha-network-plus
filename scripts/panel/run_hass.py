"""Start Home Assistant for the page tests, reachable from this machine only.

Home Assistant resolves names with an mDNS resolver that listens on every
network interface (UDP 5353). The page tests need no name resolution, so this
starter swaps it for the plain resolver of aiohttp; together with
`server_host: 127.0.0.1` nothing listens beyond the loopback interface, and the
desktop firewall has nothing to ask about.

Usage: python -I run_hass.py -c <config dir>
"""

import sys

import aiohttp

import homeassistant.helpers.aiohttp_client as aiohttp_client


class _LocalResolver(aiohttp.ThreadedResolver):
    """The resolver of aiohttp, closed the way Home Assistant closes its own."""

    async def real_close(self) -> None:
        await self.close()


aiohttp_client._async_make_resolver = lambda hass: _LocalResolver()

from homeassistant.__main__ import main

sys.exit(main())

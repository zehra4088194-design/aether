"""Read-only market information. No order endpoints exist in this plugin."""

from __future__ import annotations

from datetime import datetime, timezone

import requests

from aether.agent_loop import AetherPlugin, ToolResult, ToolSpec


DISCLAIMER = "Informational only — not financial advice. Aether cannot place trades or orders."


class TradingPlugin(AetherPlugin):
    name = "trading"
    description = "Fetch read-only public market quotes with prominent informational disclaimers."
    resource_cost = "light"

    def _quote(self, args: dict) -> ToolResult:
        symbol = str(args["symbol"]).strip().upper()
        if not symbol or len(symbol) > 15:
            return ToolResult(ok=False, output=f"Invalid symbol. {DISCLAIMER}")
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        try:
            response = requests.get(
                url,
                params={"interval": "1d", "range": "5d"},
                headers={"User-Agent": "Aether/1.0"},
                timeout=12,
            )
            response.raise_for_status()
            result = response.json()["chart"]["result"][0]
            meta = result["meta"]
            price = meta.get("regularMarketPrice")
            previous = meta.get("chartPreviousClose") or meta.get("previousClose")
            currency = meta.get("currency", "")
            change = None if not previous or price is None else price - previous
            change_pct = None if not previous or change is None else (change / previous) * 100
        except (requests.RequestException, KeyError, IndexError, TypeError, ValueError) as exc:
            return ToolResult(ok=False, output=f"Could not load {symbol}: {exc}. {DISCLAIMER}")
        movement = ""
        if change is not None and change_pct is not None:
            movement = f" ({change:+.2f}, {change_pct:+.2f}%)"
        output = f"{symbol}: {price} {currency}{movement}. {DISCLAIMER}"
        return ToolResult(
            ok=True,
            output=output,
            data={
                "symbol": symbol,
                "price": price,
                "currency": currency,
                "change": change,
                "change_percent": change_pct,
                "as_of": datetime.now(timezone.utc).isoformat(),
                "disclaimer": DISCLAIMER,
            },
        )

    def get_tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="get_market_quote",
                description=(
                    "Fetch a delayed/public market quote for informational use only. "
                    "This plugin has no order-execution capability."
                ),
                input_schema={
                    "type": "object",
                    "properties": {"symbol": {"type": "string"}},
                    "required": ["symbol"],
                },
                handler=self._quote,
                # Even read-only data touches an external service, so the central
                # confirmation path remains visible and auditable.
                requires_confirmation=True,
                resource_cost="light",
            )
        ]

"""Individual RB/HC contract daily quotes without synthetic rolling."""

from .adapters import FuturesQuote, parse_shfe

__all__ = ["FuturesQuote", "parse_shfe"]

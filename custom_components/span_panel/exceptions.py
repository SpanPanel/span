"""Exceptions for Span Panel integration."""


class SpanPanelReturnedEmptyData(Exception):
    """Exception raised when the Span Panel API returns empty or missing data."""


class SpanPanelSimulationOfflineError(Exception):
    """Exception raised when the panel is intentionally offline in simulation mode."""

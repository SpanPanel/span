"""Constants for Gen3 Span panel gRPC support."""

# Configuration key to distinguish Gen2 (REST) from Gen3 (gRPC)
CONF_PANEL_GEN = "panel_generation"
GEN2 = "gen2"
GEN3 = "gen3"

# gRPC connection
DEFAULT_GRPC_PORT = 50065
GRPC_SERVICE_PATH = "/io.span.panel.protocols.traithandler.TraitHandlerService"

# Trait IDs
TRAIT_BREAKER_GROUPS = 15
TRAIT_CIRCUIT_NAMES = 16
TRAIT_BREAKER_CONFIG = 17
TRAIT_POWER_METRICS = 26
TRAIT_RELAY_STATE = 27
TRAIT_BREAKER_PARAMS = 31

# Vendor/Product IDs
VENDOR_SPAN = 1
PRODUCT_GEN3_PANEL = 4
PRODUCT_GEN3_GATEWAY = 5

# Metric IID offset: circuit N -> metric IID = N + 27
METRIC_IID_OFFSET = 27

# Main feed IID (always 1 for trait 26)
MAIN_FEED_IID = 1

# Voltage threshold for breaker state detection (millivolts)
# Below this = breaker OFF
BREAKER_OFF_VOLTAGE_MV = 5000  # 5V

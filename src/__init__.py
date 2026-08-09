"""Amazon 5461/GTIN 自动化工具包"""

from .flow_verify_add_product import (
    BrandVerifyFlow,
    run_verify_add_product,
    DEFAULT_SELECTORS,
    SUCCESS_MARKERS,
    FAIL_MARKERS,
)

__all__ = [
    "BrandVerifyFlow",
    "run_verify_add_product",
    "DEFAULT_SELECTORS",
    "SUCCESS_MARKERS",
    "FAIL_MARKERS",
]

try:
    from .smart_teach_framework import (
        SmartTeachFramework,
        AdaptiveExecutor,
        NaturalLanguageParser,
        SmartSelectorFinder,
        ACTION_LIBRARY,
    )
except ModuleNotFoundError:
    SmartTeachFramework = None
    AdaptiveExecutor = None
    NaturalLanguageParser = None
    SmartSelectorFinder = None
    ACTION_LIBRARY = None
else:
    __all__.extend([
        "SmartTeachFramework",
        "AdaptiveExecutor",
        "NaturalLanguageParser",
        "SmartSelectorFinder",
        "ACTION_LIBRARY",
    ])

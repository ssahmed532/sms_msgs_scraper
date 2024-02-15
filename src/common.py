from enum import StrEnum, auto
from zoneinfo import ZoneInfo

DEFAULT_CURRENCY = "PKR"

DEFAULT_TZ = ZoneInfo("Asia/Karachi")
"""
    The default timezone used in this application
"""


class Currency(StrEnum):
    PKR = auto()
    USD = auto()
    CAD = auto()


class SpendingCategories(StrEnum):
    GROCERIES = auto()
    FUEL = auto()
    AMAZON_PURCHASES = auto()
    HOSPITAL = auto()
    MEDICAL_PHARMACY = auto()
    BAKERY = auto()
    FASHION_CLOTHING = auto()
    RESTAURANTS = auto()
    SALMAN_TECH_CAREER = auto()
    MISCELLANEOUS_OTHER = auto()

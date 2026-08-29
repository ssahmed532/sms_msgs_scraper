"""Per-bank message parsers, and the registry that binds them to their banks.

Each `*_sms_parser.py` owns one bank's templates and nothing else. `registry.py`
is the one module here that is not a bank: it declares every `BankSpec` and is
the only place a sender short code appears.
"""

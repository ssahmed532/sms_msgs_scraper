# Codebase Improvements & Fixes

A comprehensive audit of the SMS Messages Scraper codebase, covering performance issues,
Python 3.14 modernization opportunities, bugs, code quality, and test coverage gaps.

---

## 1. Performance & Optimization Issues

### 1a. Regex recompiled on every call

**File:** `parser/hbl_sms_parser.py:49`
**Severity:** Medium

`_extractCurrencyAndAmount` calls `re.compile(HBLSmsParser.HBL_CC_TXN_AMOUNT_RE)` on every
invocation, creating a new compiled regex object per transaction. The main pattern
`HBL_CC_TXN_PTTRN` is correctly pre-compiled at class level (line 16), but the amount
pattern is not.

**Current code:**
```python
@staticmethod
def _extractCurrencyAndAmount(strValue) -> CurrencyAmountTuple:
    currency = None
    amount = -1.2345

    pattern = re.compile(HBLSmsParser.HBL_CC_TXN_AMOUNT_RE)
    m = pattern.match(strValue)
```

**Fix:** Pre-compile at class level, matching the existing pattern for `HBL_CC_TXN_PTTRN`:
```python
# At class level (alongside HBL_CC_TXN_PTTRN):
HBL_CC_TXN_AMOUNT_PTTRN = re.compile(HBL_CC_TXN_AMOUNT_RE)

# In _extractCurrencyAndAmount:
m = HBLSmsParser.HBL_CC_TXN_AMOUNT_PTTRN.match(strValue)
```

---

### 1b. SHA3-512 is overkill for duplicate detection

**File:** `sms_backup_file_parser.py:37`
**Severity:** Low

`hashlib.sha3_512` produces a 128-character hex digest. For deduplicating SMS messages
(short strings, non-adversarial context), `hashlib.sha256` or even `hashlib.md5` would be
significantly faster while being perfectly adequate.

Better yet, skip hashing entirely and use the raw `body` string as the dict key — a typical
SMS body is shorter than its own SHA3-512 hex digest (128 chars).

**Current code:**
```python
sha256Hash = hashlib.sha3_512(msgBody.encode("utf-8")).hexdigest()
```

**Fix (option A — simpler hash):**
```python
msg_hash = hashlib.sha256(msgBody.encode("utf-8")).hexdigest()
```

**Fix (option B — skip hashing entirely):**
```python
# Use the message body itself as the dedup key
def _isSmsDuplicate(self, sms) -> bool:
    body = sms.attrib["body"].strip()
    if body in self.seen_bodies:
        return True
    self.seen_bodies.add(body)
    return False
```

---

### 1c. Duplicate hash computed multiple times per message

**File:** `sms_backup_file_parser.py:109-128`
**Severity:** Medium

In `parseMessages()`, the `elif` chain calls `self._isSmsDuplicate(child)` independently in
each branch. If a message is not from HBL, the hash is recomputed when checking FBL, then
again for SCB, then again for MEZN — up to 4 hash computations per message.

**Current code:**
```python
if HBLSmsParser.isSmsFromHBL(child) and (not self._isSmsDuplicate(child)):
    ...
elif (child.attrib["address"] in self.FBL_SHORT_CODES) and (not self._isSmsDuplicate(child)):
    ...
elif (child.attrib["address"] in self.SCB_SHORT_CODES) and (not self._isSmsDuplicate(child)):
    ...
elif (child.attrib["address"] in self.MEZN_SHORT_CODES) and (not self._isSmsDuplicate(child)):
    ...
```

**Fix:** Perform the duplicate check once, before the bank-routing logic:
```python
if child.tag == "mms":
    continue

self.msgCounts["ALL"] += 1

if self._isSmsDuplicate(child):
    continue

address = child.attrib["address"]
if address in HBLSmsParser.HBL_SHORT_CODES:
    self.msgCounts[HBLSmsParser.ID] += 1
    ...
elif address in self.FBL_SHORT_CODES:
    self.msgCounts["FBL"] += 1
elif ...
```

---

### 1d. Entire XML tree loaded into memory

**File:** `sms_backup_file_parser.py:61`
**Severity:** Medium

`ET.parse(filepath)` loads the entire XML DOM into memory. For large SMS backup files
(thousands of messages), `ET.iterparse()` would be far more memory-efficient, processing
elements one at a time and discarding them after use.

**Current code:**
```python
def loadFromSmsBackupFile(self, filepath: str):
    self.xmlTree = ET.parse(filepath)
    self.xmlRoot = self.xmlTree.getroot()
    self.expectedMsgs = int(self.xmlRoot.attrib["count"])
```

**Fix:** Use iterative parsing:
```python
def loadAndParseMessages(self, filepath: str):
    for event, elem in ET.iterparse(filepath, events=("end",)):
        if elem.tag == "sms":
            self._processSmsMsgElement(elem)
            elem.clear()  # free memory
```

Note: This requires restructuring since the root's `count` attribute is currently read
before iteration. The count can be read via a two-pass approach or by counting during
iteration.

---

### 1e. Dataclass missing `slots=True`

**File:** `cc_txn.py:15`
**Severity:** Low

`@dataclass` without `slots=True` uses a `__dict__` per instance. Adding
`@dataclass(slots=True)` (available since Python 3.10) reduces memory usage per instance and
speeds up attribute access — meaningful when you have thousands of transactions.

**Current code:**
```python
@dataclass
class CreditCardTxnDC:
```

**Fix:**
```python
@dataclass(slots=True)
class CreditCardTxnDC:
```

**Caveat:** The mutable class variable `DEFAULT_TZ` on the dataclass is incompatible with
`slots=True`. That constant must be moved out first (see item 3e).

---

## 2. Python 3.12–3.14 Features Not Utilized

### 2a. Modern dataclass parameters

**File:** `cc_txn.py:15`
**Severity:** Low

Since `CreditCardTxnDC` is a value object that should not be mutated after creation,
`frozen=True` enforces immutability. `kw_only=True` (Python 3.10+) prevents positional
construction bugs.

**Fix:**
```python
@dataclass(frozen=True, slots=True, kw_only=True)
class CreditCardTxnDC:
    amountTuple: CurrencyAmountTuple
    date: datetime
    vendor: str
    ccLastFourDigits: int = 0
```

Requires moving `DEFAULT_TZ` out of the class first (see item 3e).

---

### 2b. `typing.NamedTuple` instead of `collections.namedtuple`

**File:** `cc_txn.py:12`
**Severity:** Low

`CurrencyAmountTuple` uses the old-style `collections.namedtuple`, which provides no type
information for the fields.

**Current code:**
```python
from collections import namedtuple
CurrencyAmountTuple = namedtuple("CurrencyAmountTuple", ["currency", "amount"])
```

**Fix:**
```python
from typing import NamedTuple

class CurrencyAmountTuple(NamedTuple):
    currency: str
    amount: float
```

---

### 2c. Structural pattern matching (`match`/`case`)

**File:** `sms_backup_file_parser.py:94-130`
**Severity:** Low

The `if/elif/else` chain routing messages to bank parsers by short code is a textbook use
case for `match`/`case` (Python 3.10+). It becomes cleaner as more banks are added.

**Current code:**
```python
if HBLSmsParser.isSmsFromHBL(child) and ...:
    ...
elif (child.attrib["address"] in self.FBL_SHORT_CODES) and ...:
    ...
elif (child.attrib["address"] in self.SCB_SHORT_CODES) and ...:
    ...
```

**Fix (after extracting duplicate check per 1c):**
```python
match address:
    case addr if addr in HBLSmsParser.HBL_SHORT_CODES:
        self.msgCounts[HBLSmsParser.ID] += 1
        ...
    case addr if addr in self.FBL_SHORT_CODES:
        self.msgCounts["FBL"] += 1
    case addr if addr in self.SCB_SHORT_CODES:
        self.msgCounts["SCB"] += 1
    case addr if addr in self.MEZN_SHORT_CODES:
        self.msgCounts["MEZN"] += 1
    case _:
        self.msgCounts["OTHER"] += 1
```

---

### 2d. Type alias syntax (PEP 695, Python 3.12+)

**File:** All files using `xml.etree.ElementTree.Element`
**Severity:** Low

The verbose type hint `xml.etree.ElementTree.Element` is repeated throughout. Python 3.12+
supports the `type` statement for clean type aliases.

**Fix:** Add a shared type alias:
```python
# In common.py:
import xml.etree.ElementTree as ET
type SmsElement = ET.Element
```

Then use `SmsElement` in all type hints throughout the codebase.

---

### 2e. `@typing.override` decorator (Python 3.12+)

**File:** `cc_txn.py:33`
**Severity:** Low

The `__repr__` method overrides the dataclass-generated one. `@typing.override` makes this
intent explicit and enables static analysis tools to catch errors.

**Fix:**
```python
from typing import override

@override
def __repr__(self) -> str:
    return f"CC Txn: {self.amountTuple.currency} {self.amountTuple.amount:.2f} ..."
```

---

## 3. Code Quality & Correctness Issues

### 3a. Missing `@staticmethod` decorators

**File:** `sms_backup_file_parser.py:40-45`
**Severity:** **High**

`printSmsMsg` and `smsMsgToString` lack the `@staticmethod` decorator. They define `sms` as
their first parameter where Python would bind `self`. Calling
`instance.printSmsMsg(some_element)` would fail — the instance is silently bound to `sms`,
and `some_element` becomes an unexpected second argument, raising a `TypeError`.

**Current code:**
```python
def printSmsMsg(sms: xml.etree.ElementTree.Element) -> None:
    strValue = ET.tostring(sms, encoding="utf-8").decode("utf-8")
    print(strValue)

def smsMsgToString(sms: xml.etree.ElementTree.Element) -> str:
    return ET.tostring(sms, encoding="utf-8").decode("utf-8")
```

**Fix:**
```python
@staticmethod
def printSmsMsg(sms: xml.etree.ElementTree.Element) -> None:
    strValue = ET.tostring(sms, encoding="utf-8").decode("utf-8")
    print(strValue)

@staticmethod
def smsMsgToString(sms: xml.etree.ElementTree.Element) -> str:
    return ET.tostring(sms, encoding="utf-8").decode("utf-8")
```

---

### 3b. `assert` used for runtime validation

**Files:** `sms_backup_file_parser.py:64,114-119` and `parser/hbl_sms_parser.py:85,101,106`
**Severity:** Medium

`assert` statements are stripped when Python runs with `-O` (optimized mode). Using `assert`
for data validation means all checks silently disappear in production.

**Current code:**
```python
assert self.expectedMsgs > 0
...
assert ccTxn is not None
assert ccTxn.amountTuple.currency
assert ccTxn.amountTuple.amount > 0
```

**Fix:** Use explicit validation with proper exceptions:
```python
if self.expectedMsgs <= 0:
    raise ValueError(f"Expected message count must be positive, got {self.expectedMsgs}")
...
if ccTxn is None:
    raise ValueError("Failed to parse transaction from SMS message")
if not ccTxn.amountTuple.currency:
    raise ValueError(f"Missing currency in parsed transaction: {ccTxn}")
```

---

### 3c. Global mutable state

**File:** `hbl_sms_query_tool.py:14`
**Severity:** Medium

`smsParser = None` at module level, mutated via `global smsParser` inside `cli()`. This
pattern is fragile, makes testing difficult, and prevents concurrent usage.

**Current code:**
```python
smsParser = None

@click.group()
def cli(filepath):
    global smsParser
    smsParser = SmsBackupFileParser()
    ...
```

**Fix:** Use Click's context object:
```python
@click.group()
@click.pass_context
def cli(ctx, filepath):
    ctx.ensure_object(dict)
    parser = SmsBackupFileParser()
    parser.loadFromSmsBackupFile(filepath)
    parser.parseMessages()
    ctx.obj["parser"] = parser

@cli.command()
@click.pass_context
def list_all_vendors(ctx):
    parser = ctx.obj["parser"]
    ...
```

---

### 3d. Unused import

**File:** `hbl_sms_query_tool.py:4`
**Severity:** Low

`from pprint import PrettyPrinter` is imported but never used. Only `pprint.pprint` (from
`import pprint` on line 1) is actually called.

**Fix:** Remove line 4:
```python
# Delete this line:
from pprint import PrettyPrinter
```

---

### 3e. Duplicate `DEFAULT_TZ` definition

**Files:** `common.py:6` and `cc_txn.py:23`
**Severity:** Low

`DEFAULT_TZ = ZoneInfo("Asia/Karachi")` is defined in both files. The TODO on `cc_txn.py:21`
already notes this should be consolidated.

**Fix:** Remove the definition from `cc_txn.py` and import from `common.py`:
```python
# In cc_txn.py:
from common import DEFAULT_TZ
```

Update all references from `CreditCardTxnDC.DEFAULT_TZ` to the imported `DEFAULT_TZ`.

---

### 3f. `SpendingCategories` enum defined but never used

**File:** `common.py:18`
**Severity:** Low

The `SpendingCategories` StrEnum is dead code. It is not referenced anywhere in the
codebase. It should either be removed or integrated into the transaction model if spending
category classification is planned.

---

### 3g. Dead code — duplicate parsing architecture

**File:** `parser/hbl_sms_parser.py:119-159`
**Severity:** Medium

The `HBLSmsParser` class has instance methods `loadFromSmsBackupFile` and `parseMessages`
that duplicate the same logic now handled by `SmsBackupFileParser`. These instance methods
appear to be leftover from before the architectural refactor and are never called.

**Fix:** Remove the dead instance methods (lines 119–159) along with the now-unnecessary
instance variables in `__init__` (lines 24–29), since all real work is done through the
static methods.

---

### 3h. Shadowed builtin

**File:** `sms_backup_file_parser.py:68`
**Severity:** Low

The local variable `hash` shadows Python's built-in `hash()` function.

**Current code:**
```python
hash = SmsBackupFileParser.calcSmsMsgHash(sms)
duplicate = hash in self.msgHashes
```

**Fix:**
```python
msg_hash = SmsBackupFileParser.calcSmsMsgHash(sms)
duplicate = msg_hash in self.msgHashes
```

---

## 4. Inconsistencies

### 4a. Mixed `click.echo()` and `print()`

**File:** `hbl_sms_query_tool.py`
**Severity:** Low

The file uses both `click.echo()` (lines 36, 50–57, 62–68) and bare `print()` (lines 40–41,
101, 106–110). In a Click application, `click.echo()` should be used consistently — it
handles encoding issues across platforms and can be captured by Click's testing utilities
(`CliRunner`).

**Fix:** Replace all `print()` calls with `click.echo()` in the CLI module.

---

### 4b. Inconsistent naming convention

**Files:** `parser/hbl_sms_parser.py` vs `sms_backup_file_parser.py`
**Severity:** Low

The CLAUDE.md documents that "methods use camelCase," but within `HBLSmsParser` there is a
mix:
- Instance variables use snake_case: `cc_txns`, `all_vendors`, `msgs_parsed`, `xml_tree`
- Methods use camelCase: `isSmsFromHBL`, `isMsgCreditCardTxn`, `extractDetailsFromTxnMsg`

Meanwhile `SmsBackupFileParser` uses camelCase for everything: `ccVendors`, `ccTxns`,
`msgsParsed`.

**Fix:** Pick one convention and apply it consistently across the entire codebase. Since
the project convention is documented as camelCase, the snake_case instance variables in
`HBLSmsParser` should be renamed to match.

---

### 4c. No `logging` module usage

**Files:** All source files
**Severity:** Medium

The entire project uses `print()` for error reporting and debug output. The `logging` module
would allow log levels (DEBUG for verbose output like duplicate detection messages, ERROR for
parse failures, INFO for summaries) and be easily disabled or redirected without modifying
code.

**Current code (scattered across files):**
```python
print(f"ERROR: unable to parse txn amount into float value: ...")
print("Original msg body:")
print(f"\t{self.msgHashes[hash]}")
```

**Fix:**
```python
import logging
logger = logging.getLogger(__name__)

logger.error("Unable to parse txn amount into float value: %s", ...)
logger.debug("Original msg body: %s", self.msgHashes[msg_hash])
```

---

## 5. Robustness & Missing Validation

### 5a. No guard on missing XML attributes

**File:** `sms_backup_file_parser.py:109-128`
**Severity:** Medium

If an `<sms>` element is missing the `address` attribute, the code raises a `KeyError`. A
`.get()` with a guard would be safer, especially since the XML comes from external backups
that may have varying formats.

**Current code:**
```python
if HBLSmsParser.isSmsFromHBL(child) ...
# isSmsFromHBL does: sms.attrib["address"]
```

**Fix:**
```python
address = child.attrib.get("address", "")
if not address:
    continue
```

---

### 5b. `_updateMonthlyTotals` doesn't leverage `defaultdict`

**File:** `hbl_sms_query_tool.py:74-87`
**Severity:** Low

`txnsPerMonth` uses `defaultdict(int)` (line 92), but `monthlySpendingTotals` is a plain
`dict` with manual key-existence checking. This creates unnecessary branching.

**Current code:**
```python
def _updateMonthlyTotals(txn, monthlyTotals):
    monthKey = txn.date.strftime("%Y_%m")
    currencyKey = txn.amountTuple.currency.lower()

    if monthKey in monthlyTotals:
        monthlyTotals[monthKey][currencyKey] += txn.amountTuple.amount
    else:
        monthlyTotals[monthKey] = {
            Currency.CAD.value.lower(): 0.00,
            Currency.PKR.value.lower(): 0.00,
            Currency.USD.value.lower(): 0.00
        }
        monthlyTotals[monthKey][currencyKey] += txn.amountTuple.amount
```

**Fix:**
```python
def _makeEmptyMonthBucket():
    return {c.value.lower(): 0.0 for c in Currency}

monthlySpendingTotals = defaultdict(_makeEmptyMonthBucket)

def _updateMonthlyTotals(txn, monthlyTotals):
    monthKey = txn.date.strftime("%Y_%m")
    currencyKey = txn.amountTuple.currency.lower()
    monthlyTotals[monthKey][currencyKey] += txn.amountTuple.amount
```

---

### 5c. Narrow exception handling in CLI entry point

**File:** `hbl_sms_query_tool.py:42-45`
**Severity:** Medium

The `try/except` in `cli()` only catches `PermissionError`. Other likely exceptions go
unhandled:
- `ET.ParseError` from a corrupt/malformed XML file
- `KeyError` from a missing `count` attribute on the root element
- `FileNotFoundError` in race conditions

**Current code:**
```python
try:
    smsParser = SmsBackupFileParser()
    ...
except PermissionError as e:
    raise click.BadParameter(...)
```

**Fix:**
```python
try:
    smsParser = SmsBackupFileParser()
    ...
except PermissionError:
    raise click.BadParameter(f"Permission denied: {filepath}")
except ET.ParseError:
    raise click.BadParameter(f"Invalid or corrupt XML file: {filepath}")
except KeyError as e:
    raise click.BadParameter(f"Malformed SMS backup file (missing attribute: {e}): {filepath}")
```

---

## 6. Test Coverage Gaps

### 6a. No tests for core parsing logic

**File:** `tests/test_hbl_sms_parser.py`
**Severity:** **High**

`extractDetailsFromTxnMsg`, `_extractCurrencyAndAmount`, and `_convertToDateTime` — the most
complex and error-prone methods in the codebase — have zero test coverage. These methods
contain regex matching, float parsing, and date parsing, all of which are common sources of
bugs.

**Recommended tests:**
- Valid PKR transaction message -> correct CreditCardTxnDC fields
- Valid USD transaction message -> correct currency detection
- Transaction with comma-separated amount (e.g., "25,170.49") -> correct float
- Transaction with date in various months -> correct datetime
- Malformed amount string -> graceful error handling
- Malformed date string -> graceful error handling

---

### 6b. No tests for `SmsBackupFileParser`

**File:** `tests/` (missing)
**Severity:** **High**

The orchestrator class, including its duplicate detection logic (`_isSmsDuplicate`,
`calcSmsMsgHash`) and bank routing in `parseMessages()`, has no test coverage at all.

**Recommended tests:**
- Two identical SMS messages -> second is detected as duplicate
- Two different SMS messages -> neither flagged as duplicate
- Messages routed to correct bank counters by short code
- MMS messages are skipped
- Unknown short codes counted as "OTHER"

---

### 6c. No edge case tests

**File:** `tests/` (missing)
**Severity:** Medium

No tests exist for:
- Malformed SMS bodies (missing fields, extra whitespace, truncated messages)
- Missing XML attributes (`address`, `body`, `date`)
- Boundary dates (month transitions, year boundaries, leap years)
- Multi-currency handling (USD, CAD amounts in HBL messages)
- Empty XML backup files (zero messages)

---

## Summary

| #   | Category    | File(s)                          | Severity   |
|-----|-------------|----------------------------------|------------|
| 1a  | Perf        | `hbl_sms_parser.py:49`           | Medium     |
| 1b  | Perf        | `sms_backup_file_parser.py:37`   | Low        |
| 1c  | Perf        | `sms_backup_file_parser.py:109`  | Medium     |
| 1d  | Perf        | `sms_backup_file_parser.py:61`   | Medium     |
| 1e  | Perf        | `cc_txn.py:15`                   | Low        |
| 2a  | Py 3.12-14  | `cc_txn.py:15`                   | Low        |
| 2b  | Py 3.12-14  | `cc_txn.py:12`                   | Low        |
| 2c  | Py 3.12-14  | `sms_backup_file_parser.py:94`   | Low        |
| 2d  | Py 3.12-14  | all files                        | Low        |
| 2e  | Py 3.12-14  | `cc_txn.py:33`                   | Low        |
| 3a  | Bug         | `sms_backup_file_parser.py:40`   | **High**   |
| 3b  | Correctness | multiple                         | Medium     |
| 3c  | Design      | `hbl_sms_query_tool.py:14`       | Medium     |
| 3d  | Cleanup     | `hbl_sms_query_tool.py:4`        | Low        |
| 3e  | Cleanup     | `common.py` / `cc_txn.py`        | Low        |
| 3f  | Cleanup     | `common.py:18`                   | Low        |
| 3g  | Cleanup     | `hbl_sms_parser.py:119`          | Medium     |
| 3h  | Cleanup     | `sms_backup_file_parser.py:68`   | Low        |
| 4a  | Consistency | `hbl_sms_query_tool.py`          | Low        |
| 4b  | Consistency | `hbl_sms_parser.py`              | Low        |
| 4c  | Design      | all files                        | Medium     |
| 5a  | Robustness  | `sms_backup_file_parser.py`      | Medium     |
| 5b  | Efficiency  | `hbl_sms_query_tool.py:74`       | Low        |
| 5c  | Robustness  | `hbl_sms_query_tool.py:42`       | Medium     |
| 6a  | Testing     | `tests/`                         | **High**   |
| 6b  | Testing     | `tests/`                         | **High**   |
| 6c  | Testing     | `tests/`                         | Medium     |

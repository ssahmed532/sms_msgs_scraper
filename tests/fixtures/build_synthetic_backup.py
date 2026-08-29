"""Build the synthetic backup fixture.

    uv run python tests/fixtures/build_synthetic_backup.py

The reference backup is one person's real financial history: it cannot be
committed, cannot go near CI, and cannot be shown to anyone. That left the only
corpus-level check in the project runnable on exactly one machine.

This file generates a stand-in. Every message here is synthetic -- invented
names, invented amounts, invented account and card numbers -- but every
*template* is real, taken from the shapes the four banks actually send. It
covers each template family, each skip path, both short codes of every bank that
has two, duplicates from a bank with and without a timestamp, an unregistered
sender, and an MMS element with nested children.

Regenerate it whenever a template is added, then re-run the synthetic corpus
test and update its expectations from the derivation printed there.

**Expectations about this fixture live in exactly one file:
`tests/test_synthetic_corpus.py`.** Derive each new number from what you added
-- three messages worth PKR 1,500, 2,500 and 3,500 move three counts and two
totals by knowable amounts -- and then confirm the code agrees, rather than
copying whatever the run printed. That is the same anti-drift rule
`scripts/verify_against_backup.py` states for the real corpus.

`tests/test_adversarial_cli.py` reads this fixture too, but asserts no count
taken from it, by design. It used to carry its own copy of the transaction
count, so a single message added here failed two files -- the second one being
about `python -O` parity and stream contracts, which had nothing to do with the
change. If an edit here makes something in that file fail, the fix is to remove
the number from it, not to update it.
"""

import xml.etree.ElementTree as ET
from pathlib import Path

OUTPUT_PATH = Path(__file__).resolve().parent / "synthetic_backup.xml"

# (sender, body, readable_date)
MESSAGES = [
    # ---------------------------------------------------------------- HBL 4250
    (
        "4250",
        "Dear Customer, Your HBL CreditCard (ending with 8526) has been charged "
        "at SYNTHETIC SUPERMART for PKR-25,170.49 on 01/Oct/2023.",
        "Oct 1, 2023 9:57:06 PM",
    ),
    (
        "4250",
        "Dear Customer, Your HBL CreditCard (ending with 8526) has been charged "
        "at SYNTHETIC ONLINE STORE for USD-4.02 on 02/Oct/2023.",
        "Oct 2, 2023 10:01:00 AM",
    ),
    (
        # ungrouped thousands: this shape used to abort the entire run
        "4250",
        "Dear Customer, Your HBL CreditCard (ending with 8526) has been charged "
        "at SYNTHETIC FUEL STOP for PKR-25170.49 on 03/Oct/2023.",
        "Oct 3, 2023 11:11:11 AM",
    ),
    (
        # non-txn on a bank short code: counted, never parsed
        "4250",
        "648975 is your One Time Password (OTP) for the internet transaction on "
        "HBL Card ending with 0077. This OTP is valid for 10 mins.",
        "Oct 3, 2023 12:00:00 PM",
    ),
    (
        # exact repeat of the first message: DUP, and ambiguous because HBL
        # alerts carry a date but no time of day
        "4250",
        "Dear Customer, Your HBL CreditCard (ending with 8526) has been charged "
        "at SYNTHETIC SUPERMART for PKR-25,170.49 on 01/Oct/2023.",
        "Oct 1, 2023 9:58:00 PM",
    ),
    (
        # one merchant, spelled four ways across this fixture. Here with a
        # station number; on 14250 with a city suffix as well; on SCB 7220 with
        # that city glued straight on; on SCB 9220 truncated mid-word. Nothing
        # but an alias table can tell that these are one merchant.
        "4250",
        "Dear Customer, Your HBL CreditCard (ending with 8526) has been charged "
        "at SYNTHETIC SERVICE STATION 7 for PKR-1,500.00 on 04/Oct/2023.",
        "Oct 4, 2023 8:00:00 AM",
    ),
    # --------------------------------------------------------------- HBL 14250
    (
        # the same merchant again, this time with a trailing city and country
        "14250",
        "Dear Customer, Your HBL CreditCard (ending with 8526) has been charged "
        "at SYNTHETIC SERVICE STATION 7 Karachi PAK for PKR-2,500.00 on "
        "05/Oct/2023.",
        "Oct 5, 2023 8:30:00 AM",
    ),
    (
        # the short code HBL migrated to in January 2025
        "14250",
        "Dear Customer, Your HBL CreditCard (ending with 8526) has been charged "
        "at SYNTHETIC PHARMACY for PKR-1,000.00 on 20/Jan/2025.",
        "Jan 20, 2025 3:15:00 PM",
    ),
    # ---------------------------------------------------------------- FBL 8756
    (
        "8756",
        "Dear JOHN DOE, your FBL Card  has been charged for PKR 8100 on "
        "20-Sep-23 01:17:16 PM at SYNTHETIC BILL PAY     KARACHI        PK.",
        "Sep 20, 2023 1:17:16 PM",
    ),
    (
        "8756",
        "Dear JOHN DOE, your FBL Card  has been charged for USD 39.99 on "
        "21-Sep-23 02:00:00 PM at SYNTHETIC WEB SHOP     SEATTLE       US.",
        "Sep 21, 2023 2:00:00 PM",
    ),
    (
        "8756",
        "Dear JOHN DOE, your FBL Card  has been charged for CAD 12.50 on "
        "22-Sep-23 09:05:00 AM at SYNTHETIC COFFEE       TORONTO       CA.",
        "Sep 22, 2023 9:05:00 AM",
    ),
    (
        # one decimal place, and the 24-hour numeric date format
        "8756",
        "Dear JOHN DOE, your FBL Card  has been charged for PKR 18298.9 on "
        "04-02-2024 21:11:41 at SYNTHETIC DEPT STORE   KARACHI        PK.",
        "Feb 4, 2024 9:11:41 PM",
    ),
    (
        # comma-grouped amount: FBL never sends one, so this is a template
        # change. Carries the signal, fails extraction -> FBL_SKIPPED
        "8756",
        "Dear JOHN DOE, your FBL Card  has been charged for PKR 25,170.49 on "
        "05-Feb-24 10:00:00 AM at SYNTHETIC ODD SHOP     KARACHI        PK.",
        "Feb 5, 2024 10:00:00 AM",
    ),
    (
        "8756",
        "Dear JOHN DOE, Payment of PKR 50,000.00 has been received on your "
        "FBL Card. Thank you for banking with Faysal Bank.",
        "Feb 6, 2024 8:00:00 AM",
    ),
    # ---------------------------------------------------------------- SCB 7220
    (
        "7220",
        "Dear Client, PKR 12,450.90 have been paid at SYNTHETIC SERVICE "
        "STATIONKarachi PAK on 29-09-23 using Credit Card no 5452xxxxxxxx1280. "
        "Avail Limit PKR59563.45. SCBPL",
        "Sep 29, 2023 6:12:00 PM",
    ),
    (
        # BIN-only mask: carries no last four at all
        "7220",
        "Dear Client, PKR 100.00 have been paid at SYNTHETIC BOOKSHOP Karachi "
        "PAK on 30-09-23 using Credit Card no 5495. Avail Limit PKR59463.45. "
        "SCBPL",
        "Sep 30, 2023 1:00:00 PM",
    ),
    (
        # truncated mid-body -> SCB_SKIPPED
        "7220",
        "Dear Client, PKR 281.00 have been paid at SYNTHETIC NATURAL STORE ",
        "Oct 1, 2023 2:00:00 PM",
    ),
    (
        # foreign-currency txn carrying no PKR amount -> SCB_SKIPPED
        "7220",
        "Dear Client, PKR .00 have been paid at SYNTHETIC OVERSEAS CAN OTTAWA "
        "CAN on 14-06-24 using Credit Card no 5495. Avail Limit PKR181962.55. "
        "SCBPL",
        "Jun 14, 2024 4:00:00 PM",
    ),
    # ---------------------------------------------------------------- SCB 9220
    (
        # the second Standard Chartered short code, undeclared until 2.0.0
        "9220",
        "Dear Client, PKR 500.00 have been paid at SYNTHETIC HARDWARE Karachi "
        "PAK on 07-09-25 using Credit Card no 5452xxxxxxxx1280. Avail Limit "
        "PKR40000.00. SCBPL",
        "Sep 7, 2025 5:00:00 PM",
    ),
    (
        # the same merchant a fourth time, truncated mid-word by the issuer --
        # the shape no suffix rule could ever repair
        "9220",
        "Dear Client, PKR 3,500.00 have been paid at SYNTHETIC SERVICE STAT on "
        "06-10-23 using Credit Card no 5452xxxxxxxx1280. Avail Limit "
        "PKR40000.00. SCBPL",
        "Oct 6, 2023 9:00:00 AM",
    ),
    # --------------------------------------------------------------- MEZN 8079
    (
        "8079",
        "PKR 3,251.00 charged at SYNTHETIC STUDIO for card used, from A/C "
        "xxxxxx5602 (SYNTHETIC BRANCH KHI) on 11-Dec-23 at 14:58 "
        "Balance: 13,171,500.59",
        "Dec 11, 2023 2:58:00 PM",
    ),
    (
        "8079",
        "PKR 35,000.00 cash withdrawn from SYNTHETIC ATM KHI from A/C "
        "xxxxxx5602 SYNTHETIC BRANCH KHI on 19-Sep-23 at 19:42 "
        "Balance: PKR 13,776,380.62",
        "Sep 19, 2023 7:42:00 PM",
    ),
    (
        "8079",
        "PKR 1,000.00 is debited as SYNTHETIC WALLET - Load from your A/C "
        "xxxxxx5602 of SYNTHETIC BRANCH KHI on 20-Sep-23 at 09:45 "
        "Bal: PKR 13,775,380.62",
        "Sep 20, 2023 9:45:00 AM",
    ),
    (
        "8079",
        "PKR 63,100.00 sent to SYNTHETIC SCHOOL SCB-5504000000015496 from your "
        "A/C xxxxxx5602 of SYNTHETIC BRANCH KHI on 19-Sep-23 at 10:01 "
        "Fee: Rs.63.10 Bal: PKR 13,874,889.12",
        "Sep 19, 2023 10:01:00 AM",
    ),
    (
        # uppercase transfer, no account clause at all
        "8079",
        "PKR 5,000.00 SENT TO JOHN DOE MBL- 01130100000267 on 19-Sep-23 at "
        "11:36 Bal: PKR 13,811,380.62",
        "Sep 19, 2023 11:36:00 AM",
    ),
    (
        # drifted template: trips the signal, matches nothing -> MEZN_SKIPPED
        "8079",
        "PKR 5,000.00 sent to JOHN DOE via SomeNewChannel ref 12345 dated "
        "19/Sep/2023 20:15",
        "Sep 19, 2023 8:15:00 PM",
    ),
    (
        # a credit, which must not be read as a debit
        "8079",
        "PKR 250.00 is credited as Rev of Late Pymnt chrg in your A/C "
        "xxxxxx5602 of SYNTHETIC BRANCH KHI on 14-Nov-23 at 17:43. "
        "Bal: PKR 13,312,669.35",
        "Nov 14, 2023 5:43:00 PM",
    ),
    # --------------------------------------------------------------- MEZN 9779
    (
        # the second Meezan short code, and the 4-digit-year date format
        "9779",
        "PKR 80,000.00 cash withdrawn from SYNTHETIC ATM DHA from A/C xxx5602 "
        "SYNTHETIC BRANCH KHI on 24-Apr-2025 at 16:14 TID:026128",
        "Apr 24, 2025 4:14:00 PM",
    ),
    # ------------------------------------------------------ unregistered sender
    (
        "99999",
        "Synthetic promo: buy two, get one free! Reply STOP to opt out.",
        "Oct 5, 2023 8:00:00 AM",
    ),
    (
        # exact repeat from a non-bank sender: DUP, and not ambiguous
        "99999",
        "Synthetic promo: buy two, get one free! Reply STOP to opt out.",
        "Oct 5, 2023 8:00:05 AM",
    ),
]


def build() -> ET.ElementTree:
    root = ET.Element("smses")

    for sender, body, readableDate in MESSAGES:
        sms = ET.SubElement(root, "sms")
        sms.set("protocol", "0")
        sms.set("address", sender)
        sms.set("date", "1696265826791")
        sms.set("type", "1")
        sms.set("read", "1")
        sms.set("body", body)
        sms.set("readable_date", readableDate)

    # One MMS, with the nested children a real one carries. It pins the depth
    # handling in the streaming reader: counting every end event rather than
    # only top-level ones read the reference backup's 4,719 records as 5,040
    # and called 321 nested MMS children malformed messages.
    mms = ET.SubElement(root, "mms")
    mms.set("address", "99999")
    parts = ET.SubElement(mms, "parts")
    part = ET.SubElement(parts, "part")
    part.set("ct", "text/plain")
    part.set("text", "synthetic mms part")
    addrs = ET.SubElement(mms, "addrs")
    addr = ET.SubElement(addrs, "addr")
    addr.set("address", "99999")

    # `count` covers every child element, MMS included -- which is exactly the
    # distinction the envelope accounting keeps: declared counts records, not
    # parseable messages.
    root.set("count", str(len(list(root))))

    return ET.ElementTree(root)


if __name__ == "__main__":
    tree = build()
    ET.indent(tree, space="  ")
    tree.write(OUTPUT_PATH, encoding="utf-8", xml_declaration=True)
    print(f"wrote {OUTPUT_PATH} ({len(MESSAGES)} sms + 1 mms)")

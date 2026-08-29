"""The one record shape a parser sees.

Parsers used to be handed the raw `xml.etree` element and reach into
`sms.attrib["body"]` themselves. Three things follow from that, and this type
exists to end all three:

  * A message missing `address` or `body` raised `KeyError` from inside a
    parser, aborting the run over one malformed record in a file of thousands.
    Building the record is now the single place that can fail, and it fails by
    returning `None` for the orchestrator to count.

  * The whole XML tree stayed reachable for the lifetime of the parse, which
    kept every message body -- personal financial data -- alive in memory long
    after the handful of fields anyone needed had been read out.

  * Nothing stopped a parser printing a body. It now only ever holds a body
    while extracting from it; what escapes into a diagnostic is the locator.
"""

from dataclasses import dataclass
from xml.etree.ElementTree import Element

# The tag every message we parse carries. MMS elements are skipped, and any
# other tag is counted as invalid rather than assumed to be a message.
SMS_TAG = "sms"
MMS_TAG = "mms"


@dataclass(frozen=True, slots=True)
class SmsRecord:
    """One SMS message, reduced to the fields this tool reads.

    `index` is the element's position among the root's children, so a
    diagnostic or a duplicate can be traced back to a specific record in the
    file without retaining the record itself.
    """

    sender: str
    body: str
    receivedAt: str
    index: int

    @classmethod
    def fromElement(cls, element: Element, index: int) -> SmsRecord | None:
        """Build a record from an `<sms>` element, or None if it is unusable.

        A record needs a sender and a body to be routed or hashed at all. One
        missing either is not a message this tool can say anything about, so it
        is rejected here -- once -- rather than by whichever parser happens to
        touch it first.
        """
        sender = element.attrib.get("address")
        body = element.attrib.get("body")

        if sender is None or body is None:
            return None

        return cls(
            sender=sender,
            body=body,
            receivedAt=element.attrib.get("readable_date", "?"),
            index=index,
        )

    @property
    def strippedBody(self) -> str:
        """The body with leading and trailing whitespace removed.

        This is the form the duplicate identity is computed over.
        """
        return self.body.strip()

    def locator(self) -> str:
        """A short, body-free description of where this message is.

        Everything a person needs to find the message in their own backup, and
        nothing that would reproduce its contents in a log or a bug report.
        """
        return f"sender {self.sender}, received {self.receivedAt}"

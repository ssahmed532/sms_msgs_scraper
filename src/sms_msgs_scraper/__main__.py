"""Entry point for `python -m sms_msgs_scraper`.

The installed console script `sms-txn` is the normal way to run this. This
exists so the tool is also runnable straight out of a source checkout, without
depending on a script path that moves whenever the layout does.
"""

from sms_msgs_scraper.sms_txn_query_tool import main

if __name__ == "__main__":
    main()

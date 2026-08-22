import unittest

from hbl_sms_query_tool import cli


class TestCliCommandRegistration(unittest.TestCase):

    def test_subcommand_names_use_underscores(self):
        """Test method to verify that the three documented subcommand names
        are registered verbatim. Click >=8.2 derives command names by
        replacing underscores with dashes, so without the explicit name
        string in @cli.command(...) the documented list_all_vendors
        invocation would silently become list-all-vendors.
        """
        self.assertEqual(
            set(cli.commands),
            {"list_all_vendors", "list_all_cc_txns", "monthly_cc_spending_summary"},
        )


if __name__ == "__main__":
    # to run this script:
    #   cd /path/to/src sub-directory
    #   python -m unittest discover -s ..\tests\ -v
    #
    unittest.main()

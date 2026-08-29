"""The import graph is a contract, and this is what enforces it.

The layers below are not a preference about tidiness. Each arrow that used to
point the wrong way was load-bearing:

  * `domain/report.py` imported `cc_txn` and `debit_txn` from the package root,
    while both of those imported back down into `domain/money.py`. The domain
    depended on the root and the root depended on the domain.
  * `domain/registry.py` imported all four bank parsers, and every one of those
    imports `domain/money.py`. The pure core depended on the parsing layer that
    was supposed to be built on top of it.
  * Both `render/` modules reached up to a root-level `console_ui.py` for the
    consoles and the cell helpers they are written against.

None of that was a *module* cycle -- Python resolves modules, not packages, so
it all imported fine and no test could see it. That is precisely why it needs a
test: an architecture that only exists in a document is one commit from not
existing at all.

Ordering is by dependency, so a layer may import from itself and from anything
below it, and never from anything above:

    domain  <- the core: values, identity, diagnostics, reports. Stdlib only.
    parser  <- one module per bank, plus the registry that binds them
    render  <- consoles, tables, JSON and CSV
    app     <- the package root: the orchestrator and the CLI that compose it all
"""

import ast
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
PACKAGE = "sms_msgs_scraper"

# Low index depends on nothing above it.
LAYERS = ("domain", "parser", "render", "app")


def _layerOf(modulePath: str) -> str:
    """The layer a dotted module name belongs to.

    Anything directly under the package root is the application layer: the
    orchestrator and the CLI are the only things allowed to see everything.
    """
    parts = modulePath.split(".")
    if len(parts) > 1 and parts[1] in LAYERS:
        return parts[1]
    return "app"


def _importedModules(tree: ast.AST):
    """Every first-party module a source file imports, dotted and absolute.

    Relative imports are not handled because there are none: the package
    imports itself by absolute path throughout, which is what makes a plain
    string comparison sufficient here.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(f"{PACKAGE}."):
                    yield alias.name
        elif (
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and node.module
            and node.module.startswith(f"{PACKAGE}.")
        ):
            yield node.module


class TestImportLayering(unittest.TestCase):
    def test_no_module_imports_from_a_layer_above_its_own(self):
        violations = []

        for path in sorted(SRC_DIR.rglob("*.py")):
            modulePath = ".".join(path.relative_to(SRC_DIR).with_suffix("").parts)
            fromLayer = _layerOf(modulePath)
            tree = ast.parse(path.read_text(encoding="utf-8"))

            for imported in _importedModules(tree):
                toLayer = _layerOf(imported)
                if LAYERS.index(toLayer) > LAYERS.index(fromLayer):
                    violations.append(
                        f"{modulePath} ({fromLayer}) imports {imported} ({toLayer})"
                    )

        self.assertEqual(violations, [])

    def test_the_domain_imports_nothing_outside_itself(self):
        """The strongest form of the rule, stated separately so it fails loudly.

        `domain` is the bottom layer, so the general check above already covers
        it -- but this is the property that the registry split was for, and a
        reader looking for it should not have to derive it from an index
        comparison.
        """
        strays = []

        for path in sorted((SRC_DIR / PACKAGE / "domain").rglob("*.py")):
            modulePath = ".".join(path.relative_to(SRC_DIR).with_suffix("").parts)
            tree = ast.parse(path.read_text(encoding="utf-8"))

            for imported in _importedModules(tree):
                if not imported.startswith(f"{PACKAGE}.domain."):
                    strays.append(f"{modulePath} imports {imported}")

        self.assertEqual(strays, [])

    def test_the_package_root_holds_only_composition_modules(self):
        """Nothing new should land at the root without a deliberate decision.

        Every module that used to sit here turned out to belong in a
        subpackage, and each one only got there by being added without anyone
        asking which layer it was in.
        """
        atRoot = sorted(
            path.name
            for path in (SRC_DIR / PACKAGE).glob("*.py")
        )

        self.assertEqual(
            atRoot,
            [
                "__init__.py",
                "__main__.py",
                "sms_backup_file_parser.py",
                "sms_txn_query_tool.py",
            ],
        )


if __name__ == "__main__":
    unittest.main()

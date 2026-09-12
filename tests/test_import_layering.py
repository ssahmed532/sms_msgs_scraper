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

**What the checker itself is held to.** A mutation pass on an earlier version
of this file found six ways to import upward that it could not see: `from
sms_msgs_scraper import render` (the package name was filtered out), a relative
import (`from ..render import tables`), a new subpackage (it defaulted to the
most permissive layer), a `.pyi` stub, a stray file in `src/` outside the
package, and `import rich` inside `domain/`. Each of those is now a case in
`TestTheCheckerItself`, on a source snippet rather than on the tree, so the
checker cannot quietly lose one of them again.
"""

import ast
import sys
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
PACKAGE = "sms_msgs_scraper"

# Low index depends on nothing above it.
LAYERS = ("domain", "parser", "render", "app")

# The layers that may import nothing but the standard library and the package
# itself. Rich and click are renderers and a CLI framework; a parser that could
# reach either could print, and a domain value that could would no longer be
# the pure core everything else is built on. Checkable, and checked here,
# rather than remembered.
STDLIB_ONLY_LAYERS = ("domain", "parser")

# Stubs are imports too. A `.pyi` next to a module is read by the type checker
# in place of the module, so an upward import in one is an upward import.
SOURCE_SUFFIXES = (".py", ".pyi")


class LayerError(Exception):
    """A module that belongs to no declared layer.

    Raised rather than defaulted: a new subpackage used to fall through to
    `app`, the layer that may import everything, which is the one answer that
    could never produce a violation.
    """


def _sourceFiles(root: Path = SRC_DIR):
    return sorted(path for path in root.rglob("*") if path.suffix in SOURCE_SUFFIXES)


def _moduleNameFor(path: Path, root: Path = SRC_DIR) -> str:
    return ".".join(path.relative_to(root).with_suffix("").parts)


def _layerOf(modulePath: str) -> str:
    """The layer a dotted module name belongs to.

    Anything directly under the package root is the application layer: the
    orchestrator and the CLI are the only things allowed to see everything. A
    name inside a subpackage that is not a declared layer is an error, not an
    `app` module.
    """
    parts = modulePath.split(".")

    if len(parts) > 1 and parts[1] in LAYERS:
        return parts[1]

    inUndeclaredSubpackage = len(parts) > 2 or (
        len(parts) == 2 and (SRC_DIR / PACKAGE / parts[1]).is_dir()
    )
    if inUndeclaredSubpackage:
        raise LayerError(
            f"{modulePath} is inside a subpackage that is not a declared layer; "
            f"add it to LAYERS at the position its dependencies allow"
        )

    return "app"


def _importedModules(tree: ast.AST):
    """Every first-party module a source file imports, dotted and absolute.

    `from sms_msgs_scraper import render` names a module too -- one per
    imported name -- and is yielded as `sms_msgs_scraper.render`, so importing
    a layer through the package root is seen the same as importing it by path.
    Relative imports are reported separately by `_relativeImports`; there are
    none in the package, and one would be a violation in itself.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == PACKAGE or alias.name.startswith(f"{PACKAGE}."):
                    yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module == PACKAGE:
                for alias in node.names:
                    yield f"{PACKAGE}.{alias.name}"
            elif node.module.startswith(f"{PACKAGE}."):
                yield node.module


def _relativeImports(tree: ast.AST):
    """The line of every relative import. The package has none, deliberately:
    a plain string comparison on absolute names is what keeps this checker
    simple enough to trust, and a relative import would slip past it."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level > 0:
            yield node.lineno


def _topLevelImports(tree: ast.AST):
    """The top-level name of every module a source file imports, first- and
    third-party alike -- `rich` for `from rich.console import Console`."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module.split(".")[0]


def _parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


class TestImportLayering(unittest.TestCase):
    def test_no_module_imports_from_a_layer_above_its_own(self):
        violations = []

        for path in _sourceFiles():
            modulePath = _moduleNameFor(path)
            fromLayer = _layerOf(modulePath)
            tree = _parse(path)

            for lineno in _relativeImports(tree):
                violations.append(f"{modulePath} uses a relative import at line {lineno}")

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

        for path in _sourceFiles(SRC_DIR / PACKAGE / "domain"):
            modulePath = _moduleNameFor(path)

            for imported in _importedModules(_parse(path)):
                if not imported.startswith(f"{PACKAGE}.domain."):
                    strays.append(f"{modulePath} imports {imported}")

        self.assertEqual(strays, [])

    def test_the_domain_and_parser_layers_import_only_the_stdlib_and_themselves(self):
        """`grep -rl rich src/sms_msgs_scraper/parser/` finding nothing is a
        documented property. This is that grep, for every third-party name at
        once, and it is what makes "a parser cannot print" true rather than
        currently the case.
        """
        strays = []

        for layer in STDLIB_ONLY_LAYERS:
            for path in _sourceFiles(SRC_DIR / PACKAGE / layer):
                modulePath = _moduleNameFor(path)

                for name in _topLevelImports(_parse(path)):
                    if name != PACKAGE and name not in sys.stdlib_module_names:
                        strays.append(f"{modulePath} imports third-party {name}")

        self.assertEqual(strays, [])

    def test_the_package_root_holds_only_composition_modules(self):
        """Nothing new should land at the root without a deliberate decision.

        Every module that used to sit here turned out to belong in a
        subpackage, and each one only got there by being added without anyone
        asking which layer it was in.
        """
        atRoot = sorted(
            path.name
            for path in (SRC_DIR / PACKAGE).iterdir()
            if path.suffix in SOURCE_SUFFIXES
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

    def test_src_holds_the_package_and_nothing_else(self):
        """A module dropped straight into `src/` is importable from the tests
        by accident and belongs to no layer at all."""
        entries = [
            path.name for path in SRC_DIR.iterdir() if not path.name.startswith(".")
        ]

        self.assertEqual(entries, [PACKAGE])

    def test_every_subpackage_is_a_declared_layer(self):
        """The lookup raises on an undeclared subpackage; this is the same rule
        stated over the tree, so the failure names the directory rather than
        the first module inside it."""
        subpackages = sorted(
            path.name
            for path in (SRC_DIR / PACKAGE).iterdir()
            if path.is_dir() and path.name not in ("__pycache__", "data")
        )

        self.assertEqual(subpackages, sorted(layer for layer in LAYERS if layer != "app"))


class TestTheCheckerItself(unittest.TestCase):
    """Each case here is an upward import the previous checker could not see."""

    def test_importing_a_layer_through_the_package_root_is_seen(self):
        tree = ast.parse("from sms_msgs_scraper import render, domain")

        self.assertEqual(
            list(_importedModules(tree)),
            ["sms_msgs_scraper.render", "sms_msgs_scraper.domain"],
        )
        self.assertEqual(_layerOf("sms_msgs_scraper.render"), "render")

    def test_importing_the_package_itself_is_the_application_layer(self):
        tree = ast.parse("import sms_msgs_scraper")

        self.assertEqual(list(_importedModules(tree)), ["sms_msgs_scraper"])
        self.assertEqual(_layerOf("sms_msgs_scraper"), "app")

    def test_a_name_from_the_package_root_is_the_application_layer(self):
        """`from sms_msgs_scraper import __version__` is what the CLI does."""
        self.assertEqual(_layerOf("sms_msgs_scraper.__version__"), "app")

    def test_a_relative_import_is_reported(self):
        tree = ast.parse("from ..render import tables\nfrom . import money")

        self.assertEqual(list(_relativeImports(tree)), [1, 2])
        self.assertEqual(list(_importedModules(tree)), [])

    def test_an_undeclared_subpackage_is_an_error_not_the_app_layer(self):
        with self.assertRaises(LayerError):
            _layerOf("sms_msgs_scraper.newpkg.module")

    def test_the_declared_layers_resolve(self):
        self.assertEqual(_layerOf("sms_msgs_scraper.domain.money"), "domain")
        self.assertEqual(_layerOf("sms_msgs_scraper.parser.registry"), "parser")
        self.assertEqual(_layerOf("sms_msgs_scraper.render.tables"), "render")
        self.assertEqual(_layerOf("sms_msgs_scraper.sms_txn_query_tool"), "app")

    def test_third_party_imports_are_named_by_their_top_level_package(self):
        tree = ast.parse(
            "import rich.console\nfrom click import echo\nimport re\n"
            "from sms_msgs_scraper.domain import money"
        )

        names = set(_topLevelImports(tree))

        self.assertEqual(names, {"rich", "click", "re", "sms_msgs_scraper"})
        self.assertNotIn("rich", sys.stdlib_module_names)
        self.assertNotIn("click", sys.stdlib_module_names)
        self.assertIn("re", sys.stdlib_module_names)

    def test_stubs_are_source_files_and_bytecode_is_not(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpDir:
            root = Path(tmpDir)
            (root / "pkg").mkdir()
            (root / "pkg" / "a.py").write_text("", encoding="utf-8")
            (root / "pkg" / "b.pyi").write_text("", encoding="utf-8")
            (root / "pkg" / "c.pyc").write_bytes(b"")

            found = [_moduleNameFor(path, root) for path in _sourceFiles(root)]

        self.assertEqual(found, ["pkg.a", "pkg.b"])


if __name__ == "__main__":
    unittest.main()

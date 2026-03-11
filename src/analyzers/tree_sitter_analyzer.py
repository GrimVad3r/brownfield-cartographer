"""
src/analyzers/tree_sitter_analyzer.py
───────────────────────────────────────
Multi-language AST parser using tree-sitter.

LanguageRouter selects the correct grammar based on file extension.
analyse_file() returns a partial ModuleNode with structural data:
- imports
- exported / public symbols
- function signatures
- complexity proxy (function + class count)

Graceful degradation: any parse failure is logged and a ModuleNode
with parse_error set is returned — never raises.

Branch: feature/03-surveyor-agent
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from src.models.nodes import FunctionNode, Language, ModuleNode
from src.utils.logging_config import get_logger
from src.utils.security import is_safe_file

logger = get_logger(__name__)

# ── Language routing ──────────────────────────────────────────────────────────

_EXT_TO_LANGUAGE: dict[str, Language] = {
    ".py":    Language.PYTHON,
    ".sql":   Language.SQL,
    ".yaml":  Language.YAML,
    ".yml":   Language.YAML,
    ".js":    Language.JAVASCRIPT,
    ".mjs":   Language.JAVASCRIPT,
    ".ts":    Language.TYPESCRIPT,
    ".tsx":   Language.TYPESCRIPT,
    ".ipynb": Language.NOTEBOOK,
}


def route_language(path: Path) -> Language:
    """Return the Language enum for *path* based on its extension."""
    return _EXT_TO_LANGUAGE.get(path.suffix.lower(), Language.OTHER)


# ── Python-specific regex fallback ────────────────────────────────────────────
# tree-sitter grammars may not always be available in all deployment environments.
# We implement a pure-regex fallback so the tool always produces *some* output.

_PY_IMPORT_RE   = re.compile(r"^\s*(?:import|from)\s+([\w.]+)", re.MULTILINE)
_PY_DEF_RE      = re.compile(r"^(?:    )*def\s+([A-Za-z_]\w*)\s*\(([^)]*)\)", re.MULTILINE)
_PY_CLASS_RE    = re.compile(r"^class\s+([A-Za-z_]\w*)", re.MULTILINE)
_PY_COMMENT_RE  = re.compile(r"^\s*#", re.MULTILINE)

_JS_IMPORT_RE   = re.compile(r"^\s*import\s+.*?from\s+['\"]([^'\"]+)['\"]", re.MULTILINE)
_JS_REQUIRE_RE  = re.compile(r"require\(\s*['\"]([^'\"]+)['\"]\s*\)")
_JS_EXPORT_FN_RE = re.compile(r"export\s+function\s+([A-Za-z_]\w*)")
_JS_EXPORT_CLASS_RE = re.compile(r"export\s+class\s+([A-Za-z_]\w*)")
_JS_EXPORT_CONST_RE = re.compile(r"export\s+const\s+([A-Za-z_]\w*)")


def _analyse_python_fallback(source: str, module_node: ModuleNode) -> None:
    """Populate *module_node* using regex when tree-sitter is unavailable."""
    lines = source.splitlines()
    module_node.lines_of_code = len(lines)

    # Imports
    module_node.imports = list(
        {m.group(1) for m in _PY_IMPORT_RE.finditer(source)}
    )

    # Exported symbols (public functions and classes)
    functions  = [m.group(1) for m in _PY_DEF_RE.finditer(source)]
    classes    = [m.group(1) for m in _PY_CLASS_RE.finditer(source)]
    module_node.exported_symbols = [
        s for s in (functions + classes) if not s.startswith("_")
    ]

    # Comment ratio
    comment_lines = sum(1 for ln in lines if _PY_COMMENT_RE.match(ln))
    module_node.comment_ratio = (
        comment_lines / len(lines) if lines else 0.0
    )

    # Complexity proxy: number of functions + classes
    module_node.complexity_score = float(len(functions) + len(classes))


def _analyse_sql_fallback(source: str, module_node: ModuleNode) -> None:
    """Basic SQL analysis — table name extraction via regex."""
    lines = source.splitlines()
    module_node.lines_of_code = len(lines)
    table_re = re.compile(
        r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+([`\"\[]?[\w.]+[`\"\]]?)",
        re.IGNORECASE,
    )
    module_node.imports = list({m.group(1).strip('`"[]') for m in table_re.finditer(source)})


def _analyse_yaml_fallback(source: str, module_node: ModuleNode) -> None:
    lines = source.splitlines()
    module_node.lines_of_code = len(lines)


def _analyse_js_ts_fallback(source: str, module_node: ModuleNode) -> None:
    """Basic JS/TS analysis â€” import/export extraction via regex."""
    lines = source.splitlines()
    module_node.lines_of_code = len(lines)
    imports = set()
    imports.update(m.group(1) for m in _JS_IMPORT_RE.finditer(source))
    imports.update(m.group(1) for m in _JS_REQUIRE_RE.finditer(source))
    module_node.imports = list(imports)

    exported: list[str] = []
    exported.extend(m.group(1) for m in _JS_EXPORT_FN_RE.finditer(source))
    exported.extend(m.group(1) for m in _JS_EXPORT_CLASS_RE.finditer(source))
    exported.extend(m.group(1) for m in _JS_EXPORT_CONST_RE.finditer(source))
    module_node.exported_symbols = [s for s in exported if not s.startswith("_")]
    comment_lines = sum(1 for ln in lines if ln.strip().startswith("//") or ln.strip().startswith("/*"))
    module_node.comment_ratio = comment_lines / len(lines) if lines else 0.0


# ── Tree-sitter powered analysis (optional, gracefully degrades) ──────────────

def _try_tree_sitter_python(source: str, module_node: ModuleNode) -> bool:
    """
    Attempt tree-sitter Python analysis.
    Returns True on success, False if tree-sitter is unavailable.
    """
    try:
        import tree_sitter_python as tspython  # type: ignore[import]
        from tree_sitter import Language as TSLanguage, Parser  # type: ignore[import]

        PY_LANGUAGE = TSLanguage(tspython.language())
        parser = Parser(PY_LANGUAGE)
        tree = parser.parse(source.encode("utf-8", errors="replace"))

        imports: list[str]  = []
        symbols: list[str]  = []
        fn_count            = 0
        class_count         = 0

        def walk(node: Any) -> None:
            nonlocal fn_count, class_count
            ntype = node.type

            if ntype == "import_statement":
                for child in node.children:
                    if child.type == "dotted_name":
                        imports.append(child.text.decode("utf-8", errors="replace"))

            elif ntype == "import_from_statement":
                for child in node.children:
                    if child.type == "dotted_name":
                        imports.append(child.text.decode("utf-8", errors="replace"))
                        break

            elif ntype == "function_definition":
                fn_count += 1
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = name_node.text.decode("utf-8", errors="replace")
                    if not name.startswith("_"):
                        symbols.append(name)

            elif ntype == "class_definition":
                class_count += 1
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = name_node.text.decode("utf-8", errors="replace")
                    if not name.startswith("_"):
                        symbols.append(name)

            for child in node.children:
                walk(child)

        walk(tree.root_node)

        lines = source.splitlines()
        module_node.lines_of_code   = len(lines)
        module_node.imports         = list(set(imports))
        module_node.exported_symbols = symbols
        module_node.complexity_score = float(fn_count + class_count)

        comment_lines = sum(1 for ln in lines if ln.strip().startswith("#"))
        module_node.comment_ratio = comment_lines / len(lines) if lines else 0.0

        return True

    except ImportError:
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning("tree_sitter_python_error", error=str(exc))
        return False


def _try_tree_sitter_sql(source: str, module_node: ModuleNode) -> bool:
    """Attempt tree-sitter SQL parse; returns True on success."""
    try:
        import tree_sitter_sql as tssql  # type: ignore[import]
        from tree_sitter import Language as TSLanguage, Parser  # type: ignore[import]

        SQL_LANGUAGE = TSLanguage(tssql.language())
        parser = Parser(SQL_LANGUAGE)
        parser.parse(source.encode('utf-8', errors='replace'))
        module_node.lines_of_code = len(source.splitlines())
        return True
    except ImportError:
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning('tree_sitter_sql_error', error=str(exc))
        return False


def _try_tree_sitter_yaml(source: str, module_node: ModuleNode) -> bool:
    """Attempt tree-sitter YAML parse; returns True on success."""
    try:
        import tree_sitter_yaml as tsyaml  # type: ignore[import]
        from tree_sitter import Language as TSLanguage, Parser  # type: ignore[import]

        YAML_LANGUAGE = TSLanguage(tsyaml.language())
        parser = Parser(YAML_LANGUAGE)
        parser.parse(source.encode('utf-8', errors='replace'))
        module_node.lines_of_code = len(source.splitlines())
        return True
    except ImportError:
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning('tree_sitter_yaml_error', error=str(exc))
        return False


def _try_tree_sitter_javascript(source: str, module_node: ModuleNode) -> bool:
    """Attempt tree-sitter JS parse for imports/exports."""
    try:
        import tree_sitter_javascript as tsjs  # type: ignore[import]
        from tree_sitter import Language as TSLanguage, Parser  # type: ignore[import]

        JS_LANGUAGE = TSLanguage(tsjs.language())
        parser = Parser(JS_LANGUAGE)
        tree = parser.parse(source.encode('utf-8', errors='replace'))
        imports: list[str] = []
        exports: list[str] = []

        def walk(node: Any) -> None:
            if node.type == 'import_statement':
                for child in node.children:
                    if child.type == 'string':
                        imports.append(child.text.decode('utf-8', errors='replace').strip("\"'"))
            if node.type == 'export_statement':
                for child in node.children:
                    if child.type in {'function_declaration', 'class_declaration'}:
                        name = child.child_by_field_name('name')
                        if name:
                            exports.append(name.text.decode('utf-8', errors='replace'))
            for child in node.children:
                walk(child)

        walk(tree.root_node)
        module_node.lines_of_code = len(source.splitlines())
        module_node.imports = list(set(imports))
        module_node.exported_symbols = [e for e in exports if not e.startswith('_')]
        return True
    except ImportError:
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning('tree_sitter_js_error', error=str(exc))
        return False


def _try_tree_sitter_typescript(source: str, module_node: ModuleNode) -> bool:
    """Attempt tree-sitter TS parse for imports/exports."""
    try:
        import tree_sitter_typescript as tsts  # type: ignore[import]
        from tree_sitter import Language as TSLanguage, Parser  # type: ignore[import]

        TS_LANGUAGE = TSLanguage(tsts.language_typescript())
        parser = Parser(TS_LANGUAGE)
        tree = parser.parse(source.encode('utf-8', errors='replace'))
        imports: list[str] = []
        exports: list[str] = []

        def walk(node: Any) -> None:
            if node.type == 'import_statement':
                for child in node.children:
                    if child.type == 'string':
                        imports.append(child.text.decode('utf-8', errors='replace').strip("\"'"))
            if node.type == 'export_statement':
                for child in node.children:
                    if child.type in {'function_declaration', 'class_declaration'}:
                        name = child.child_by_field_name('name')
                        if name:
                            exports.append(name.text.decode('utf-8', errors='replace'))
            for child in node.children:
                walk(child)

        walk(tree.root_node)
        module_node.lines_of_code = len(source.splitlines())
        module_node.imports = list(set(imports))
        module_node.exported_symbols = [e for e in exports if not e.startswith('_')]
        return True
    except ImportError:
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning('tree_sitter_ts_error', error=str(exc))
        return False


# ?????? Function extraction ????????????????????????????????????????????????????????????????????????????????????????????????????????????

def _module_qualifier(module_path: str) -> str:
    return module_path.replace('\\', '/').replace('/', '.').rsplit('.', 1)[0]


def _extract_python_functions_tree_sitter(source: str, module_path: str) -> list[FunctionNode]:
    """
    Extract FunctionNode entries using tree-sitter if available.
    Returns an empty list if tree-sitter is unavailable.
    """
    try:
        import tree_sitter_python as tspython  # type: ignore[import]
        from tree_sitter import Language as TSLanguage, Parser  # type: ignore[import]

        PY_LANGUAGE = TSLanguage(tspython.language())
        parser = Parser(PY_LANGUAGE)
        tree = parser.parse(source.encode('utf-8', errors='replace'))

        module_qual = _module_qualifier(module_path)
        nodes: list[FunctionNode] = []

        def walk(node: Any, current_class: str | None = None) -> None:
            ntype = node.type

            if ntype == 'class_definition':
                name_node = node.child_by_field_name('name')
                class_name = name_node.text.decode('utf-8', errors='replace') if name_node else 'UnknownClass'
                qualified = f'{module_qual}.{class_name}'
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                nodes.append(FunctionNode(
                    node_id=f'{module_path}:{class_name}',
                    qualified_name=qualified,
                    parent_module=module_path,
                    signature=f'class {class_name}',
                    is_public_api=not class_name.startswith('_'),
                    start_line=start_line,
                    end_line=end_line,
                ))
                current_class = class_name

            if ntype == 'function_definition':
                name_node = node.child_by_field_name('name')
                fn_name = name_node.text.decode('utf-8', errors='replace') if name_node else 'unknown_function'
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                if current_class:
                    qualified = f'{module_qual}.{current_class}.{fn_name}'
                else:
                    qualified = f'{module_qual}.{fn_name}'
                signature = source.splitlines()[start_line - 1].strip() if start_line > 0 else fn_name
                nodes.append(FunctionNode(
                    node_id=f'{module_path}:{fn_name}:{start_line}',
                    qualified_name=qualified,
                    parent_module=module_path,
                    signature=signature,
                    is_public_api=not fn_name.startswith('_'),
                    start_line=start_line,
                    end_line=end_line,
                ))

            for child in node.children:
                walk(child, current_class=current_class if ntype != 'class_definition' else current_class)

        walk(tree.root_node)
        return nodes

    except ImportError:
        return []
    except Exception as exc:  # noqa: BLE001
        logger.warning('tree_sitter_function_extract_error', error=str(exc))
        return []


def _extract_python_functions_fallback(source: str, module_path: str) -> list[FunctionNode]:
    """Regex-based fallback for FunctionNode extraction."""
    module_qual = _module_qualifier(module_path)
    lines = source.splitlines()
    nodes: list[FunctionNode] = []

    for m in _PY_CLASS_RE.finditer(source):
        class_name = m.group(1)
        start_line = source[: m.start()].count('\n') + 1
        nodes.append(FunctionNode(
            node_id=f'{module_path}:{class_name}',
            qualified_name=f'{module_qual}.{class_name}',
            parent_module=module_path,
            signature=f'class {class_name}',
            is_public_api=not class_name.startswith('_'),
            start_line=start_line,
            end_line=start_line,
        ))

    for m in _PY_DEF_RE.finditer(source):
        fn_name = m.group(1)
        start_line = source[: m.start()].count('\n') + 1
        signature = lines[start_line - 1].strip() if start_line - 1 < len(lines) else fn_name
        nodes.append(FunctionNode(
            node_id=f'{module_path}:{fn_name}:{start_line}',
            qualified_name=f'{module_qual}.{fn_name}',
            parent_module=module_path,
            signature=signature,
            is_public_api=not fn_name.startswith('_'),
            start_line=start_line,
            end_line=start_line,
        ))

    return nodes



# ── Public API ────────────────────────────────────────────────────────────────

class LanguageRouter:
    """Dispatch file analysis to the correct language analyser."""

    def analyse_file(self, path: Path, repo_root: Path) -> ModuleNode | None:
        """
        Parse *path* and return a ModuleNode.

        Returns None if the file should be skipped (blocked extension,
        too large, symlink). Never raises — any error sets parse_error
        on the returned node.
        """
        if not is_safe_file(path):
            return None

        language = route_language(path)
        if language == Language.OTHER:
            return None  # Not a source file we understand

        rel_path = str(path.relative_to(repo_root))
        node_id  = rel_path

        module_node = ModuleNode(
            node_id=node_id,
            path=rel_path,
            language=language,
        )

        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            module_node.parse_error = f"IO error: {exc}"
            logger.warning("file_read_error", path=rel_path, error=str(exc))
            return module_node

        try:
            self._dispatch(source, module_node, language)
        except Exception as exc:  # noqa: BLE001
            module_node.parse_error = f"Analysis error: {exc}"
            logger.warning("analysis_error", path=rel_path, error=str(exc))

        return module_node

    def _dispatch(self, source: str, node: ModuleNode, lang: Language) -> None:
        if lang == Language.PYTHON:
            if not _try_tree_sitter_python(source, node):
                logger.debug("tree_sitter_unavailable_using_regex", path=node.path)
                _analyse_python_fallback(source, node)
        elif lang == Language.SQL:
            _try_tree_sitter_sql(source, node)
            _analyse_sql_fallback(source, node)
        elif lang == Language.YAML:
            _try_tree_sitter_yaml(source, node)
            _analyse_yaml_fallback(source, node)
        elif lang == Language.JAVASCRIPT:
            if not _try_tree_sitter_javascript(source, node):
                _analyse_js_ts_fallback(source, node)
        elif lang == Language.TYPESCRIPT:
            if not _try_tree_sitter_typescript(source, node):
                _analyse_js_ts_fallback(source, node)
        elif lang == Language.NOTEBOOK:
            self._analyse_notebook(source, node)
        else:
            node.lines_of_code = len(source.splitlines())

    def extract_functions(self, path: Path, repo_root: Path) -> list[FunctionNode]:
        """
        Extract FunctionNode entries from a source file.
        Currently supports Python; other languages return an empty list.
        """
        if not is_safe_file(path):
            return []

        language = route_language(path)
        if language != Language.PYTHON:
            return []

        rel_path = str(path.relative_to(repo_root))
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []

        nodes = _extract_python_functions_tree_sitter(source, rel_path)
        if not nodes:
            nodes = _extract_python_functions_fallback(source, rel_path)
        return nodes

    @staticmethod
    def _analyse_notebook(source: str, node: ModuleNode) -> None:
        """Extract Python code cells from a Jupyter notebook and analyse them."""
        try:
            import json as _json
            nb = _json.loads(source)
            combined_source = "\n".join(
                "".join(cell.get("source", []))
                for cell in nb.get("cells", [])
                if cell.get("cell_type") == "code"
            )
            if combined_source:
                _analyse_python_fallback(combined_source, node)
        except Exception as exc:  # noqa: BLE001
            node.parse_error = f"Notebook parse error: {exc}"

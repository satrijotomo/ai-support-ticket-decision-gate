import ast
import re
from pathlib import Path


ROOT = Path(__file__).parents[1]
APP_ROOT = ROOT / "app"

SQLITE_IMPLEMENTATION = APP_ROOT / "db.py"
SQLITE_CONSUMERS = {APP_ROOT / "activities_blueprint.py", APP_ROOT / "api_blueprint.py"}
FOUNDRY_OWNERS = {APP_ROOT / "activities_blueprint.py", APP_ROOT / "foundry_client.py"}


def python_sources() -> list[Path]:
    return [ROOT / "function_app.py", *sorted(APP_ROOT.glob("*.py"))]


def imported_modules(source: Path) -> set[str]:
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_future_orchestrator_is_coordination_only() -> None:
    orchestrator = APP_ROOT / "durable_blueprint.py"
    if not orchestrator.exists():
        return

    forbidden_roots = {
        "app.config",
        "app.db",
        "app.foundry_client",
        "azure.ai",
        "azure.identity",
        "httpx",
        "openai",
        "os",
        "pathlib",
        "random",
        "requests",
        "sqlite3",
        "urllib",
        "uuid",
    }
    imports = imported_modules(orchestrator)

    violations = sorted(
        module
        for module in imports
        if any(module == root or module.startswith(f"{root}.") for root in forbidden_roots)
    )

    assert violations == []


def test_sqlite_imports_stay_in_owned_modules() -> None:
    violations: list[str] = []
    for source in python_sources():
        imports = imported_modules(source)
        if "sqlite3" in imports and source != SQLITE_IMPLEMENTATION:
            violations.append(f"{source.relative_to(ROOT)} imports sqlite3")
        if "app.db" in imports and source not in SQLITE_CONSUMERS:
            violations.append(f"{source.relative_to(ROOT)} imports app.db")

    assert violations == []


def test_foundry_imports_stay_in_activity_or_leaf_adapter() -> None:
    foundry_roots = ("azure.ai", "azure.identity", "openai")
    violations: list[str] = []
    for source in python_sources():
        imports = imported_modules(source)
        for module in imports:
            if any(
                module == root or module.startswith(f"{root}.")
                for root in foundry_roots
            ) and source not in FOUNDRY_OWNERS:
                violations.append(f"{source.relative_to(ROOT)} imports {module}")

    assert violations == []


def test_foundry_adapter_cannot_import_persistence() -> None:
    adapter = APP_ROOT / "foundry_client.py"
    if not adapter.exists():
        return

    assert "app.db" not in imported_modules(adapter)
    assert "sqlite3" not in imported_modules(adapter)


def test_ui_javascript_cannot_bypass_http_api() -> None:
    forbidden_tokens = re.compile(
        r"\b(?:indexedDB|localStorage|openDatabase|sqlite3?|AIProjectClient|DurableTask)\b"
    )
    violations: list[str] = []
    for source in sorted((ROOT / "ui").glob("*.js")):
        code = source.read_text(encoding="utf-8")
        if match := forbidden_tokens.search(code):
            violations.append(f"{source.relative_to(ROOT)} uses {match.group(0)}")

    assert violations == []


def test_assignment_implementation_stays_in_owned_modules() -> None:
    allowed = {APP_ROOT / "activities_blueprint.py", APP_ROOT / "db.py"}
    violations: list[str] = []
    for source in python_sources():
        if source in allowed:
            continue
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and re.search(
                r"assign.*ticket|ticket.*assign", node.name, re.IGNORECASE
            ):
                violations.append(f"{source.relative_to(ROOT)} defines {node.name}")

    assert violations == []
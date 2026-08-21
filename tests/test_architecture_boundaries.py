import ast
import re
from pathlib import Path


ROOT = Path(__file__).parents[1]
APP_ROOT = ROOT / "app"

SQLITE_IMPLEMENTATION = APP_ROOT / "db.py"
SQLITE_CONSUMERS = {
    APP_ROOT / "activities_blueprint.py",
    APP_ROOT / "api_blueprint.py",
    APP_ROOT / "demo_controls.py",
}
FOUNDRY_OWNERS = {APP_ROOT / "activities_blueprint.py", APP_ROOT / "foundry_client.py"}
ORCHESTRATOR = APP_ROOT / "durable_blueprint.py"


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
    if not ORCHESTRATOR.exists():
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
    imports = imported_modules(ORCHESTRATOR)

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


def test_foundry_adapter_is_imported_only_by_activities() -> None:
    violations: list[str] = []
    for source in python_sources():
        if source == APP_ROOT / "activities_blueprint.py":
            continue
        if "app.foundry_client" in imported_modules(source):
            violations.append(str(source.relative_to(ROOT)))

    assert violations == []


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
    allowed = {
        APP_ROOT / "activities_blueprint.py",
        APP_ROOT / "api_blueprint.py",
        APP_ROOT / "db.py",
        APP_ROOT / "demo_controls.py",
    }
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


def test_sql_statements_stay_in_database_module() -> None:
    sql_pattern = re.compile(r"\b(?:SELECT|INSERT|UPDATE|DELETE|CREATE TABLE)\b", re.IGNORECASE)
    violations: list[str] = []
    for source in python_sources():
        if source == SQLITE_IMPLEMENTATION:
            continue
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if sql_pattern.search(node.value):
                    violations.append(str(source.relative_to(ROOT)))

    assert violations == []


def test_orchestrator_has_exact_import_allowlist() -> None:
    assert imported_modules(ORCHESTRATOR) == {"azure.durable_functions", "datetime"}


def test_orchestrator_uses_only_durable_coordination_methods() -> None:
    approved_context_methods = {
        "call_activity",
        "call_activity_with_retry",
        "create_timer",
        "get_input",
        "set_custom_status",
        "task_all",
        "task_any",
        "wait_for_external_event",
    }
    tree = ast.parse(ORCHESTRATOR.read_text(encoding="utf-8"))
    context_calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "context"
    }

    assert context_calls <= approved_context_methods


def test_orchestrator_invokes_activities_only_by_name() -> None:
    tree = ast.parse(ORCHESTRATOR.read_text(encoding="utf-8"))
    forbidden_direct_calls = {
        "create_ticket_activity",
        "run_foundry_agent_activity",
        "build_recommendation_activity",
        "save_pending_approval_activity",
        "record_approval_activity",
        "finalize_decision_activity",
        "execute_assignment_activity",
    }
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert called_names.isdisjoint(forbidden_direct_calls)
"""The Python source frontend.

Parsing uses the standard library's ``ast`` module: a real parser for the exact
language version CodeWeaver runs on, rather than a hand-written approximation.
The project being migrated is never imported or executed — only parsed.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from codeweaver.diagnostics import DiagnosticBag, SourceSpan
from codeweaver.frontends.python.annotations import annotation_to_type
from codeweaver.frontends.python.context import ConversionContext
from codeweaver.frontends.python.expressions import convert_expression, convert_params
from codeweaver.frontends.python.statements import convert_block, convert_statement
from codeweaver.ir.declarations import (
    ACCESSOR_GETTER,
    ACCESSOR_SETTER,
    IRClass,
    IRDecorator,
    IRField,
    IRFunction,
    IRImport,
    IRImportName,
    IRModule,
    Visibility,
)
from codeweaver.ir.statements import IRVarDecl
from codeweaver.ir.types import t_unknown

__all__ = [
    "PythonFrontend",
    "ParseResult",
    "module_name_for_path",
    "parse_source",
]

#: Decorators the frontend understands well enough to act on.
_RECOGNISED_DECORATORS = frozenset(
    {
        "staticmethod",
        "classmethod",
        "property",
        "abstractmethod",
        "dataclass",
        "setter",
        "getter",
        "deleter",
    }
)

#: Parameter names treated as the method receiver and removed from the IR
#: parameter list.  The receiver is recorded in the function's metadata so a
#: backend can reconstruct it rather than guessing.
_RECEIVER_NAMES = ("self", "cls")


@dataclass
class ParseResult:
    """The outcome of parsing one file."""

    module: IRModule | None
    diagnostics: DiagnosticBag = field(default_factory=DiagnosticBag)

    @property
    def ok(self) -> bool:
        return self.module is not None


def module_name_for_path(path: str) -> str:
    """Derive a dotted module name from a project-relative path."""
    pure = PurePosixPath(path)
    parts = list(pure.parts)
    if not parts:
        return ""
    stem = pure.stem
    if stem == "__init__":
        parts = parts[:-1]
    else:
        parts[-1] = stem
    return ".".join(part for part in parts if part not in ("", "."))


def parse_source(source: str, path: str) -> ParseResult:
    """Parse *source* into an :class:`IRModule`."""
    diagnostics = DiagnosticBag()
    try:
        tree = ast.parse(source, filename=path, type_comments=False)
    except SyntaxError as exc:
        diagnostics.error(
            "CW0410",
            f"syntax error: {exc.msg}",
            stage="frontend/python",
            span=SourceSpan(path, exc.lineno or 0, max((exc.offset or 1) - 1, 0)),
            hint="CodeWeaver parses with the Python version it runs on",
        )
        return ParseResult(None, diagnostics)
    except ValueError as exc:
        # e.g. source containing null bytes
        diagnostics.error("CW0411", f"source could not be parsed: {exc}", stage="frontend/python")
        return ParseResult(None, diagnostics)

    ctx = ConversionContext(path=path, source_lines=source.splitlines(), diagnostics=diagnostics)
    module = _convert_module(tree, path, ctx)
    return ParseResult(module, diagnostics)


def _convert_module(tree: ast.Module, path: str, ctx: ConversionContext) -> IRModule:
    module = IRModule(
        name=module_name_for_path(path),
        path=path,
        source_language="python",
        docstring=ast.get_docstring(tree) or "",
        span=SourceSpan(path, 1, 0),
    )
    body = _strip_docstring(tree.body, module.docstring)
    for statement in body:
        if isinstance(statement, ast.Import | ast.ImportFrom):
            module.imports.extend(_convert_import(statement, ctx))
        elif isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
            module.functions.append(_convert_function(statement, ctx))
            ctx.bound_names.add(statement.name)
        elif isinstance(statement, ast.ClassDef):
            module.classes.append(_convert_class(statement, ctx))
            ctx.bound_names.add(statement.name)
        else:
            converted = convert_statement(statement, ctx)
            if isinstance(converted, IRVarDecl):
                converted.is_const = converted.name.isupper()
            module.body.append(converted)
    return module


def _convert_import(statement: ast.stmt, ctx: ConversionContext) -> list[IRImport]:
    span = ctx.span(statement)
    if isinstance(statement, ast.Import):
        return [
            IRImport(alias.name, module_alias=alias.asname or "", span=span)
            for alias in statement.names
        ]
    assert isinstance(statement, ast.ImportFrom)
    level = statement.level or 0
    names = [IRImportName(alias.name, alias.asname or "") for alias in statement.names]
    return [
        IRImport(
            statement.module or "",
            names,
            is_relative=level > 0,
            level=level,
            span=span,
        )
    ]


def _convert_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    ctx: ConversionContext,
    *,
    is_method: bool = False,
) -> IRFunction:
    decorators = _convert_decorators(node, ctx)
    names = {_decorator_name(decorator) for decorator in decorators}
    docstring = ast.get_docstring(node) or ""

    body_ctx = ConversionContext(
        path=ctx.path,
        source_lines=ctx.source_lines,
        diagnostics=ctx.diagnostics,
        bound_names=set(ctx.bound_names),
    )
    params = convert_params(node.args, body_ctx)
    receiver = ""
    if is_method and params and params[0].name in _RECEIVER_NAMES and "staticmethod" not in names:
        receiver = params[0].name
        params = params[1:]
    for param in params:
        body_ctx.bound_names.add(param.name)

    function = IRFunction(
        name=node.name,
        params=params,
        return_type=annotation_to_type(node.returns),
        body=convert_block(_strip_docstring(node.body, docstring), body_ctx),
        is_async=isinstance(node, ast.AsyncFunctionDef),
        is_static="staticmethod" in names,
        is_method=is_method,
        is_constructor=is_method and node.name == "__init__",
        is_abstract="abstractmethod" in names,
        decorators=decorators,
        docstring=docstring,
        visibility=Visibility.from_name(node.name),
        span=ctx.span(node),
    )
    if receiver:
        function.meta["pythonReceiver"] = receiver
    function.accessor = _accessor_kind(node, names)
    if "classmethod" in names:
        function.meta["pythonClassMethod"] = True
    if "property" in names:
        function.meta["pythonProperty"] = True
    _report_unrecognised_decorators(function, names, ctx, node)
    return function


def _accessor_kind(node: ast.stmt, decorator_names: set[str]) -> str:
    """Which half of a property this definition is, if either.

    ``@property`` is a getter.  ``@<name>.setter`` is a setter — the decorator
    is an attribute access on the property object, so the frontend matches on
    the attribute rather than on a bare name.  A ``@deleter`` has no mapping in
    the IR and is left as an ordinary method so the backend reports it.
    """
    if "property" in decorator_names:
        return ACCESSOR_GETTER
    for decorator in getattr(node, "decorator_list", []):
        if isinstance(decorator, ast.Attribute) and decorator.attr == "setter":
            return ACCESSOR_SETTER
    return ""


def _convert_class(node: ast.ClassDef, ctx: ConversionContext) -> IRClass:
    decorators = _convert_decorators(node, ctx)
    docstring = ast.get_docstring(node) or ""
    klass = IRClass(
        name=node.name,
        bases=[convert_expression(base, ctx) for base in node.bases],
        decorators=decorators,
        docstring=docstring,
        visibility=Visibility.from_name(node.name),
        span=ctx.span(node),
    )
    if node.keywords:
        ctx.diagnostics.warning(
            "CW0412",
            f"class {node.name!r} uses class keyword arguments, which are not mapped",
            stage="frontend/python",
            span=ctx.span(node),
            hint="metaclass and __init_subclass__ arguments are dropped from the IR",
        )
    for statement in _strip_docstring(node.body, docstring):
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
            klass.methods.append(_convert_function(statement, ctx, is_method=True))
        elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            klass.fields.append(
                IRField(
                    statement.target.id,
                    annotation_to_type(statement.annotation),
                    convert_expression(statement.value, ctx) if statement.value else None,
                    is_static=False,
                    visibility=Visibility.from_name(statement.target.id),
                    span=ctx.span(statement),
                )
            )
        elif isinstance(statement, ast.Assign) and _single_name_target(statement):
            name = _single_name_target(statement) or ""
            klass.fields.append(
                IRField(
                    name,
                    t_unknown(),
                    convert_expression(statement.value, ctx),
                    is_static=True,
                    visibility=Visibility.from_name(name),
                    span=ctx.span(statement),
                )
            )
        elif isinstance(statement, ast.Pass):
            continue
        else:
            # A class body can hold arbitrary statements.  They are neither
            # fields nor methods, so they are recorded on the class rather
            # than dropped, and the diagnostic names the construct.
            unsupported = ctx.unsupported(statement, "class body statement has no IR mapping")
            klass.meta.setdefault("unsupportedMembers", []).append(unsupported.to_dict())
    return klass


def _convert_decorators(node: ast.stmt, ctx: ConversionContext) -> list[IRDecorator]:
    decorators: list[IRDecorator] = []
    for decorator in getattr(node, "decorator_list", []):
        decorators.append(
            IRDecorator(
                convert_expression(decorator, ctx),
                ctx.source_of(decorator),
                span=ctx.span(decorator),
            )
        )
    return decorators


def _decorator_name(decorator: IRDecorator) -> str:
    expression = decorator.expression
    for attribute in ("name", "callee"):
        value = getattr(expression, attribute, None)
        if isinstance(value, str):
            return value
        if value is not None:
            return getattr(value, "name", "") or ""
    return ""


def _report_unrecognised_decorators(
    function: IRFunction, names: set[str], ctx: ConversionContext, node: ast.stmt
) -> None:
    unknown = sorted(name for name in names if name and name not in _RECOGNISED_DECORATORS)
    if unknown:
        ctx.diagnostics.warning(
            "CW0413",
            f"function {function.name!r} has decorators with no mapping: {', '.join(unknown)}",
            stage="frontend/python",
            span=ctx.span(node),
            hint="the decorator is recorded in the IR but not applied by backends",
        )


def _single_name_target(statement: ast.Assign) -> str | None:
    if len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
        return statement.targets[0].id
    return None


def _strip_docstring(body: list[ast.stmt], docstring: str) -> list[ast.stmt]:
    if not docstring or not body:
        return body
    first = body[0]
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        return body[1:]
    return body


class PythonFrontend:
    """Frontend adapter registered in the language registry."""

    language = "python"
    display_name = "Python 3"
    file_extensions = (".py", ".pyi")
    supported_constructs = (
        "module docstrings",
        "imports (absolute and relative)",
        "module-level constants and variables",
        "functions and async functions",
        "classes, methods, constructors, class and instance fields",
        "properties: @property getters and @x.setter setters",
        "parameters with annotations, defaults, *args and **kwargs",
        "arithmetic, bitwise, logical and comparison expressions",
        "calls, attribute access, indexing and slicing",
        "list, tuple, set and dict literals",
        "f-strings without format specifications",
        "if/elif/else, while, for-each, break, continue",
        "try/except/else/finally, raise, assert",
        "lambdas and conditional expressions",
        "single-clause list, set and dict comprehensions",
    )
    known_limitations = (
        "generator expressions are reported, not translated (they are lazy)",
        "comprehensions with nested clauses, several filters or tuple targets "
        "are reported, not translated",
        "context managers (with) are reported, not translated",
        "structural pattern matching is reported, not translated",
        "nested function and class definitions are reported, not lifted",
        "destructuring assignment and tuple for-targets are reported",
        "decorators other than staticmethod/classmethod/property/abstractmethod "
        "are recorded but not applied",
        "metaclasses, reflection and dynamic attribute access are not modelled",
    )

    def parse_module(self, source: str, path: str) -> ParseResult:
        return parse_source(source, path)

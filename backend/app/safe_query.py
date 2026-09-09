"""Validation and execution for ``query_closet_items``'s ``where_clause`` (spec §4.1).

The assistant hands us a single SQL **boolean expression** — never a statement.
This module decides whether that expression is acceptable and, if it is, runs
it. It is the highest-risk surface in the build, so it is worth being precise
about where the safety actually comes from.

Ordering of the guarantees, strongest first:

1. **The read-only database role.** Everything here runs over
   ``db.readonly_transaction``, which uses the ``wardrobe_readonly`` role from
   db/migrations/0004. That role holds exactly one privilege in the whole
   database: ``SELECT`` on ``public.closet_query_view``. It cannot read
   ``items`` directly, cannot read any other table, and cannot write anything.
   Its transactions are read-only and its statements are time-bounded at the
   role level as well as per transaction. If every other layer in this file
   failed open, a successful injection would still be limited to selecting
   from one view.

2. **The parameterised ``user_id``.** The predicate is executed as
   ``... WHERE user_id = $1 AND (<validated>)``. ``$1`` is bound by asyncpg
   from the authenticated token's subject and never derived from any text the
   assistant supplied. The validated predicate is wrapped in parentheses, so
   no amount of ``OR`` inside it can widen the row set beyond that user. This
   matters more than usual here: ``closet_query_view`` is not a
   ``security_invoker`` view, so RLS on ``items`` does **not** apply through
   it (see db/migrations/0003). The bound ``user_id`` is the only thing
   keeping one user out of another's closet on this path.

3. **The AST allowlist below.** This is defence in depth and a source of good
   error messages for the assistant — it is deliberately *not* the security
   boundary. sqlglot's own documentation is explicit that it is a
   parser/transpiler and not a security validator, and its parser is
   intentionally lenient. Treating it as the boundary would be a mistake; it
   is here to reject obvious nonsense early, cheaply, and with an explanation
   the model can act on.

The validator is an allowlist over AST *node types*, not a denylist of bad
words. Anything the parser produces that is not explicitly permitted is
rejected, so a construct nobody thought of (a cast trick, an unknown
function, a window expression) fails closed rather than needing to have been
predicted. The validated AST is then **re-serialised by sqlglot** — the
assistant's raw text is never concatenated into the executed statement.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from uuid import UUID

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers

from app.config import get_settings
from app.db import readonly_transaction
from app.errors import UnsafeQueryError
from app.services import taxonomy

logger = logging.getLogger(__name__)

# The columns of closet_query_view an assistant-supplied predicate may name.
# `id` and `user_id` are columns of the view but deliberately absent: `id` is
# useless as a filter to a model that has just been handed the ids anyway, and
# `user_id` must only ever be set by the bound parameter (spec §4.1 step 4).
ALLOWED_COLUMNS = frozenset(
    {
        "category",
        "colors",
        "brand",
        "warmth_rating",
        "formality",
        "tags",
        "notes",
        "fields",
    }
)

# Only this column can be JSON-traversed, and only into a field name that
# actually exists in category_field_defs.
JSON_COLUMN = "fields"

MAX_CLAUSE_LENGTH = 2000
MAX_AST_NODES = 400
# Depth is bounded separately from node count because they fail differently:
# 200 nested parentheses is only ~205 nodes but recurses 200 deep, and blowing
# the Python stack inside a request is a crash, not a rejection. It is checked
# twice — textually in _prescan before sqlglot's recursive parser runs, and
# again on the AST afterwards, since a deep tree can also be built without deep
# parenthesisation.
MAX_NESTING_DEPTH = 40
MAX_AST_DEPTH = 40

# Step 1 of spec §4.1: a blunt textual scan, before the parser sees anything.
# It rejects a few things the AST walk would also catch, on the principle that
# the cheapest rejection is the best one and that a predicate containing these
# is not a predicate.
#
# The scan runs over the input with string literals blanked out first. Without
# that, `notes ILIKE '%goes with jeans%'` would be rejected for containing
# "with" -- and `notes` is a column the spec specifically wants searchable,
# because it is where differentiating detail lives. Blanking literals costs
# nothing in safety: the scan's only output is a yes/no rejection, it never
# feeds the SQL that gets built, and the structural constructs these words
# stand for are independently rejected by the AST node allowlist below.
_FORBIDDEN_KEYWORDS = (
    "select",
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "grant",
    "revoke",
    "create",
    "truncate",
    "with",
    "union",
    "intersect",
    "except",
    "into",
    "returning",
    "copy",
    "execute",
    "call",
    "vacuum",
    "analyze",
    "set",
    "reset",
    "commit",
    "rollback",
    "begin",
    "lateral",
    "table",
    "from",
    "join",
    "exists",
)
_KEYWORD_RE = re.compile(
    r"\b(" + "|".join(_FORBIDDEN_KEYWORDS) + r")\b",
    re.IGNORECASE,
)

# Comment markers, statement separators and the psql/COPY escapes. A comment is
# how you truncate the rest of a statement, so there is no benign reason for one
# in a bare predicate.
_FORBIDDEN_SEQUENCES = {
    ";": "a semicolon (only a single boolean expression is accepted, never a statement)",
    "--": "a SQL line comment",
    "/*": "a SQL block comment",
    "*/": "a SQL block comment",
    "\\": "a backslash escape",
    "$$": "a dollar-quoted string",
    "::": "a type cast",
}

# Node types the walk permits. Everything else -- Cast, Subquery, Select, Union,
# Anonymous (any function call such as pg_sleep), Command (anything sqlglot
# could not parse properly), window functions, and so on -- is rejected because
# it is not on this list, not because it was anticipated.
_ALLOWED_NODES: tuple[type[exp.Expression], ...] = (
    # Boolean structure
    exp.And,
    exp.Or,
    exp.Not,
    exp.Paren,
    # Comparison
    exp.EQ,
    exp.NEQ,
    exp.GT,
    exp.GTE,
    exp.LT,
    exp.LTE,
    exp.Is,
    exp.In,
    exp.Between,
    exp.Like,
    exp.ILike,
    # Array operators from spec §4.1: && @> <@ and ANY/ALL
    exp.ArrayOverlaps,
    exp.ArrayContainsAll,
    exp.ArrayContainedBy,
    exp.Any,
    exp.All,
    exp.Array,
    # jsonb traversal, validated against category_field_defs at the node itself
    exp.JSONExtract,
    exp.JSONExtractScalar,
    exp.JSONPath,
    exp.JSONPathRoot,
    exp.JSONPathKey,
    # Leaves
    exp.Column,
    exp.Identifier,
    exp.Literal,
    exp.Boolean,
    exp.Null,
    exp.Neg,
)

# Node types worth naming in the error message, because the assistant can act
# on a specific "no subqueries" far better than on "unsupported expression".
_EXPLAINED_NODES: dict[type[exp.Expression], str] = {
    exp.Subquery: "a subquery",
    exp.Select: "a SELECT statement",
    exp.Union: "a set operation (UNION/INTERSECT/EXCEPT)",
    exp.With: "a common table expression (WITH ...)",
    exp.CTE: "a common table expression (WITH ...)",
    exp.Cast: "a type cast",
    exp.Command: "a SQL statement rather than a boolean expression",
    exp.Star: "a wildcard (*)",
    exp.Table: "a table reference",
    exp.Placeholder: "a bind placeholder",
    exp.Parameter: "a session parameter reference",
}


def _reject(reason: str, **details: Any) -> UnsafeQueryError:
    return UnsafeQueryError(
        f"{reason} Supply a single SQL boolean expression over the allowed columns only — "
        "see query_instructions from get_closet_structure for the exact grammar.",
        **details,
    )


def _describe(node: exp.Expression) -> str:
    for node_type, description in _EXPLAINED_NODES.items():
        if isinstance(node, node_type):
            return description
    if isinstance(node, exp.Func):
        # Covers every function sqlglot knows by name (Length, Lower,
        # CurrentVersion, ...) as well as the ones it does not (Anonymous), so
        # the message reads the same whether or not sqlglot happened to
        # recognise the name. Reached only for functions outside the allowlist:
        # the permitted node types are matched before this.
        name = node.sql_name() if hasattr(node, "sql_name") else type(node).__name__
        return f"a function call ({name})"
    return f"an unsupported expression ({type(node).__name__})"


def _check_column(node: exp.Column) -> None:
    if node.table:
        raise _reject(
            f"'{node.sql(dialect='postgres')}' is table-qualified; "
            "reference the columns by bare name.",
            column=node.sql(dialect="postgres"),
        )
    name = node.name
    if name not in ALLOWED_COLUMNS:
        raise _reject(
            f"'{name}' is not a queryable column.",
            column=name,
            allowed_columns=sorted(ALLOWED_COLUMNS),
        )


def _check_json_access(node: exp.Expression, known_fields: set[str]) -> None:
    """``fields->>'x'``: the operand must be ``fields`` and ``x`` must be real."""
    operand = node.this
    if not isinstance(operand, exp.Column) or operand.table or operand.name != JSON_COLUMN:
        raise _reject(
            f"only '{JSON_COLUMN}' supports -> / ->> traversal.",
            column=operand.sql(dialect="postgres") if operand else None,
        )

    path = node.expression
    keys: list[str] = []
    if isinstance(path, exp.JSONPath):
        for part in path.expressions:
            if isinstance(part, exp.JSONPathRoot):
                continue
            if not isinstance(part, exp.JSONPathKey):
                raise _reject(f"only a simple '{JSON_COLUMN}->>''field_name''' access is accepted.")
            keys.append(str(part.this))
    elif isinstance(path, exp.Literal) and path.is_string:
        keys.append(path.this)
    else:
        raise _reject(f"the key in a '{JSON_COLUMN}' access must be a literal field name.")

    if len(keys) != 1:
        raise _reject(f"only a single-level '{JSON_COLUMN}->>''field_name''' access is accepted.")

    key = keys[0]
    if key not in known_fields:
        raise _reject(
            f"'{key}' is not a category-specific field.",
            field=key,
            known_fields=sorted(known_fields),
        )


def _blank_string_literals(clause: str) -> str:
    """Replace the contents of every ``'...'`` literal with spaces.

    Postgres doubles a quote to escape it (``'it''s'``) and, with
    ``standard_conforming_strings`` on, a backslash is not an escape — and this
    module rejects backslashes outright anyway — so a single left-to-right pass
    is exact rather than approximate. An unterminated literal is a rejection:
    it is the classic way to make the rest of a statement part of a string.
    """
    out: list[str] = []
    index = 0
    length = len(clause)
    while index < length:
        char = clause[index]
        if char != "'":
            out.append(char)
            index += 1
            continue

        out.append("'")
        index += 1
        closed = False
        while index < length:
            if clause[index] == "'":
                if index + 1 < length and clause[index + 1] == "'":
                    out.append("  ")
                    index += 2
                    continue
                out.append("'")
                index += 1
                closed = True
                break
            out.append(" ")
            index += 1
        if not closed:
            raise _reject("The where_clause has an unterminated string literal.")
    return "".join(out)


def _depth(nodes: list[exp.Expression], root: exp.Expression) -> int:
    """How deep the tree goes, counted by walking each node's parent chain.

    Bounded by the node-count check that runs first, so this stays cheap.
    """
    deepest = 0
    for node in nodes:
        levels = 0
        current = node
        while current is not root and current.parent is not None:
            levels += 1
            current = current.parent
            if levels > MAX_AST_DEPTH:
                return levels
        deepest = max(deepest, levels)
    return deepest


def _prescan(where_clause: str) -> str:
    """Spec §4.1 step 1 — reject before the parser is even involved."""
    clause = (where_clause or "").strip()
    if not clause:
        raise _reject("The where_clause is empty.")
    if len(clause) > MAX_CLAUSE_LENGTH:
        raise _reject(
            f"The where_clause is {len(clause)} characters; the limit is {MAX_CLAUSE_LENGTH}.",
            length=len(clause),
            limit=MAX_CLAUSE_LENGTH,
        )

    # Structural characters are scanned across the whole input, including
    # inside literals: a semicolon or comment marker in a string is not a
    # legitimate closet filter, and treating it as one is how these checks get
    # talked out of firing.
    for sequence, description in _FORBIDDEN_SEQUENCES.items():
        if sequence in clause:
            raise _reject(f"The where_clause contains {description}.", found=sequence)

    blanked = _blank_string_literals(clause)

    # Nesting depth is bounded *here*, before sqlglot sees the input, because
    # sqlglot's parser is recursive: a few hundred nested parentheses raise
    # RecursionError inside parse_one, which is a crash rather than a
    # rejection. Checking the AST afterwards would be too late. Parentheses in
    # string literals do not count, hence the blanked copy.
    depth = 0
    for char in blanked:
        if char == "(":
            depth += 1
            if depth > MAX_NESTING_DEPTH:
                raise _reject(
                    f"The where_clause nests parentheses more than {MAX_NESTING_DEPTH} deep.",
                    limit=MAX_NESTING_DEPTH,
                )
        elif char == ")":
            depth -= 1

    keyword = _KEYWORD_RE.search(blanked)
    if keyword:
        raise _reject(
            f"The where_clause contains the SQL keyword '{keyword.group(1).upper()}'. "
            "It must be a bare boolean predicate — no statements, CTEs or subqueries.",
            keyword=keyword.group(1).lower(),
        )
    return clause


def validate_where_clause(where_clause: str, known_fields: set[str]) -> str:
    """Validate a predicate and return the **re-serialised** SQL for it.

    Raises :class:`UnsafeQueryError` with an explanation the assistant can act
    on. The returned string comes from sqlglot's generator, not from the input:
    nothing the caller typed is ever concatenated into the executed statement.
    """
    clause = _prescan(where_clause)

    try:
        tree = sqlglot.parse_one(clause, read="postgres")
    except sqlglot.errors.ParseError as parse_error:
        raise _reject(
            f"The where_clause is not valid SQL: {str(parse_error).splitlines()[0]}"
        ) from parse_error
    except RecursionError as recursion_error:
        # _prescan's nesting check should have caught this already; if some
        # other shape still recurses too deep, it is a rejection rather than a
        # 500. Deliberately not narrowed to a specific construct — the point is
        # that an unanticipated one fails closed.
        raise _reject("The where_clause is too deeply nested to parse.") from recursion_error

    if tree is None:
        raise _reject("The where_clause did not parse to an expression.")

    # Fold unquoted identifiers to lower case, exactly as Postgres itself would
    # before resolving them. Without this, `CATEGORY = 'jacket'` -- valid SQL,
    # and a plausible thing for a model writing SQL-shaped text to produce --
    # is rejected as an unknown column, which costs the user a call out of a
    # tight daily budget for a predicate that was never wrong. Quoted
    # identifiers keep their case, also as Postgres would, so `"User_Id"` is
    # still not a column name any allowlist matches.
    tree = normalize_identifiers(tree, dialect="postgres")

    # sqlglot.parse_one on multiple statements keeps only the first, so confirm
    # the input really was one expression rather than trusting the parser to
    # have complained.
    if len(sqlglot.parse(clause, read="postgres")) != 1:
        raise _reject("The where_clause contains more than one statement.")

    nodes = list(tree.walk())
    if len(nodes) > MAX_AST_NODES:
        raise _reject(
            f"The where_clause is too complex ({len(nodes)} nodes; the limit is {MAX_AST_NODES}).",
            nodes=len(nodes),
            limit=MAX_AST_NODES,
        )

    depth = _depth(nodes, tree)
    if depth > MAX_AST_DEPTH:
        raise _reject(
            f"The where_clause is nested too deeply ({depth} levels; "
            f"the limit is {MAX_AST_DEPTH}).",
            depth=depth,
            limit=MAX_AST_DEPTH,
        )

    for node in nodes:
        if not isinstance(node, _ALLOWED_NODES):
            raise _reject(f"The where_clause contains {_describe(node)}, which is not allowed.")

        if isinstance(node, (exp.JSONExtract, exp.JSONExtractScalar)):
            _check_json_access(node, known_fields)
        elif isinstance(node, exp.Column):
            # A column reached as the operand of a validated JSON access has
            # already been checked; checking it again is harmless and keeps the
            # walk order-independent.
            _check_column(node)
        elif isinstance(node, exp.In):
            # `x IN (SELECT ...)` hangs the subquery off `query`/`field`, which
            # walk() visits, but naming it explicitly gives a better message.
            if node.args.get("query") or node.args.get("unnest") or node.args.get("field"):
                raise _reject("IN must list literal values, not a subquery.")

    # The predicate must be a boolean expression, not a bare value: `1` or
    # `'x'` alone would make the generated statement `WHERE user_id = $1 AND (1)`,
    # which Postgres rejects anyway, but with a far worse error message.
    if isinstance(tree, (exp.Literal, exp.Column, exp.Null)):
        raise _reject("The where_clause must be a boolean expression, not a bare value.")

    validated = tree.sql(dialect="postgres")

    # Belt and braces on the generator: if re-serialisation somehow produced a
    # separator or comment, the statement never gets built.
    for sequence in (";", "--", "/*"):
        if sequence in validated:
            raise _reject("The where_clause could not be safely re-serialised.")

    return validated


async def run_where_clause(user_id: UUID, where_clause: str) -> dict[str, Any]:
    """Validate, then execute, an assistant-supplied predicate.

    Returns the matching rows plus the SQL actually executed, so the assistant
    can see how its predicate was normalised (and so a rejection and a
    zero-result match are never confusable).
    """
    settings = get_settings()
    known_fields = await taxonomy.known_field_names()

    try:
        validated = validate_where_clause(where_clause, known_fields)
    except UnsafeQueryError as rejection:
        # Spec §4.1 step 6. Logged at WARNING with the raw input so the
        # allowlist can be tuned, and abuse spotted, from the application log.
        logger.warning(
            "rejected where_clause for user %s: %s | raw=%r",
            user_id,
            rejection.message,
            where_clause,
        )
        raise

    limit = settings.query_result_limit
    # The predicate is interpolated as *generated* SQL, and the user id is a
    # bound parameter. Wrapping the predicate in parentheses is what stops an
    # `OR` inside it from escaping the user scoping.
    statement = (
        "select id, category, colors, brand, warmth_rating, formality, tags, notes, fields "
        "from public.closet_query_view "
        f"where user_id = $1 and ({validated}) "
        f"order by category, brand nulls last, id limit {int(limit)}"
    )

    logger.info("query_closet_items for user %s: %s", user_id, validated)

    async with readonly_transaction() as conn:
        rows = await conn.fetch(statement, user_id)

    items = [
        {
            "id": str(row["id"]),
            "category": row["category"],
            "colors": list(row["colors"] or []),
            "brand": row["brand"],
            "warmth_rating": row["warmth_rating"],
            "formality": row["formality"],
            "tags": list(row["tags"] or []),
            "notes": row["notes"],
            "fields": row["fields"] or {},
        }
        for row in rows
    ]

    return {
        "items": items,
        "count": len(items),
        "limit": limit,
        "truncated": len(items) == limit,
        "where_clause_executed": validated,
    }


# ---------------------------------------------------------------------------
# Fixed queries (spec §4)
#
# `list_category_items` and `get_closet_summary` never see assistant-supplied
# predicate text at all. Both statements below are constant, with every value
# bound as a parameter, so none of the machinery above applies to them — which
# is exactly why the spec says to reach for these two first.
# ---------------------------------------------------------------------------

_ITEM_SELECT = (
    "select id, category, colors, brand, warmth_rating, formality, tags, notes, fields "
    "from public.closet_query_view"
)


def _row_to_item(row: Any) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "category": row["category"],
        "colors": list(row["colors"] or []),
        "brand": row["brand"],
        "warmth_rating": row["warmth_rating"],
        "formality": row["formality"],
        "tags": list(row["tags"] or []),
        "notes": row["notes"],
        "fields": row["fields"] or {},
    }


async def list_category(user_id: UUID, category: str) -> dict[str, Any]:
    """Every item in one category, up to the listing limit."""
    settings = get_settings()
    limit = settings.category_listing_limit

    async with readonly_transaction() as conn:
        rows = await conn.fetch(
            f"{_ITEM_SELECT} where user_id = $1 and category = $2 "
            "order by brand nulls last, id limit $3",
            user_id,
            category,
            limit,
        )

    items = [_row_to_item(row) for row in rows]
    return {
        "category": category,
        "items": items,
        "count": len(items),
        "limit": limit,
        "truncated": len(items) == limit,
    }


async def closet_summary(user_id: UUID) -> dict[str, Any]:
    """A shape-of-the-closet overview without pulling every item (spec §4)."""
    async with readonly_transaction() as conn:
        rows = await conn.fetch(
            "select category, brand, warmth_rating, formality, tags "
            "from public.closet_query_view where user_id = $1",
            user_id,
        )

    per_category: dict[str, int] = {}
    per_formality: dict[str, int] = {}
    per_warmth: dict[str, int] = {}
    brands: set[str] = set()
    tags: set[str] = set()

    for row in rows:
        per_category[row["category"]] = per_category.get(row["category"], 0) + 1

        formality = row["formality"] or "unspecified"
        per_formality[formality] = per_formality.get(formality, 0) + 1

        warmth = str(row["warmth_rating"]) if row["warmth_rating"] is not None else "unspecified"
        per_warmth[warmth] = per_warmth.get(warmth, 0) + 1

        if row["brand"]:
            brands.add(row["brand"])
        tags.update(row["tags"] or [])

    return {
        "total_items": len(rows),
        "items_per_category": dict(sorted(per_category.items(), key=lambda kv: (-kv[1], kv[0]))),
        "brands": sorted(brands),
        "tags": sorted(tags),
        "formality_distribution": dict(sorted(per_formality.items())),
        "warmth_distribution": dict(sorted(per_warmth.items())),
    }

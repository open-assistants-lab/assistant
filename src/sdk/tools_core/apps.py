"""App tools — SDK-native implementation.

Structured data apps using HybridDB (SQLite + FTS5 + ChromaDB) for
full-text and semantic search. Each app has tables with typed columns,
and TEXT columns automatically get FTS5 + vector search.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from hybriddb import HybridDB, SearchMode
from hybriddb.embedding import hash_embedding as _hash_embedding

from src.app_logging import get_logger
from src.config import get_settings
from src.sdk.messages import Message
from src.sdk.tools import ToolAnnotations, tool
from src.storage.paths import DEFAULT_USER_ID, get_paths

logger = get_logger()

MODEL_CACHE_DIR = Path(os.path.expanduser("~")) / ".cache" / "sentence-transformers"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384

_embedding_model = None


def _get_embedding_model() -> Any:
    global _embedding_model
    if _embedding_model is None:
        try:
            from sentence_transformers import SentenceTransformer

            _embedding_model = SentenceTransformer(
                EMBEDDING_MODEL,
                cache_folder=str(MODEL_CACHE_DIR),
            )
        except Exception as exc:
            # sentence-transformers is an optional extra (assistant-sdk[memory-vector]).
            if "sentence_transformers" in str(exc) or isinstance(
                exc, ModuleNotFoundError
            ):
                logger.warning(
                    "apps.embedding_missing",
                    {"hint": "install assistant-sdk[memory-vector] for semantic embeddings"},
                )
            _embedding_model = None
    return _embedding_model


def get_embedding(text: str) -> list[float]:
    if not text:
        return [0.0] * EMBEDDING_DIM
    model = _get_embedding_model()
    if model is not None:
        try:
            embedding = model.encode(str(text), show_progress_bar=False)
            return [float(x) for x in embedding.tolist()]
        except Exception:
            pass
    result: list[float] = _hash_embedding(text)
    return result


@dataclass
class TableSchema:
    name: str
    columns: dict[str, str]
    text_columns: list[str] = field(default_factory=list)
    chroma_columns: list[str] = field(default_factory=list)


@dataclass
class AppSchema:
    name: str
    tables: dict[str, TableSchema]


_dbs: dict[str, HybridDB] = {}


def _get_base_path(user_id: str) -> Path:
    return get_paths(user_id).apps_dir()


def _sanitize_app_name(name: str) -> str:
    return "".join(c if c.isalnum() or c == "_" else "_" for c in name.lower())


def _get_app_path(app_name: str, user_id: str) -> Path:
    base = _get_base_path(user_id)
    base.mkdir(parents=True, exist_ok=True)
    safe_name = _sanitize_app_name(app_name)
    app_path = base / safe_name
    app_path.mkdir(parents=True, exist_ok=True)
    return app_path


def _get_db(app_name: str, user_id: str) -> HybridDB:
    key = f"{user_id}:{app_name}"
    if key not in _dbs:
        app_path = _get_app_path(app_name, user_id)
        _dbs[key] = HybridDB(
            str(app_path),
            embedding_model_name=EMBEDDING_MODEL,
            max_chroma_index_gb=get_settings().memory.messages.max_chroma_index_gb,
        )
    return _dbs[key]


def _get_schema(app_name: str, user_id: str) -> AppSchema | None:
    db = _get_db(app_name, user_id)
    tables = db.list_tables()
    if not tables:
        return None
    table_schemas = {}
    for tname in tables:
        cols = db.get_schema(tname)
        text_cols = [c for c, ct in cols.items() if ct in ("TEXT", "LONGTEXT")]
        chroma_cols = [c for c, ct in cols.items() if ct == "LONGTEXT"]
        table_schemas[tname] = TableSchema(
            name=tname,
            columns=cols,
            text_columns=text_cols,
            chroma_columns=chroma_cols,
        )
    return AppSchema(name=app_name, tables=table_schemas)


def _list_apps(user_id: str) -> list[str]:
    base = _get_base_path(user_id)
    apps = []
    for db_file in base.glob("*/app.db"):
        apps.append(db_file.parent.name)
    return apps


def _delete_app(app_name: str, user_id: str) -> bool:
    app_path = _get_app_path(app_name, user_id)
    key = f"{user_id}:{app_name}"
    _dbs.pop(key, None)
    if app_path.exists():
        shutil.rmtree(app_path)
        return True
    return False


@tool
def app_create(name: str, tables: dict[str, dict[str, str]], user_id: str =  DEFAULT_USER_ID) -> str:
    """Create a new app with one or more tables.

    Args:
        name: App name (e.g., 'pos', 'project', 'wine')
        tables: Dict of {table_name: {column: type}} where type is TEXT, INTEGER, REAL, BOOLEAN.
               Text columns get FTS5 + sqlite-vec automatically
        user_id: User identifier

    Returns:
        Success message with app details
    """
    try:
        db = _get_db(name, user_id)
        table_schemas: dict[str, TableSchema] = {}

        for table_name, schema in tables.items():
            db.create_table(table_name, schema)
            text_columns = [col for col, ct in schema.items() if ct.upper() in ("TEXT", "LONGTEXT")]
            chroma_columns = [col for col, ct in schema.items() if ct.upper() == "LONGTEXT"]
            table_schemas[table_name] = TableSchema(
                name=table_name,
                columns=schema,
                text_columns=text_columns,
                chroma_columns=chroma_columns,
            )

        tables_info = []
        for tname, tschema in table_schemas.items():
            text_cols = ", ".join(tschema.text_columns) if tschema.text_columns else "none"
            tables_info.append(f"  - {tname}: {list(tschema.columns.keys())} (text: {text_cols})")

        return f"App '{name}' created successfully.\n\nTables:\n" + "\n".join(tables_info)
    except Exception as e:
        logger.error("app_create.error", {"name": name, "error": str(e)}, user_id=user_id)
        return f"Error creating app: {e}"


app_create.annotations = ToolAnnotations(title="Create App", destructive=True)


@tool
def app_list(user_id: str =  DEFAULT_USER_ID) -> str:
    """List all apps the user has created.

    Args:
        user_id: User identifier

    Returns:
        List of app names
    """
    try:
        apps = _list_apps(user_id)

        if not apps:
            return "No apps found. Create one with app_create()."

        return "Apps:\n" + "\n".join(f"  - {app}" for app in sorted(apps))
    except Exception as e:
        logger.error("app_list.error", {"error": str(e)}, user_id=user_id)
        return f"Error listing apps: {e}"


app_list.annotations = ToolAnnotations(title="List Apps", read_only=True, idempotent=True)


@tool
def app_schema(name: str, user_id: str =  DEFAULT_USER_ID) -> str:
    """Get schema for an app.

    Args:
        name: App name
        user_id: User identifier

    Returns:
        App schema details with all tables
    """
    try:
        schema = _get_schema(name, user_id)

        if not schema:
            return f"App '{name}' not found."

        lines = [f"App: {name}", "", "Tables:"]

        for tname, tschema in schema.tables.items():
            columns_str = ", ".join(f"{col}: {typ}" for col, typ in tschema.columns.items())
            text_cols = ", ".join(tschema.text_columns) if tschema.text_columns else "none"
            lines.append(f"  - {tname}: {columns_str}")
            lines.append(f"    Text columns (FTS5 + vec): {text_cols}")

        return "\n".join(lines)
    except Exception as e:
        logger.error("app_schema.error", {"name": name, "error": str(e)}, user_id=user_id)
        return f"Error getting schema: {e}"


app_schema.annotations = ToolAnnotations(title="App Schema", read_only=True, idempotent=True)


@tool
def app_delete(name: str, user_id: str =  DEFAULT_USER_ID) -> str:
    """Delete an app and all its data.

    Args:
        name: App name to delete
        user_id: User identifier

    Returns:
        Success or error message
    """
    try:
        if _delete_app(name, user_id):
            return f"App '{name}' deleted successfully."
        return f"App '{name}' not found."
    except Exception as e:
        logger.error("app_delete.error", {"name": name, "error": str(e)}, user_id=user_id)
        return f"Error deleting app: {e}"


app_delete.annotations = ToolAnnotations(title="Delete App", destructive=True)


@tool
def app_insert(app: str, table: str, data: dict[str, Any], user_id: str =  DEFAULT_USER_ID) -> str:
    """Insert a row into a table.

    Args:
        app: App name
        table: Table name
        data: Dict of column: value pairs
        user_id: User identifier

    Returns:
        Success or error message
    """
    try:
        db = _get_db(app, user_id)
        row_id = db.insert(table, data)
        return f"Inserted row {row_id} into '{app}.{table}'."
    except Exception as e:
        logger.error(
            "app_insert.error", {"app": app, "table": table, "error": str(e)}, user_id=user_id
        )
        return f"Error inserting data: {e}"


app_insert.annotations = ToolAnnotations(title="Insert App Row")


@tool
def app_update(
    app: str, table: str, id: int, data: dict[str, Any], user_id: str =  DEFAULT_USER_ID
) -> str:
    """Update a row by ID.

    Args:
        app: App name
        table: Table name
        id: Row ID to update
        data: Dict of column: value pairs to update
        user_id: User identifier

    Returns:
        Success or error message
    """
    try:
        db = _get_db(app, user_id)
        if db.update(table, id, data):
            return f"Updated row {id} in '{app}.{table}'."
        return f"Row {id} not found in '{app}.{table}'."
    except Exception as e:
        logger.error(
            "app_update.error",
            {"app": app, "table": table, "id": id, "error": str(e)},
            user_id=user_id,
        )
        return f"Error updating data: {e}"


app_update.annotations = ToolAnnotations(title="Update App Row")


@tool
def app_delete_row(app: str, table: str, id: int, user_id: str =  DEFAULT_USER_ID) -> str:
    """Delete a row by ID.

    Args:
        app: App name
        table: Table name
        id: Row ID to delete
        user_id: User identifier

    Returns:
        Success or error message
    """
    try:
        db = _get_db(app, user_id)
        if db.delete(table, id):
            return f"Deleted row {id} from '{app}.{table}'."
        return f"Row {id} not found in '{app}.{table}'."
    except Exception as e:
        logger.error(
            "app_delete_row.error",
            {"app": app, "table": table, "id": id, "error": str(e)},
            user_id=user_id,
        )
        return f"Error deleting data: {e}"


app_delete_row.annotations = ToolAnnotations(title="Delete App Row", destructive=True)


@tool
def app_column_add(
    app: str,
    table: str,
    column: str,
    col_type: str,
    enable_search: bool = True,
    user_id: str =  DEFAULT_USER_ID,
) -> str:
    """Add a column to a table.

    Args:
        app: App name
        table: Table name
        column: Column name
        col_type: Column type (TEXT, INTEGER, REAL, BOOLEAN)
        enable_search: If True and col_type is TEXT, enable FTS5 + vec (default True)
        user_id: User identifier

    Returns:
        Success or error message
    """
    try:
        db = _get_db(app, user_id)
        db.add_column(table, column, col_type)
        search_info = " with FTS5 search" if enable_search and col_type.upper() == "TEXT" else ""
        return f"Added column '{column}' ({col_type}) to '{app}.{table}'{search_info}."
    except Exception as e:
        logger.error(
            "app_column_add.error",
            {"app": app, "table": table, "column": column, "error": str(e)},
            user_id=user_id,
        )
        return f"Error adding column: {e}"


app_column_add.annotations = ToolAnnotations(title="Add App Column")


@tool
def app_column_delete(app: str, table: str, column: str, user_id: str =  DEFAULT_USER_ID) -> str:
    """Delete a column from a table.

    Args:
        app: App name
        table: Table name
        column: Column name to delete
        user_id: User identifier

    Returns:
        Success or error message
    """
    try:
        db = _get_db(app, user_id)
        db.drop_column(table, column)
        return f"Deleted column '{column}' from '{app}.{table}'."
    except Exception as e:
        logger.error(
            "app_column_delete.error",
            {"app": app, "table": table, "column": column, "error": str(e)},
            user_id=user_id,
        )
        return f"Error deleting column: {e}"


app_column_delete.annotations = ToolAnnotations(title="Delete App Column", destructive=True)


@tool
def app_column_rename(
    app: str, table: str, old_name: str, new_name: str, user_id: str =  DEFAULT_USER_ID
) -> str:
    """Rename a column in a table.

    Args:
        app: App name
        table: Table name
        old_name: Current column name
        new_name: New column name
        user_id: User identifier

    Returns:
        Success or error message
    """
    try:
        db = _get_db(app, user_id)
        db.rename_column(table, old_name, new_name)
        return f"Renamed column '{old_name}' to '{new_name}' in '{app}.{table}'."
    except Exception as e:
        logger.error(
            "app_column_rename.error",
            {
                "app": app,
                "table": table,
                "old_name": old_name,
                "new_name": new_name,
                "error": str(e),
            },
            user_id=user_id,
        )
        return f"Error renaming column: {e}"


app_column_rename.annotations = ToolAnnotations(title="Rename App Column")


def _convert_date_in_query(query: str) -> str:
    """Rewrite date words/ISO dates to epoch-ms OUTSIDE single-quoted literals.

    Date words inside string literals (e.g. ``note LIKE '%today%'``) are
    user data and must survive untouched (audit B17): the query is split on
    single quotes and only even-index segments (outside literals) are
    rewritten.
    """
    now = datetime.now()
    dec_epoch = str(
        int(
            datetime(
                now.year - 1 if now.month == 1 else now.year,
                now.month - 1 if now.month > 1 else 12,
                1,
            ).timestamp()
            * 1000
        )
    )
    this_month_epoch = str(int(datetime(now.year, now.month, 1).timestamp() * 1000))
    today_epoch = str(int(datetime(now.year, now.month, now.day).timestamp() * 1000))

    def _rewrite(segment: str) -> str:
        segment = re.sub(r"last month", dec_epoch, segment, flags=re.IGNORECASE)
        segment = re.sub(r"this month", this_month_epoch, segment, flags=re.IGNORECASE)
        segment = re.sub(r"today", today_epoch, segment, flags=re.IGNORECASE)

        date_pattern = re.compile(r"(\d{4}-\d{2}-\d{2})")
        for match in date_pattern.finditer(segment):
            date_str = match.group(1)
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d")
                segment = segment.replace(date_str, str(int(dt.timestamp() * 1000)))
            except ValueError:
                pass
        return segment

    # Sequential walk with an in-literal flag (audit B17 fix round 1):
    # robust against SQL doubled quotes ('') — an escaped pair never flips
    # the literal state, unlike positional even/odd parity.
    out_parts: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(query)
    in_literal = False
    while i < n:
        ch = query[i]
        if ch == "'" and in_literal and i + 1 < n and query[i + 1] == "'":
            buf.append("''")
            i += 2
            continue
        buf.append(ch)
        if ch == "'":
            out_parts.append(_rewrite("".join(buf)) if not in_literal else "".join(buf))
            buf = []
            in_literal = not in_literal
        i += 1
    out_parts.append(_rewrite("".join(buf)) if not in_literal else "".join(buf))
    return "".join(out_parts)


@tool
def app_query(app: str, query: str, user_id: str =  DEFAULT_USER_ID) -> str:
    """Query app data with SQL.

    Args:
        app: App name
        query: SQL query (SELECT, INSERT, UPDATE, DELETE)
        user_id: User identifier

    Returns:
        Query results
    """
    try:
        schema = _get_schema(app, user_id)

        if not schema:
            return f"App '{app}' not found."

        db = _get_db(app, user_id)
        query = _convert_date_in_query(query)

        if not query.strip().upper().startswith("SELECT"):
            return "Only SELECT queries are allowed."

        results = db.raw_query(query)

        if not results:
            return "No results found."

        formatted = []
        for row in results[:20]:
            formatted.append(str(row))

        return "\n".join(formatted) + (
            f"\n\n... and {len(results) - 20} more" if len(results) > 20 else ""
        )

    except Exception as e:
        logger.error(
            "app_query.error", {"app": app, "query": query, "error": str(e)}, user_id=user_id
        )
        return f"Error querying app: {e}"


app_query.annotations = ToolAnnotations(title="Query App Data", open_world=True)


@tool
def app_search_fts(
    app: str, table: str, column: str, query: str, limit: int = 10, user_id: str =  DEFAULT_USER_ID
) -> str:
    """Search app data using keyword search (FTS5).

    Only works on TEXT columns that have been indexed for search.
    Columns like 'description', 'notes', 'content', 'title' are typically indexed.
    Avoid using on columns with choices/options (e.g., 'status', 'category', 'type').

    Args:
        app: App name
        table: Table name
        column: Column name to search (must be TEXT type, not options)
        query: Search query (keywords)
        limit: Max results (default 10)
        user_id: User identifier

    Returns:
        Matching rows with scores
    """
    try:
        schema = _get_schema(app, user_id)

        if not schema:
            return f"App '{app}' not found."

        if table not in schema.tables:
            return f"Table '{table}' not found in app '{app}'."

        table_schema = schema.tables[table]
        if column not in table_schema.columns:
            return f"Column '{column}' not found in table '{table}'. Available: {list(table_schema.columns.keys())}"

        col_type = table_schema.columns.get(column, "").upper()
        if "TEXT" not in col_type:
            return f"Column '{column}' is '{col_type}', not TEXT. FTS5 only works on TEXT columns."

        db = _get_db(app, user_id)
        results = db.search(table, column, query, mode=SearchMode.KEYWORD, limit=limit)

        if not results:
            return f"No results found for '{query}' in {table}.{column}"

        formatted = [f"Found {len(results)} results:"]
        for row in results[:20]:
            formatted.append(str(row))

        return "\n".join(formatted)

    except Exception as e:
        logger.error(
            "app_search_fts.error",
            {"app": app, "table": table, "column": column, "query": query, "error": str(e)},
            user_id=user_id,
        )
        return f"Error searching: {e}"


app_search_fts.annotations = ToolAnnotations(
    title="Search App (FTS5)", read_only=True, open_world=True
)




@tool
def app_import_csv(
    path: str,
    app_name: str,
    table: str | None = None,
    user_id: str = DEFAULT_USER_ID,
) -> str:
    """Import a CSV or XLSX file into app tables.

    Excel formula cells are stored raw (never evaluated). Re-importing the
    same file replaces its prior rows (duplicate import = upsert).

    Args:
        path: Path to the .csv or .xlsx file
        app_name: Target app (created on demand)
        table: Target table name — omit to import every sheet as its own table
        user_id: User identifier

    Returns:
        Success message with imported tables/row counts
    """
    from src.sdk.tools_core.sheets import (
        SheetParseError,
        normalize,
        parse_sheets,
        rows_to_schema,
        source_key,
    )

    user_id = user_id or DEFAULT_USER_ID
    try:
        src = Path(path).expanduser().resolve()
        if not src.exists():
            return f"Error: file not found: {src}"
        sheets = parse_sheets(src)
        if table:
            # Explicit table name overrides the sheet/file name.
            if len(sheets) == 1:
                sheets = {table: next(iter(sheets.values()))}
            elif table in sheets:
                sheets = {table: sheets[table]}
            else:
                return f"Error: sheet '{table}' not found in {src.name}"
        db = _get_db(app_name, user_id)
        key = source_key(src)
        imported: list[str] = []
        for tbl_name, rows in sheets.items():
            if not rows:
                continue
            sql_types = rows_to_schema(rows)
            existing = set(db.list_tables())
            tname = _sanitize_app_name(tbl_name) or "sheet1"
            if tname not in existing:
                db.create_table(tname, sql_types)
            else:
                live_cols = db.get_schema(tname)
                if "_source_key" not in live_cols:
                    db.add_column(tname, "_source_key", "TEXT")
            # Upsert: replace rows previously imported from this source.
            for stale in db.read_query(
                f'SELECT id FROM "{tname}" WHERE _source_key = ?', (key,)  # noqa: S608
            ):
                db.delete(tname, stale["id"])
            n = 0
            for row in normalize(rows, sql_types):
                db.insert(tname, {**row, "_source_key": key})
                n += 1
            imported.append(f"{tname}: {n} rows")
        logger.info(
            "app_import_csv.imported",
            {"source": src.name, "tables": len(imported)},
            user_id=user_id,
        )
        return f"Imported from '{src.name}':\n  - " + "\n  - ".join(imported)
    except SheetParseError as e:
        return str(e)
    except Exception as e:
        logger.error(
            "app_import_csv.error", {"path": path, "error": str(e)}, user_id=user_id
        )
        return f"Error importing file: {e}"


app_import_csv.annotations = ToolAnnotations(title="Import CSV/XLSX", destructive=True)


@tool
async def app_summarize(app: str, user_id: str = DEFAULT_USER_ID) -> str:
    """One-line (<=200 char) LLM description of what a workbook contains.

    Args:
        app: App name to summarize
        user_id: User identifier

    Returns:
        A <=200 character description of the workbook
    """
    try:
        schema = _get_schema(_sanitize_app_name(app), user_id)
        if schema is None or not schema.tables:
            return f"Error: app '{app}' not found"
        parts = [f"{t.name}: {list(t.columns.keys())}" for t in schema.tables.values()]
        context = f"App '{app}' tables:\n" + "\n".join(parts[:10])
        model = get_settings().agent.model
        if not model:
            return f"Workbook '{app}' with {len(schema.tables)} table(s): {parts[0]}"[:200]
        from src.sdk.providers.factory import get_cached_model_provider

        provider = get_cached_model_provider(model)
        result = await provider.chat(
            model=model,
            system="Describe what this structured workbook is in ONE sentence, max 200 characters. Reply with the sentence only.",
            messages=[Message.user(context)],
        )
        content = result.content
        text = content.strip() if isinstance(content, str) else str(content)
        return text[:200] or context[:200]
    except Exception as e:
        logger.error("app_summarize.error", {"app": app, "error": str(e)}, user_id=user_id)
        return f"Error summarizing app: {e}"


app_summarize.annotations = ToolAnnotations(title="Summarize Workbook", read_only=True)

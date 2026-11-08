"""Apply an authenticated alliance scope to the legacy SQL used by the tools."""

from contextvars import ContextVar
from functools import lru_cache
import json

from pymysql.connections import Connection
from pymysql.cursors import DictCursor
import sqlglot
from sqlglot import exp

from workspace_migration import TABLES


current_workspace = ContextVar("current_workspace", default=None)
FEATURE_TABLES = {
    name:name for name in (
        "alliance_claims", "alliance_player_claims", "alliance_claim_comments",
        "alliance_notifications", "alliance_wall_posts", "alliance_wall_replies",
        "alliance_tasks", "alliance_task_occurrences",
    )
}
SCOPED_TABLES = {**TABLES,**FEATURE_TABLES}


def run_worker(job_id, function, *args, **kwargs):
    scope=current_workspace.get()
    if not scope:
        return function(*args,**kwargs)
    token=current_workspace.set({**scope,"uploader_id":scope["actor"]["id"],"actor":None,"worker":True,"job_id":job_id,
                                 "action":"scan_worker"})
    try:
        return function(*args,**kwargs)
    finally:
        current_workspace.reset(token)


def _scope_table(table, alliance_id):
    name = table.name
    if table.db or table.catalog or name not in SCOPED_TABLES:
        raise ValueError(f"Unsupported workspace table: {name}")
    target = SCOPED_TABLES[name]
    alias = table.args.get("alias") or exp.TableAlias(this=exp.to_identifier(name, quoted=True))
    query = (
        exp.select("*")
        .from_(exp.to_table(target))
        .where(exp.column("alliance_id").eq(exp.Literal.number(alliance_id)))
    )
    return exp.Subquery(this=query, alias=alias.copy())


@lru_cache(maxsize=2048)
def scoped_query(query, alliance_id):
    """Translate supported MySQL statements and refuse unsupported table access."""
    if not isinstance(alliance_id, int) or alliance_id < 1:
        raise ValueError("A valid workspace is required.")
    # PyMySQL placeholders are not SQL syntax. Question marks are restored
    # after generation and values are still bound by PyMySQL.
    if "?" in query:
        raise ValueError("Unsupported query placeholder.")
    statement = sqlglot.parse_one(query.replace("%s", "?"), read="mysql")
    if not isinstance(statement, (exp.Select, exp.Insert, exp.Update, exp.Delete)):
        raise ValueError("Unsupported workspace statement.")

    target = None
    if isinstance(statement, exp.Insert):
        schema = statement.this
        if not isinstance(schema, exp.Schema) or not isinstance(schema.this, exp.Table):
            raise ValueError("Insert must name its columns.")
        target = schema.this
        if target.name not in SCOPED_TABLES or target.db or target.catalog:
            raise ValueError("Unsupported workspace insert.")
        target.set("this", exp.to_identifier(SCOPED_TABLES[target.name]))
        schema.set("expressions", [exp.to_identifier("alliance_id"), *schema.expressions])
        values = statement.expression
        if isinstance(values, exp.Values):
            for row in values.expressions:
                row.set("expressions", [exp.Literal.number(alliance_id), *row.expressions])
        elif isinstance(values, exp.Select):
            values.set("expressions", [exp.Literal.number(alliance_id), *values.expressions])
        else:
            raise ValueError("Unsupported workspace insert source.")
    elif isinstance(statement, (exp.Update, exp.Delete)):
        target = statement.this
        if not isinstance(target, exp.Table) or target.name not in SCOPED_TABLES or target.db or target.catalog:
            raise ValueError("Unsupported workspace write.")
        if statement.args.get("using") or statement.args.get("joins"):
            raise ValueError("Multi-table writes are unsupported.")
        alias = target.alias_or_name
        if not target.args.get("alias"):
            target.set("alias", exp.TableAlias(this=exp.to_identifier(alias, quoted=True)))
        target.set("this", exp.to_identifier(SCOPED_TABLES[target.name]))
        condition = exp.column("alliance_id", table=alias).eq(exp.Literal.number(alliance_id))
        statement = statement.where(condition, append=True, copy=False)

    tables = list(statement.find_all(exp.Table))
    for table in tables:
        if table is target:
            continue
        table.replace(_scope_table(table, alliance_id))
    return statement.sql(dialect="mysql").replace("?", "%s")


class ScopedCursor(DictCursor):
    def execute(self, query, args=None):
        workspace = self.connection.workspace_id
        if isinstance(query, bytes):
            query = query.decode("utf-8")
        changed = super().execute(scoped_query(query, workspace), args)
        operation = query.lstrip().split(None, 1)[0].upper()
        if operation in ("INSERT", "UPDATE", "DELETE", "REPLACE") and changed:
            self.connection.record_write(operation, changed, query)
        return changed

    def executemany(self, query, args):
        return sum(self.execute(query, values) for values in args)


class WorkspaceConnection(Connection):
    def __init__(self, *, scope, **kwargs):
        self.workspace_id = scope["id"]
        self.workspace_scope = scope
        self.writes = {}
        super().__init__(cursorclass=ScopedCursor, **kwargs)

    def record_write(self, operation, count, query):
        if self.workspace_scope.get("worker") and operation == "UPDATE" and "progress" in query and "status" not in query:
            return
        statement=sqlglot.parse_one(query.replace("%s","?"),read="mysql")
        target=statement.this.this if isinstance(statement.this,exp.Schema) else statement.this
        table=target.name if isinstance(target,exp.Table) else "workspace"
        key=operation+" "+table
        self.writes[key] = self.writes.get(key, 0) + int(count)

    def commit(self):
        if self.writes:
            actor = self.workspace_scope.get("actor")
            details = {"counts": self.writes}
            if self.workspace_scope.get("job_id"):
                details["job_id"] = self.workspace_scope["job_id"]
            with super().cursor(DictCursor) as cursor:
                cursor.execute("""INSERT INTO alliance_audit_log
                    (alliance_id,actor_id,actor_name,action,details) VALUES (%s,%s,%s,%s,%s)""",
                    (self.workspace_id, actor["id"] if actor else None,
                     actor["display_name"] if actor else "System",
                     self.workspace_scope.get("action", "workspace_change"), json.dumps(details)))
        super().commit()
        self.writes = {}

    def rollback(self):
        self.writes = {}
        return super().rollback()

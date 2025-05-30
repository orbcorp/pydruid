import decimal
from sqlalchemy import text, types, util
from sqlalchemy.engine import default
from sqlalchemy.sql import compiler

import pydruid.db

RESERVED_SCHEMAS = ["INFORMATION_SCHEMA"]


jdbc_type_map = {
    -6: types.BigInteger,
    -5: types.BigInteger,
    1: types.String,
    3: types.Float,
    4: types.BigInteger,
    5: types.BigInteger,
    6: types.Float,
    7: types.Float,
    8: types.Float,
    12: types.String,
    16: types.Boolean,
    91: types.DATE,
    93: types.TIMESTAMP,
    1111: types.BLOB,
}


class UniversalSet(object):
    def __contains__(self, item):
        return True


class DruidIdentifierPreparer(compiler.IdentifierPreparer):
    reserved_words = UniversalSet()


class DruidCompiler(compiler.SQLCompiler):
    pass


class DruidTypeCompiler(compiler.GenericTypeCompiler):
    def visit_REAL(self, type_, **kwargs):
        return "DOUBLE"

    def visit_NUMERIC(self, type_, **kwargs):
        return "LONG"

    visit_DECIMAL = visit_NUMERIC
    visit_INTEGER = visit_NUMERIC
    visit_SMALLINT = visit_NUMERIC
    visit_BIGINT = visit_NUMERIC
    visit_BOOLEAN = visit_NUMERIC
    visit_TIMESTAMP = visit_NUMERIC
    visit_DATE = visit_NUMERIC

    def visit_CHAR(self, type_, **kwargs):
        return "STRING"

    visit_NCHAR = visit_CHAR
    visit_VARCHAR = visit_CHAR
    visit_NVARCHAR = visit_CHAR
    visit_TEXT = visit_CHAR

    def visit_DATETIME(self, type_, **kwargs):
        return "LONG"

    def visit_TIME(self, type_, **kwargs):
        return "LONG"

    def visit_BLOB(self, type_, **kwargs):
        return "COMPLEX"

    visit_CLOB = visit_BLOB
    visit_NCLOB = visit_BLOB
    visit_VARBINARY = visit_BLOB
    visit_BINARY = visit_BLOB


class DruidDialect(default.DefaultDialect):

    name = "druid"
    scheme = "http"
    driver = "rest"
    user = None
    password = None
    preparer = DruidIdentifierPreparer
    statement_compiler = DruidCompiler
    type_compiler = DruidTypeCompiler
    supports_alter = False
    supports_pk_autoincrement = False
    supports_default_values = False
    supports_empty_insert = False
    supports_unicode_statements = True
    supports_unicode_binds = True
    returns_unicode_strings = True
    description_encoding = None
    supports_native_boolean = True

    def __init__(self, context=None, *args, **kwargs):
        super(DruidDialect, self).__init__(*args, **kwargs)
        self.context = context or {}

    @classmethod
    def dbapi(cls):
        return pydruid.db

    def create_connect_args(self, url):
        standard_args = {k:v for k, v in url.query.items() if k not in ["ssl_verify_cert", "parse_float_as_decimal"]}
        kwargs = {
            **standard_args,
            "host": url.host,
            "port": url.port or 8082,
            "user": url.username or None,
            "password": url.password or None,
            "path": url.database,
            "scheme": self.scheme,
            "context": self.context,
            "header": url.query.get("header") == "true",
            "parse_float": decimal.Decimal if url.query.get("parse_float_as_decimal", None) == "true" else float,
            "ssl_verify_cert": url.query.get("ssl_verify_cert", True),
        }
        return ([], kwargs)

    def do_ping(self, dbapi_connection) -> bool:
        """
        Return if the database can be reached.
        """
        try:
            dbapi_connection.execute("SELECT 1")
        except Exception:
            return False

        return True

    def get_schema_names(self, connection, **kwargs):
        # Each Druid datasource appears as a table in the "druid" schema. This
        # is also the default schema, so Druid datasources can be referenced as
        # either druid.dataSourceName or simply dataSourceName.
        result = connection.execute(
            text("SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA")
        )

        return [
            row.SCHEMA_NAME for row in result if row.SCHEMA_NAME not in RESERVED_SCHEMAS
        ]

    def has_table(self, connection, table_name, schema=None):
        query = """
            SELECT COUNT(*) > 0 AS exists_
              FROM INFORMATION_SCHEMA.TABLES
             WHERE TABLE_NAME = '{table_name}'
        """.format(
            table_name=table_name
        )

        result = connection.execute(text(query))
        return result.fetchone().exists_

    def get_table_names(self, connection, schema=None, **kwargs):
        query = "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES"
        if schema:
            query = "{query} WHERE TABLE_SCHEMA = '{schema}'".format(
                query=query, schema=schema
            )

        result = connection.execute(text(query))
        return [row.TABLE_NAME for row in result]

    def get_view_names(self, connection, schema=None, **kwargs):
        return []

    def get_table_options(self, connection, table_name, schema=None, **kwargs):
        return {}

    def get_columns(self, connection, table_name, schema=None, **kwargs):
        query = """
            SELECT COLUMN_NAME,
                   JDBC_TYPE,
                   IS_NULLABLE,
                   COLUMN_DEFAULT
              FROM INFORMATION_SCHEMA.COLUMNS
             WHERE TABLE_NAME = '{table_name}'
        """.format(
            table_name=table_name
        )
        if schema:
            query = "{query} AND TABLE_SCHEMA = '{schema}'".format(
                query=query, schema=schema
            )

        result = connection.execute(text(query))

        return [
            {
                "name": row.COLUMN_NAME,
                "type": self._map_jdbc_type(row),
                "nullable": get_is_nullable(row.IS_NULLABLE),
                "default": get_default(row.COLUMN_DEFAULT),
            }
            for row in result
        ]

    def get_pk_constraint(self, connection, table_name, schema=None, **kwargs):
        return {"constrained_columns": [], "name": None}

    def get_foreign_keys(self, connection, table_name, schema=None, **kwargs):
        return []

    def get_check_constraints(self, connection, table_name, schema=None, **kwargs):
        return []

    def get_table_comment(self, connection, table_name, schema=None, **kwargs):
        return {"text": ""}

    def get_indexes(self, connection, table_name, schema=None, **kwargs):
        return []

    def get_unique_constraints(self, connection, table_name, schema=None, **kwargs):
        return []

    def get_view_definition(self, connection, view_name, schema=None, **kwargs):
        pass

    def do_rollback(self, dbapi_connection):
        pass

    def _check_unicode_returns(self, connection, additional_tests=None):
        return True

    def _check_unicode_description(self, connection):
        return True

    def _map_jdbc_type(self, row):
        if row.JDBC_TYPE in jdbc_type_map:
            return jdbc_type_map[row.JDBC_TYPE]
        util.warn(
            "Failed to map column '{row.COLUMN_NAME}' with "
            "JDBC type '{row.JDBC_TYPE}' to a sqlalchemy type.".format(row=row)
        )
        return types.NullType


DruidHTTPDialect = DruidDialect


class DruidHTTPSDialect(DruidDialect):

    scheme = "https"


def get_is_nullable(druid_is_nullable):
    # this should be 'YES' or 'NO'; we default to no
    return druid_is_nullable.lower() == "yes"


def get_default(druid_column_default):
    # currently unused, returns ''
    return str(druid_column_default) if druid_column_default != "" else None

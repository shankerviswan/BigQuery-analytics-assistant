from app.sql_validator import validate_sql, extract_tables

ALLOWED = {
    "bigquery-public-data.thelook_ecommerce.orders",
    "bigquery-public-data.thelook_ecommerce.order_items",
    "bigquery-public-data.thelook_ecommerce.products",
}


def test_allows_select_from_allowlisted_table():
    sql = "SELECT COUNT(*) FROM `bigquery-public-data.thelook_ecommerce.orders`"
    assert validate_sql(sql, ALLOWED).ok


def test_rejects_delete():
    sql = "DELETE FROM `bigquery-public-data.thelook_ecommerce.orders` WHERE order_id = 1"
    assert not validate_sql(sql, ALLOWED).ok


def test_rejects_unapproved_table():
    sql = "SELECT * FROM `bigquery-public-data.usa_names.usa_1910_2013`"
    result = validate_sql(sql, ALLOWED)
    assert not result.ok
    assert "Unauthorized" in result.reason


def test_rejects_multiple_statements():
    assert not validate_sql("SELECT 1; SELECT 2", ALLOWED).ok


def test_rejects_select_without_table():
    assert not validate_sql("SELECT 1", ALLOWED).ok


def test_allows_cte_against_allowlisted_table():
    sql = """
    WITH t AS (
      SELECT order_id FROM `bigquery-public-data.thelook_ecommerce.orders`
    )
    SELECT * FROM t
    """
    result = validate_sql(sql, ALLOWED)
    assert result.ok, result.reason


def test_allows_union():
    sql = """
    SELECT order_id FROM `bigquery-public-data.thelook_ecommerce.orders`
    UNION ALL
    SELECT order_id FROM `bigquery-public-data.thelook_ecommerce.orders`
    """
    result = validate_sql(sql, ALLOWED)
    assert result.ok, result.reason


def test_rejects_external_query():
    sql = "SELECT * FROM EXTERNAL_QUERY('conn', 'SELECT 1')"
    assert not validate_sql(sql, ALLOWED).ok


def test_extract_tables_excludes_cte_alias():
    sql = """
    WITH t AS (
      SELECT order_id FROM `bigquery-public-data.thelook_ecommerce.orders`
    )
    SELECT * FROM t
    """
    tables = extract_tables(sql)
    assert "bigquery-public-data.thelook_ecommerce.orders" in tables
    assert "t" not in tables


def test_cte_alias_cannot_hide_unauthorized_qualified_table():
    """P0: WITH users AS (...) SELECT * FROM project.dataset.users must still be blocked."""
    sql = """
    WITH users AS (
      SELECT order_id FROM `bigquery-public-data.thelook_ecommerce.orders`
    )
    SELECT * FROM `bigquery-public-data.thelook_ecommerce.users`
    """
    result = validate_sql(sql, ALLOWED)
    assert not result.ok, "CTE alias must not authorize a qualified forbidden table"
    assert "Unauthorized" in result.reason


def test_unqualified_cte_ref_is_allowed():
    sql = """
    WITH users AS (
      SELECT order_id FROM `bigquery-public-data.thelook_ecommerce.orders`
    )
    SELECT * FROM users
    """
    result = validate_sql(sql, ALLOWED)
    assert result.ok, result.reason

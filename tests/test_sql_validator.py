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
    sql = "DELETE FROM `bigquery-public-data.thelook_ecommerce.orders` WHERE id = 1"
    assert not validate_sql(sql, ALLOWED).ok


def test_rejects_unapproved_table():
    sql = "SELECT * FROM `bigquery-public-data.usa_names.usa_1910_2013`"
    result = validate_sql(sql, ALLOWED)
    assert not result.ok
    assert "Unauthorized" in result.reason


def test_rejects_multiple_statements():
    sql = "SELECT 1; SELECT 2"
    assert not validate_sql(sql, ALLOWED).ok


def test_rejects_select_without_table():
    assert not validate_sql("SELECT 1", ALLOWED).ok


def test_extract_tables():
    sql = (
        "SELECT o.id FROM `bigquery-public-data.thelook_ecommerce.orders` o "
        "JOIN `bigquery-public-data.thelook_ecommerce.order_items` oi ON o.id = oi.order_id"
    )
    tables = extract_tables(sql)
    assert "bigquery-public-data.thelook_ecommerce.orders" in tables
    assert "bigquery-public-data.thelook_ecommerce.order_items" in tables

"""Adversarial metric alignment cases (Claude review regressions)."""
from app.metric_check import check_metric_alignment

ORDERS = "bigquery-public-data.thelook_ecommerce.orders"
ORDER_ITEMS = "bigquery-public-data.thelook_ecommerce.order_items"
PRODUCTS = "bigquery-public-data.thelook_ecommerce.products"

cancelled_metric = {
    "tables": [ORDERS],
    "agg": "count_distinct",
    "column": "order_id",
    "time_column": "created_at",
    "filters": [
        {"column": "status", "op": "eq", "value": "cancelled", "case_insensitive": True}
    ],
}

order_count_metric = {
    "tables": [ORDERS],
    "agg": "count_distinct",
    "column": "order_id",
    "time_column": "created_at",
    "filters": [],
}

item_sales_metric = {
    "tables": [ORDER_ITEMS],
    "agg": "sum",
    "column": "sale_price",
    "time_column": "created_at",
    "filters": [
        {"column": "status", "op": "not_in", "value": ["cancelled", "returned"], "case_insensitive": True}
    ],
}

category_metric = {
    "tables": [ORDER_ITEMS, PRODUCTS],
    "agg": "sum",
    "column": "sale_price",
    "time_column": "created_at",
    "group_by": ["category"],
    "filters": [
        {"column": "status", "op": "not_in", "value": ["cancelled", "returned"], "case_insensitive": True}
    ],
}


def test_rejects_neq_cancelled():
    sql = f"""
    SELECT COUNT(DISTINCT o.order_id) AS cancelled_order_count
    FROM `{ORDERS}` AS o
    WHERE o.status != 'cancelled'
      AND o.created_at >= @start_date AND o.created_at < @end_date
    """
    assert not check_metric_alignment(sql, cancelled_metric).ok


def test_rejects_gender_literal_trick():
    sql = f"""
    SELECT COUNT(DISTINCT o.order_id) AS cancelled_order_count
    FROM `{ORDERS}` AS o
    WHERE o.status <> 'Complete' AND o.gender = 'cancelled'
      AND o.created_at >= @start_date AND o.created_at < @end_date
    """
    assert not check_metric_alignment(sql, cancelled_metric).ok


def test_rejects_inverted_not_in():
    sql = f"""
    SELECT SUM(oi.sale_price) AS item_sales_value
    FROM `{ORDER_ITEMS}` AS oi
    WHERE oi.status IN ('cancelled', 'returned')
      AND oi.created_at >= @start_date AND oi.created_at < @end_date
    """
    assert not check_metric_alignment(sql, item_sales_metric).ok


def test_rejects_wrong_count_column():
    sql = f"""
    SELECT COUNT(DISTINCT o.user_id) AS order_count
    FROM `{ORDERS}` AS o
    WHERE o.created_at >= @start_date AND o.created_at < @end_date
    """
    assert not check_metric_alignment(sql, order_count_metric).ok


def test_rejects_max_without_where_bounds():
    sql = f"""
    SELECT MAX(o.created_at) AS order_count
    FROM `{ORDERS}` AS o
    """
    assert not check_metric_alignment(sql, order_count_metric).ok


def test_rejects_hardcoded_date_literals():
    sql = f"""
    SELECT COUNT(DISTINCT o.order_id) AS order_count
    FROM `{ORDERS}` AS o
    WHERE o.created_at >= '2019-01-01' AND o.created_at < '2019-02-01'
    """
    assert not check_metric_alignment(sql, order_count_metric).ok


def test_rejects_missing_group_by_for_category():
    sql = f"""
    SELECT SUM(oi.sale_price) AS category_item_sales
    FROM `{ORDER_ITEMS}` AS oi
    CROSS JOIN `{PRODUCTS}` AS p
    WHERE oi.status NOT IN ('cancelled', 'returned')
      AND oi.created_at >= @start_date AND oi.created_at < @end_date
    """
    assert not check_metric_alignment(sql, category_metric).ok


def test_accepts_valid_cancelled():
    sql = f"""
    SELECT COUNT(DISTINCT o.order_id) AS cancelled_order_count
    FROM `{ORDERS}` AS o
    WHERE LOWER(o.status) = 'cancelled'
      AND o.created_at >= @start_date AND o.created_at < @end_date
    """
    result = check_metric_alignment(sql, cancelled_metric)
    assert result.ok, result.reason


def test_accepts_valid_order_count():
    sql = f"""
    SELECT COUNT(DISTINCT o.order_id) AS order_count
    FROM `{ORDERS}` AS o
    WHERE o.created_at >= @start_date AND o.created_at < @end_date
    """
    result = check_metric_alignment(sql, order_count_metric)
    assert result.ok, result.reason

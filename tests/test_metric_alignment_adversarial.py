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


def test_rejects_substring_column_in_aggregate():
    sql = f"""
    SELECT COUNT(DISTINCT o.shipped_order_id) AS order_count
    FROM `{ORDERS}` AS o
    WHERE o.created_at >= @start_date AND o.created_at < @end_date
    """
    assert not check_metric_alignment(sql, order_count_metric).ok


def test_rejects_wide_in_for_eq():
    sql = f"""
    SELECT COUNT(DISTINCT o.order_id) AS cancelled_order_count
    FROM `{ORDERS}` AS o
    WHERE o.status IN ('cancelled', 'complete')
      AND o.created_at >= @start_date AND o.created_at < @end_date
    """
    assert not check_metric_alignment(sql, cancelled_metric).ok


def test_rejects_partial_not_in():
    sql = f"""
    SELECT SUM(sale_price) AS item_sales_value
    FROM `{ORDER_ITEMS}`
    WHERE LOWER(status) NOT IN ('cancelled')
      AND created_at >= @start_date AND created_at < @end_date
    """
    assert not check_metric_alignment(sql, item_sales_metric).ok


def test_rejects_decoy_cte_with_unfiltered_outer_query():
    sql = f"""
    WITH decoy AS (
      SELECT * FROM `{ORDERS}` AS o
      WHERE LOWER(o.status) = 'cancelled'
        AND o.created_at >= @start_date AND o.created_at < @end_date
    )
    SELECT COUNT(DISTINCT o.order_id) FROM `{ORDERS}` AS o
    """
    assert not check_metric_alignment(sql, cancelled_metric).ok


def test_rejects_params_that_are_not_bounds_on_the_time_column():
    sql = f"""
    SELECT COUNT(DISTINCT o.order_id)
    FROM `{ORDERS}` AS o
    WHERE LOWER(o.status) = 'cancelled'
      AND @start_date < @end_date
    """
    assert not check_metric_alignment(sql, cancelled_metric).ok


def test_rejects_inclusive_end_bound():
    sql = f"""
    SELECT COUNT(DISTINCT o.order_id)
    FROM `{ORDERS}` AS o
    WHERE o.created_at > @start_date AND o.created_at <= @end_date
    """
    assert not check_metric_alignment(sql, order_count_metric).ok


def test_rejects_union_branch_without_bounds():
    sql = f"""
    SELECT COUNT(DISTINCT o.order_id) FROM `{ORDERS}` AS o
    WHERE o.created_at >= @start_date AND o.created_at < @end_date
    UNION ALL
    SELECT COUNT(DISTINCT o.order_id) FROM `{ORDERS}` AS o
    """
    assert not check_metric_alignment(sql, order_count_metric).ok


def test_rejects_cross_join_even_with_group_by():
    sql = f"""
    SELECT p.category, SUM(oi.sale_price)
    FROM `{ORDER_ITEMS}` AS oi
    CROSS JOIN `{PRODUCTS}` AS p
    WHERE LOWER(oi.status) NOT IN ('cancelled', 'returned')
      AND oi.created_at >= @start_date AND oi.created_at < @end_date
    GROUP BY p.category
    """
    assert not check_metric_alignment(sql, category_metric).ok


def test_accepts_parenthesized_lower_eq():
    sql = f"""
    SELECT COUNT(DISTINCT o.order_id) AS cancelled_order_count
    FROM `{ORDERS}` AS o
    WHERE (LOWER(o.status) = 'cancelled')
      AND o.created_at >= @start_date AND o.created_at < @end_date
    """
    result = check_metric_alignment(sql, cancelled_metric)
    assert result.ok, result.reason


def test_accepts_filters_applied_in_selected_cte():
    sql = f"""
    WITH filtered AS (
      SELECT * FROM `{ORDERS}` AS o
      WHERE LOWER(o.status) = 'cancelled'
        AND o.created_at >= @start_date AND o.created_at < @end_date
    )
    SELECT COUNT(DISTINCT order_id) AS cancelled_order_count FROM filtered
    """
    result = check_metric_alignment(sql, cancelled_metric)
    assert result.ok, result.reason


def test_accepts_not_in_via_lower_inequalities():
    sql = f"""
    SELECT SUM(sale_price) AS item_sales_value
    FROM `{ORDER_ITEMS}`
    WHERE LOWER(status) != 'cancelled' AND LOWER(status) != 'returned'
      AND created_at >= @start_date AND created_at < @end_date
    """
    result = check_metric_alignment(sql, item_sales_metric)
    assert result.ok, result.reason


def test_accepts_equijoin_category_sales():
    sql = f"""
    SELECT p.category, SUM(oi.sale_price) AS category_item_sales
    FROM `{ORDER_ITEMS}` AS oi
    JOIN `{PRODUCTS}` AS p ON oi.product_id = p.id
    WHERE LOWER(oi.status) NOT IN ('cancelled', 'returned')
      AND oi.created_at >= @start_date AND oi.created_at < @end_date
    GROUP BY p.category
    """
    result = check_metric_alignment(sql, category_metric)
    assert result.ok, result.reason


def test_rejects_self_join_on_one_table_sum():
    sql = f"""
    SELECT SUM(a.sale_price) AS item_sales_value
    FROM `{ORDER_ITEMS}` AS a
    JOIN `{ORDER_ITEMS}` AS b ON a.id = b.id
    WHERE LOWER(a.status) NOT IN ('cancelled', 'returned')
      AND a.created_at >= @start_date AND a.created_at < @end_date
    """
    result = check_metric_alignment(sql, item_sales_metric)
    assert not result.ok
    assert "one-table" in result.reason


def test_rejects_repeated_physical_table_on_multi_table_metric():
    sql = f"""
    SELECT p.category, SUM(oi.sale_price) AS category_item_sales
    FROM `{ORDER_ITEMS}` AS oi
    JOIN `{ORDER_ITEMS}` AS oi2 ON oi.id = oi2.id
    JOIN `{PRODUCTS}` AS p ON oi.product_id = p.id
    WHERE LOWER(oi.status) NOT IN ('cancelled', 'returned')
      AND oi.created_at >= @start_date AND oi.created_at < @end_date
    GROUP BY p.category
    """
    result = check_metric_alignment(sql, category_metric)
    assert not result.ok
    assert "same table twice" in result.reason


def test_rejects_extra_allow_listed_table():
    sql = f"""
    SELECT COUNT(DISTINCT o.order_id) AS order_count
    FROM `{ORDERS}` AS o
    JOIN `{PRODUCTS}` AS p ON o.user_id = p.id
    WHERE o.created_at >= @start_date AND o.created_at < @end_date
    """
    result = check_metric_alignment(sql, order_count_metric)
    assert not result.ok
    assert "outside the metric definition" in result.reason


def test_rejects_uppercase_literal_under_lower():
    sql = f"""
    SELECT COUNT(DISTINCT o.order_id) AS cancelled_order_count
    FROM `{ORDERS}` AS o
    WHERE LOWER(o.status) = 'CANCELLED'
      AND o.created_at >= @start_date AND o.created_at < @end_date
    """
    result = check_metric_alignment(sql, cancelled_metric)
    assert not result.ok
    assert "filter" in result.reason


def test_rejects_uppercase_not_in_under_lower():
    sql = f"""
    SELECT SUM(sale_price) AS item_sales_value
    FROM `{ORDER_ITEMS}`
    WHERE LOWER(status) NOT IN ('CANCELLED', 'RETURNED')
      AND created_at >= @start_date AND created_at < @end_date
    """
    result = check_metric_alignment(sql, item_sales_metric)
    assert not result.ok
    assert "filter" in result.reason


def test_rejects_coalesce_literal_trick():
    sql = f"""
    SELECT COUNT(DISTINCT o.order_id) AS cancelled_order_count
    FROM `{ORDERS}` AS o
    WHERE LOWER(o.status) = COALESCE('x', 'cancelled')
      AND o.created_at >= @start_date AND o.created_at < @end_date
    """
    result = check_metric_alignment(sql, cancelled_metric)
    assert not result.ok
    assert "filter" in result.reason


def test_rejects_scaled_sum():
    sql = f"""
    SELECT SUM(sale_price) * 1000 AS item_sales_value
    FROM `{ORDER_ITEMS}`
    WHERE LOWER(status) NOT IN ('cancelled', 'returned')
      AND created_at >= @start_date AND created_at < @end_date
    """
    result = check_metric_alignment(sql, item_sales_metric)
    assert not result.ok
    assert "aggregate" in result.reason


def test_rejects_cte_sum_rescaled_in_outer_select():
    sql = f"""
    WITH totals AS (
      SELECT SUM(sale_price) AS item_sales_value
      FROM `{ORDER_ITEMS}`
      WHERE LOWER(status) NOT IN ('cancelled', 'returned')
        AND created_at >= @start_date AND created_at < @end_date
    )
    SELECT item_sales_value * 1000 FROM totals
    """
    result = check_metric_alignment(sql, item_sales_metric)
    assert not result.ok
    assert "aggregate" in result.reason


def test_rejects_count_without_distinct():
    sql = f"""
    SELECT COUNT(o.order_id) AS order_count
    FROM `{ORDERS}` AS o
    WHERE o.created_at >= @start_date AND o.created_at < @end_date
    """
    result = check_metric_alignment(sql, order_count_metric)
    assert not result.ok
    assert "aggregate" in result.reason


def test_rejects_lower_when_metric_is_not_case_insensitive():
    metric = {
        "tables": [ORDERS],
        "agg": "count_distinct",
        "column": "order_id",
        "time_column": "created_at",
        "filters": [
            {"column": "status", "op": "eq", "value": "Cancelled", "case_insensitive": False}
        ],
    }
    sql = f"""
    SELECT COUNT(DISTINCT o.order_id) AS order_count
    FROM `{ORDERS}` AS o
    WHERE LOWER(o.status) = 'Cancelled'
      AND o.created_at >= @start_date AND o.created_at < @end_date
    """
    result = check_metric_alignment(sql, metric)
    assert not result.ok
    assert "filter" in result.reason


def test_accepts_valid_order_count():
    sql = f"""
    SELECT COUNT(DISTINCT o.order_id) AS order_count
    FROM `{ORDERS}` AS o
    WHERE o.created_at >= @start_date AND o.created_at < @end_date
    """
    result = check_metric_alignment(sql, order_count_metric)
    assert result.ok, result.reason

"""Tests for the Informatica expression → SQL translator."""

from pipeshift.translator import translate_expression


class TestIIF:
    def test_simple_iif(self):
        result = translate_expression("IIF(STATUS = 'A', 'ACTIVE', 'INACTIVE')")
        assert "CASE WHEN" in result
        assert "STATUS = 'A'" in result
        assert "THEN 'ACTIVE'" in result
        assert "ELSE 'INACTIVE'" in result
        assert result.endswith("END")

    def test_nested_iif(self):
        expr = "IIF(STATUS = 'A', 'ACTIVE', IIF(STATUS = 'I', 'INACTIVE', 'UNKNOWN'))"
        result = translate_expression(expr)
        assert result.count("CASE WHEN") == 2
        assert "ELSE CASE WHEN" in result

    def test_iif_with_isnull(self):
        expr = "IIF(ISNULL(CUSTOMER_ID), 'MISSING', 'FOUND')"
        result = translate_expression(expr)
        assert "IS NULL" in result
        assert "CASE WHEN" in result


class TestDecode:
    def test_simple_decode(self):
        expr = "DECODE(STATUS, 'A', 'ACTIVE', 'I', 'INACTIVE', 'UNKNOWN')"
        result = translate_expression(expr)
        assert "CASE STATUS" in result
        assert "WHEN 'A' THEN 'ACTIVE'" in result
        assert "WHEN 'I' THEN 'INACTIVE'" in result
        assert "ELSE 'UNKNOWN'" in result

    def test_decode_true(self):
        expr = "DECODE(TRUE, STATUS = 'A', 'ACTIVE', STATUS = 'I', 'INACTIVE', 'OTHER')"
        result = translate_expression(expr)
        assert "CASE" in result
        assert "WHEN STATUS = 'A' THEN 'ACTIVE'" in result
        assert "ELSE 'OTHER'" in result


class TestFunctions:
    def test_isnull(self):
        assert "IS NULL" in translate_expression("ISNULL(X)")

    def test_nvl(self):
        result = translate_expression("NVL(NAME, 'DEFAULT')")
        assert "COALESCE(NAME, 'DEFAULT')" == result

    def test_sysdate(self):
        assert translate_expression("SYSDATE") == "CURRENT_TIMESTAMP"

    def test_ltrim_rtrim_combo(self):
        result = translate_expression("LTRIM(RTRIM(NAME))")
        assert result == "TRIM(NAME)"

    def test_substr(self):
        result = translate_expression("SUBSTR(NAME, 1, 5)")
        assert result == "SUBSTRING(NAME, 1, 5)"

    def test_to_integer(self):
        result = translate_expression("TO_INTEGER(AMOUNT)")
        assert result == "CAST(AMOUNT AS INTEGER)"

    def test_add_to_date_days(self):
        result = translate_expression("ADD_TO_DATE(ORDER_DATE, 'DD', 30)")
        assert result == "DATEADD(day, 30, ORDER_DATE)"

    def test_add_to_date_months(self):
        result = translate_expression("ADD_TO_DATE(START_DATE, 'MM', 3)")
        assert result == "DATEADD(month, 3, START_DATE)"

    def test_concat_passthrough(self):
        # || is already SQL standard
        expr = "FIRST_NAME || ' ' || LAST_NAME"
        assert translate_expression(expr) == expr

    def test_reg_replace(self):
        result = translate_expression("REG_REPLACE(PHONE, '[^0-9]', '')")
        assert "REGEXP_REPLACE" in result


class TestComplexExpressions:
    def test_full_name_derivation(self):
        expr = "LTRIM(RTRIM(FIRST_NAME)) || ' ' || LTRIM(RTRIM(LAST_NAME))"
        result = translate_expression(expr)
        assert "TRIM(FIRST_NAME)" in result
        assert "TRIM(LAST_NAME)" in result
        assert "||" in result

    def test_customer_status_from_sample(self):
        expr = "IIF(STATUS = 'A', 'ACTIVE', IIF(STATUS = 'I', 'INACTIVE', IIF(STATUS = 'S', 'SUSPENDED', 'UNKNOWN')))"
        result = translate_expression(expr)
        assert "CASE WHEN STATUS = 'A' THEN 'ACTIVE'" in result
        assert "'SUSPENDED'" in result
        assert "'UNKNOWN'" in result

    def test_passthrough_expression(self):
        # Simple column reference should pass through unchanged
        assert translate_expression("CUSTOMER_ID") == "CUSTOMER_ID"
        assert translate_expression("") == ""


class TestNestedFunctions:
    """Regression tests for nested function handling (previously broken)."""

    def test_isnull_with_nested_function(self):
        result = translate_expression("ISNULL(SUBSTR(NAME, 1, 3))")
        assert "IS NULL" in result
        assert "SUBSTRING(NAME, 1, 3)" in result

    def test_nvl_with_nested_function(self):
        result = translate_expression("NVL(SUBSTR(X, 1, 3), 'N/A')")
        assert "COALESCE" in result
        assert "SUBSTRING(X, 1, 3)" in result

    def test_isnull_with_concat(self):
        result = translate_expression("IIF(ISNULL(A || B), 'EMPTY', A || B)")
        assert "IS NULL" in result
        assert "CASE WHEN" in result

    def test_doubled_quote_in_string(self):
        """Informatica uses '' for escaped quotes, not backslash."""
        result = translate_expression("IIF(NAME = 'O''Brien', 'YES', 'NO')")
        assert "CASE WHEN" in result
        assert "O''Brien" in result

"""Tests for TO_DATE/TO_CHAR format translation and $$PARAM variable handling."""

from pipeshift.translator import translate_expression


class TestToDateFormat:
    """Test TO_DATE format string translation."""

    def test_standard_format_passthrough(self):
        # YYYY-MM-DD is the same in Informatica and SQL
        result = translate_expression("TO_DATE(MY_COL, 'YYYY-MM-DD')")
        assert result == "TO_DATE(MY_COL, 'YYYY-MM-DD')"

    def test_rr_to_yy(self):
        # Informatica RR (2-digit year with century pivot) → SQL YY
        result = translate_expression("TO_DATE(DATE_STR, 'MM/DD/RR')")
        assert "'MM/DD/YY'" in result

    def test_rrrr_to_yyyy(self):
        # Informatica RRRR → YYYY
        result = translate_expression("TO_DATE(DATE_STR, 'RRRR-MM-DD')")
        assert "'YYYY-MM-DD'" in result

    def test_hh_to_hh24(self):
        # Informatica HH (ambiguous) → HH24
        result = translate_expression("TO_DATE(TS, 'YYYY-MM-DD HH:MI:SS')")
        assert "'YYYY-MM-DD HH24:MI:SS'" in result

    def test_hh24_stays_hh24(self):
        result = translate_expression("TO_DATE(TS, 'YYYY-MM-DD HH24:MI:SS')")
        assert "'YYYY-MM-DD HH24:MI:SS'" in result

    def test_full_timestamp_format(self):
        result = translate_expression("TO_DATE(TS, 'MM/DD/RRRR HH:MI:SS')")
        assert "'MM/DD/YYYY HH24:MI:SS'" in result

    def test_preserves_separators(self):
        result = translate_expression("TO_DATE(D, 'YYYY/MM/DD')")
        assert "'YYYY/MM/DD'" in result

    def test_single_arg_passthrough(self):
        # TO_DATE with single arg (no format) passes through
        result = translate_expression("TO_DATE(MY_COL)")
        assert result == "TO_DATE(MY_COL)"

    def test_non_quoted_format_passthrough(self):
        # If format isn't a quoted string, leave it alone
        result = translate_expression("TO_DATE(MY_COL, FMT_VAR)")
        assert result == "TO_DATE(MY_COL, FMT_VAR)"


class TestToCharFormat:
    """Test TO_CHAR format string translation."""

    def test_basic_date_format(self):
        result = translate_expression("TO_CHAR(ORDER_DATE, 'YYYY-MM-DD')")
        assert result == "TO_CHAR(ORDER_DATE, 'YYYY-MM-DD')"

    def test_rr_conversion(self):
        result = translate_expression("TO_CHAR(SYSDATE, 'DD-MON-RR')")
        # SYSDATE → CURRENT_TIMESTAMP, RR → YY
        assert "CURRENT_TIMESTAMP" in result
        assert "'DD-MON-YY'" in result

    def test_time_format(self):
        result = translate_expression("TO_CHAR(TS, 'HH:MI:SS')")
        assert "'HH24:MI:SS'" in result


class TestVariableTranslation:
    """Test $$PARAM_NAME → dbt var() translation."""

    def test_simple_param(self):
        result = translate_expression("$$START_DATE")
        assert result == "{{ var('start_date') }}"

    def test_param_in_expression(self):
        result = translate_expression("ORDER_DATE >= $$CUTOFF_DATE")
        assert "{{ var('cutoff_date') }}" in result
        assert "ORDER_DATE >=" in result

    def test_multiple_params(self):
        result = translate_expression("AMOUNT BETWEEN $$MIN_AMT AND $$MAX_AMT")
        assert "{{ var('min_amt') }}" in result
        assert "{{ var('max_amt') }}" in result

    def test_param_in_iif(self):
        result = translate_expression("IIF(STATUS = $$DEFAULT_STATUS, 'YES', 'NO')")
        assert "{{ var('default_status') }}" in result
        assert "CASE WHEN" in result

    def test_single_dollar_not_translated(self):
        # $PMFOLDER and other single-$ vars are NOT translated to dbt vars
        result = translate_expression("$PMFOLDER")
        assert result == "$PMFOLDER"
        assert "var(" not in result

    def test_param_with_underscores(self):
        result = translate_expression("$$MY_LONG_PARAM_NAME")
        assert result == "{{ var('my_long_param_name') }}"

    def test_param_lowercased(self):
        result = translate_expression("$$UPPER_CASE")
        assert "upper_case" in result
        assert "UPPER_CASE" not in result


class TestCombinedTranslation:
    """Test combinations of format + variables + other functions."""

    def test_to_date_with_param(self):
        result = translate_expression("TO_DATE($$START_DATE, 'RRRR-MM-DD')")
        assert "{{ var('start_date') }}" in result
        assert "'YYYY-MM-DD'" in result

    def test_iif_with_param_and_date(self):
        expr = "IIF(ORDER_DATE > TO_DATE($$CUTOFF, 'RRRR-MM-DD'), 'NEW', 'OLD')"
        result = translate_expression(expr)
        assert "CASE WHEN" in result
        assert "{{ var('cutoff') }}" in result
        assert "'YYYY-MM-DD'" in result

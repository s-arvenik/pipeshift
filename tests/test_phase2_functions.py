"""Tests for Phase 2: Tier 2 functions, lookup expressions, confidence scoring."""

from pipeshift.translator import Confidence, score_confidence, translate_expression


class TestTier2Functions:
    def test_cume_to_sum(self):
        result = translate_expression("CUME(AMOUNT)")
        assert result == "SUM(AMOUNT)"

    def test_movingavg_to_avg(self):
        result = translate_expression("MOVINGAVG(PRICE, 7)")
        assert result == "AVG(PRICE, 7)"

    def test_movingsum_to_sum(self):
        result = translate_expression("MOVINGSUM(QTY, 30)")
        assert result == "SUM(QTY, 30)"


class TestLookupExpression:
    def test_simple_lookup(self):
        result = translate_expression(":LKP.LKP_REGION(ZIP_CODE)")
        assert "ref('lkp_region')" in result
        assert "ZIP_CODE" in result
        assert "SELECT" in result

    def test_lookup_in_expression(self):
        result = translate_expression("IIF(ISNULL(:LKP.LKP_CUSTOMER(CUST_ID)), 'NEW', 'EXISTING')")
        assert "CASE WHEN" in result
        assert "ref('lkp_customer')" in result

    def test_lookup_preserves_surrounding(self):
        result = translate_expression("AMOUNT * :LKP.LKP_RATE(CURRENCY)")
        assert "AMOUNT *" in result
        assert "ref('lkp_rate')" in result


class TestConfidenceScoring:
    def test_simple_expression_is_high(self):
        assert score_confidence("CUSTOMER_ID") == Confidence.HIGH
        assert score_confidence("LTRIM(RTRIM(NAME))") == Confidence.HIGH
        assert score_confidence("IIF(X > 0, 'YES', 'NO')") == Confidence.HIGH

    def test_iif_nested_3_is_high(self):
        expr = "IIF(A, IIF(B, IIF(C, 1, 2), 3), 4)"
        assert score_confidence(expr) == Confidence.HIGH

    def test_iif_nested_4_is_medium(self):
        expr = "IIF(A, IIF(B, IIF(C, IIF(D, 1, 2), 3), 4), 5)"
        assert score_confidence(expr) == Confidence.MEDIUM

    def test_lookup_is_medium(self):
        assert score_confidence(":LKP.MY_LOOKUP(KEY)") == Confidence.MEDIUM

    def test_movingavg_is_medium(self):
        assert score_confidence("MOVINGAVG(PRICE, 7)") == Confidence.MEDIUM

    def test_cume_is_medium(self):
        assert score_confidence("CUME(AMOUNT)") == Confidence.MEDIUM

    def test_error_is_low(self):
        assert score_confidence("ERROR('Invalid record')") == Confidence.LOW

    def test_abort_is_low(self):
        assert score_confidence("ABORT('Fatal error')") == Confidence.LOW

    def test_setvariable_is_low(self):
        assert score_confidence("SETVARIABLE($$COUNT, $$COUNT + 1)") == Confidence.LOW

    def test_empty_is_high(self):
        assert score_confidence("") == Confidence.HIGH
        assert score_confidence("   ") == Confidence.HIGH

"""Complexity scoring for Informatica mappings."""

from typing import Dict

from pipeshift.ir.schema import Complexity, Mapping, TransformType

# Weights per transform type (higher = more complex to migrate)
_TRANSFORM_WEIGHTS: Dict[TransformType, float] = {
    TransformType.SOURCE_QUALIFIER: 0.0,
    TransformType.TARGET: 0.0,
    TransformType.EXPRESSION: 1.0,
    TransformType.FILTER: 0.5,
    TransformType.SORTER: 0.2,
    TransformType.LOOKUP: 1.5,
    TransformType.JOINER: 2.0,
    TransformType.AGGREGATOR: 2.0,
    TransformType.ROUTER: 2.5,
    TransformType.UNION: 1.5,
    TransformType.RANK: 2.0,
    TransformType.NORMALIZER: 3.0,
    TransformType.SEQUENCE_GENERATOR: 1.0,
    TransformType.SCD: 4.0,
    TransformType.UPDATE_STRATEGY: 2.0,
    TransformType.STORED_PROCEDURE: 5.0,
    TransformType.JAVA: 6.0,
    TransformType.CUSTOM: 5.0,
}

# Thresholds for complexity classification
_SIMPLE_MAX = 4.0
_MEDIUM_MAX = 10.0
_COMPLEX_MAX = 20.0
# Above COMPLEX_MAX = MANUAL


def score_mapping(mapping: Mapping) -> float:
    """Compute a complexity score for a mapping.

    Score is based on:
    - Number and type of transforms (weighted)
    - Number of expressions (nested logic adds complexity)
    - Number of lookup/join sources
    """
    score = 0.0

    for t in mapping.transforms:
        # Base weight for transform type
        score += _TRANSFORM_WEIGHTS.get(t.type, 3.0)

        # Expression complexity: count non-trivial expressions
        for expr in t.expressions:
            # Nested IIF/DECODE adds complexity
            nesting = expr.expression.upper().count("IIF") + expr.expression.upper().count("DECODE")
            score += 0.3 * max(0, nesting - 1)  # first level is free

    return round(score, 1)


def classify_mapping(mapping: Mapping) -> Complexity:
    """Classify a mapping's complexity tier."""
    score = score_mapping(mapping)
    if score <= _SIMPLE_MAX:
        return Complexity.SIMPLE
    elif score <= _MEDIUM_MAX:
        return Complexity.MEDIUM
    elif score <= _COMPLEX_MAX:
        return Complexity.COMPLEX
    else:
        return Complexity.MANUAL


def score_repository(mappings: list) -> Dict[str, Dict]:
    """Score all mappings and return summary statistics."""
    results = {}
    counts = {c: 0 for c in Complexity}

    for m in mappings:
        score = score_mapping(m)
        complexity = classify_mapping(m)
        counts[complexity] += 1
        results[m.name] = {
            "score": score,
            "complexity": complexity.value,
            "transforms": len(m.transforms),
        }

    return {
        "mappings": results,
        "summary": {c.value: count for c, count in counts.items()},
        "total": len(mappings),
    }

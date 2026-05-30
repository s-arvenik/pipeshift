# Contributing Guide

How to extend PipeShift with new transforms, expression functions, and source formats.

## Setup

```bash
cd pipeshift
pip install -e ".[dev]"   # or: pip3 install pydantic lxml pytest
python -m pytest tests/   # verify everything passes
```

## Adding a New Expression Function

**Location**: `src/pipeshift/translator/__init__.py`

### If it's a simple rename (same arg structure):

Add to `_SIMPLE_RULES` list:
```python
(re.compile(r'\bINFA_FUNC\s*\(', re.IGNORECASE), 'SQL_FUNC('),
```

### If it needs argument restructuring:

Write a dedicated `_translate_<func>` function using `_split_function_args`:
```python
def _translate_myfunc(expr: str) -> str:
    pattern = re.compile(r'\bMYFUNC\s*\(', re.IGNORECASE)
    match = pattern.search(expr)
    if not match:
        return expr
    args = _split_function_args(expr, match.end() - 1)
    if args is None:
        return expr
    close_pos = _find_closing_paren(expr, match.end() - 1)
    # ... build replacement ...
    result = expr[:match.start()] + replacement + expr[close_pos + 1:]
    return _translate_myfunc(result)  # recurse for multiple occurrences
```

Then call it from `_translate_functions()`.

### Rules:
- Combined patterns (e.g., LTRIM(RTRIM(x))→TRIM(x)) go BEFORE individual patterns
- Always add a test in `tests/test_translator.py`
- Use `_split_function_args` for anything with nested parens — never `[^()]+?` regex for args

### Test template:
```python
def test_my_new_function(self):
    result = translate_expression("INFA_FUNC(X, Y)")
    assert result == "SQL_FUNC(X, Y)"
```

---

## Adding a New Transform Type

### 1. Ensure the parser recognizes it

Check `_TRANSFORM_TYPE_MAP` in `parser/informatica_xml.py`. If the Informatica type string isn't there, add it:
```python
"My Transform": TransformType.MY_TRANSFORM,
```

And add the enum value in `ir/schema.py`:
```python
class TransformType(str, Enum):
    MY_TRANSFORM = "my_transform"
```

### 2. Add generation logic

In `generator/__init__.py`:

1. Add detection in `_build_model_sql`:
```python
my_transforms = _find_all_transforms(mapping, TransformType.MY_TRANSFORM)
```

2. Pass to `_build_cte_model` and add a CTE generation block:
```python
if my_transforms:
    for t in my_transforms:
        cte_name = "my_cte"
        lines.append(f"{cte_name} AS (")
        lines.append(_build_my_transform_cte(t, prev_cte))
        lines.append("),")
        lines.append("")
        prev_cte = cte_name
```

3. Write the `_build_my_transform_cte` helper.

### 3. Add a sample XML and test

Create `tests/sample_exports/my_transform.xml` with a minimal mapping using the transform.
Write tests in `tests/test_my_transform.py` covering:
- Parser correctly identifies the transform type
- Parser extracts relevant properties/expressions
- Generator produces valid SQL with the expected pattern

---

## Adding a New Source Format (e.g., DataStage)

### 1. Create the parser

`src/pipeshift/parser/datastage_dsx.py`:
```python
def parse_file(path: Union[str, Path]) -> Repository:
    # Parse the .dsx file
    # Return a Repository object using the same IR schema
    ...
```

### 2. Wire into CLI

In `cli.py`, detect file format by extension or add a `--format` flag:
```python
if path.suffix == '.dsx':
    from pipeshift.parser.datastage_dsx import parse_file
```

### 3. No changes needed to translator or generator

They only consume the IR — source format is irrelevant to them.

---

## Testing Conventions

- **File naming**: `tests/test_<module>.py`
- **Class naming**: `TestParseX`, `TestTranslateX`, `TestGenerateX`
- **Sample data**: `tests/sample_exports/<descriptive_name>.xml`
- **Run**: `python -m pytest tests/ -v`
- **All tests must pass before any PR is merged**

## Code Style

- Python 3.9+ compatible (use `Optional[X]`, `List[X]`, `Dict[X, Y]` — not `X | None`, `list[x]`)
- No `from __future__ import annotations` (breaks Pydantic on 3.9)
- Max line length: 100 chars
- Imports: stdlib → third-party → local (enforced by ruff)

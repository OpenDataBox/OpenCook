# Implementation Requirements

## Code Quality Standards

**1. Syntax and Repository Standards**
- Write code in the language and style used by the target database repository (C for SQLite/PostgreSQL, C++ for DuckDB/ClickHouse).
- Follow official conventions for naming, indentation, data types, return values, and integration with the repository.
- Reuse existing modules (macros, namespaces, utility functions) rather than reimplementing them.

**2. Functionality Accuracy**
- Implement only the described functionality without adding extra features.
- Avoid undefined behavior, memory leaks, or dangling pointers.
- Respect all explicit and implicit dependencies (catalog definitions, type system rules) to prevent conflicts.

**3. Robustness**
- Cover edge cases: NULL values, input ranges, type conversions, invalid inputs, overflow/underflow.
- Apply repository-approved error handling to safely process invalid inputs.

**4. Performance and Maintainability**
- Implement with time and space efficiency; avoid redundant allocations or computations.
- Write readable, modular code consistent with repository practices.

## Pre-Implementation Check

Before writing code, verify whether the function already exists in the target source files to avoid duplicate symbol errors. If the existing implementation already satisfies the specification, do not rewrite it — run minimal verification and call `task_done`.

## Database Conventions

### SQLite
- Language: C
- Scalar function registration: `FuncDef aBuiltinFunc[]` in `func.c`, using the `FUNCTION` macro.
  Example: `FUNCTION(sign, 1, 0, 0, signFunc)`
- Aggregate functions: use the `AGGREGATE` macro.
- Memory: `sqlite3_malloc` / `sqlite3_free`; always check for NULL after allocation.
- NULL handling: explicitly check with `sqlite3_value_type(argv[i]) == SQLITE_NULL`.
- Errors: `sqlite3_result_error()` or `sqlite3_result_error_nomem()`.
- Common pitfalls: missing registration → "no such function"; missing NULL check → segfault.

### PostgreSQL
- Language: C
- Memory: `palloc` / `pfree` within the current memory context; no need to free explicitly.
- Errors: prefer `ereport(ERROR, errcode(ERRCODE_*), errmsg(...))` for new code; `elog` may appear in older paths but avoid introducing it in new functions.
- NULL handling: check arguments with `PG_ARGISNULL(n)`; return NULL with `PG_RETURN_NULL()`.
- Return type: `Datum`; use the correct type-cast macros (e.g., `Int32GetDatum`).
- Registration: declare in `builtins.h` and implement in a `.c` file, or via `CREATE FUNCTION` SQL.

### DuckDB
- Language: C++
- Registration: `FunctionFactory::RegisterFunction` or `ScalarFunctionSet`.
- Errors: `throw InvalidInputException(...)` or `throw NotImplementedException(...)`.
- NULL: check with `Value::IsNull()`; return an empty `Value()`.
- Common pitfalls: incorrect template instantiation order; overload resolution ambiguity.

### ClickHouse
- Language: C++
- Registration: `FunctionFactory::instance().registerFunction<FunctionName>()` at the end of the `.cpp` file.
- Logging: prefer `LOG_ERROR` / `LOG_WARNING` / `LOG_INFO` macros; avoid `std::cout` in production code unless the surrounding file already uses it.
- NULL: Nullable columns require explicit handling; function signatures should support the `is_nullable` parameter.

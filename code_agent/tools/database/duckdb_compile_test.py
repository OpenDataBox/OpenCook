# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

import os
import re
import json
import time
import shutil
import subprocess
from datetime import datetime
from typing import Dict, Tuple, Optional, List

# ========================= 常量 =========================

RESULTS_ROOT = "results/duckdb"             # 结果根目录
TEST_TIMEOUT = 600                           # 单次运行默认超时（秒）
CATCH_ALL_TIMEOUT = TEST_TIMEOUT * 5         # 全量测试适当延长
MAX_LOG_CHARS = 20000                        # 写 JSON时 stdout/stderr 裁剪长度，避免爆体积

# 运行全部“函数相关组”的清单
BUILTIN_GROUPS = [
    "array", "autocomplete", "blob", "date", "enum", "generic",
    "interval", "list", "nested", "numeric", "operator", "string",
    "table", "time", "timestamp", "timetz", "uuid"
]

# 单函数/函数并集 的映射文件（与 ClickHouse 相同格式：type=function, keyword, testcases.test_files）
# FUNCTION_MAPPING_JSON = "/home/shuhang/database_test/function_test_统计信息/duckdb_output/duckDB_functionsV5_with_testcase.json"
FUNCTION_MAPPING_JSON = "/data/wei/program/DBCooker/data/benchmark/duckdb/duckdb_functions_with_testcase_code_understand.json"

# ========================= 工具函数 =========================
# 放到工具函数区
def _to_text(x) -> str:
    """统一把 stdout/stderr 规范化为 str"""
    if x is None:
        return ""
    if isinstance(x, bytes):
        try:
            return x.decode("utf-8", errors="ignore")
        except Exception:
            return x.decode(errors="ignore")
    return str(x)


def _jobs():
    return str(os.cpu_count() or 32)

def _abs(*parts) -> str:
    return os.path.abspath(os.path.join(*parts))

def _ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)

def _now_tag() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def _clip(s: str) -> str:
    if s is None:
        return ""
    if len(s) > MAX_LOG_CHARS:
        head = s[: MAX_LOG_CHARS // 2]
        tail = s[-MAX_LOG_CHARS // 2 :]
        return head + "\n... [TRUNCATED] ...\n" + tail
    return s

# 替换原来的 _run 实现
def _run(cmd: List[str], cwd: Optional[str] = None, timeout: Optional[int] = None) -> Tuple[bool, Dict]:
    """
    以列表形式执行命令，捕获 stdout/stderr；不抛异常。
    确保 stdout/stderr 始终为 str，避免后续正则在 bytes 上报错。
    """
    try:
        p = subprocess.run(
            cmd, cwd=cwd, timeout=timeout, check=False,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        ok = (p.returncode == 0)
        return ok, {
            "returncode": p.returncode,
            "stdout": _to_text(p.stdout),
            "stderr": _to_text(p.stderr),
            "cmd": " ".join(cmd),
        }
    except subprocess.TimeoutExpired as e:
        return False, {
            "returncode": -1,
            "stdout": _to_text(getattr(e, "stdout", "")),
            "stderr": _to_text(f"Timeout: {e}"),
            "cmd": " ".join(cmd)
        }
    except Exception as e:
        return False, {
            "returncode": -2,
            "stdout": "",
            "stderr": _to_text(f"Exception: {e}"),
            "cmd": " ".join(cmd)
        }


def _paths(duckdb_root: str) -> Dict[str, str]:
    root = _abs(duckdb_root)
    return {
        "root": root,
        "build_dir": _abs(root, "build", "release"),
        "unittest_bin": _abs(root, "build", "release", "test", "unittest"),
    }

# ========================= 编译：增量 / 从头 =========================

def compile_incremental(duckdb_root: str, timeout=None) -> Tuple[bool, str]:
    """
    增量编译（在 duckdb 根目录直接 make release）
    """
    ok, info = _run(["make", "release", f"-j{_jobs()}"],
                    cwd=_abs(duckdb_root), timeout=timeout)
    if not ok:
        return False, f"[MAKE RELEASE ERROR]\n{info.get('stderr','')}"
    return True, "OK"

def compile_clean(duckdb_root: str) -> Tuple[bool, str]:
    """
    从头编译：
      rm -rf build
      make release
    """
    top_build = _abs(duckdb_root, "build")
    if os.path.exists(top_build):
        shutil.rmtree(top_build, ignore_errors=True)

    ok, info = _run(["make", "release", f"-j{_jobs()}"], cwd=_abs(duckdb_root))
    if not ok:
        return False, f"[MAKE RELEASE ERROR]\n{info.get('stderr','')}"
    return True, "OK"

# ========================= 结果写出 =========================

def write_duckdb_results_json(
    method_id: str,
    tag: str,
    stdout_text: str,
    stderr_text: str,
    failed_tests: List[str],
    extra_summary: Optional[Dict] = None,
    results: Optional[List[Dict]] = None,
    groups: Optional[List[Dict]] = None,
) -> str:
    """
    将“测试文件 + 终端输出内容”写入 JSON。
    路径：results/duckdb/{method_id}/{tag}_{method_id}_{timestamp}.json
    """
    out_dir = _abs(RESULTS_ROOT, method_id)
    _ensure_dir(out_dir)
    ts = _now_tag()
    out_json = _abs(out_dir, f"{tag}_{method_id}_{ts}.json")
    payload = {
        "method_id": method_id,
        "tag": tag,                      # full / [group] / single / function:NAME / union:functions / builtin-groups
        "failed_tests": sorted(set(failed_tests or [])),
        "summary": extra_summary or {},
        "stdout": _clip(stdout_text or ""),
        "stderr": _clip(stderr_text or ""),
        "generated_at": ts,
    }
    if results is not None:
        # 针对逐文件运行的结果，附上每个文件的通过情况与输出（截断）
        payload["results"] = [
            {
                **{k: v for k, v in item.items() if k not in ("stdout", "stderr")},
                "stdout": _clip(item.get("stdout", "") or ""),
                "stderr": _clip(item.get("stderr", "") or ""),
            }
            for item in results
        ]
    if groups is not None:
        # 针对按 group 依次运行，记录每个 group 的摘要与失败列表（输出截断）
        payload["groups"] = [
            {
                "group": g.get("group"),
                "ok": g.get("ok"),
                "summary": g.get("summary", {}),
                "failed_tests": sorted(set(g.get("failed_tests", []) or [])),
                "stdout": _clip(g.get("stdout", "") or ""),
                "stderr": _clip(g.get("stderr", "") or ""),
            }
            for g in groups
        ]
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return out_json

# ========================= DuckDB 测试运行（基础） =========================

_FAILED_LINE_RE = re.compile(r"^(test/.+?\.test):\d+:\s+FAILED:", re.MULTILINE)
_ENUM_FILE_RE   = re.compile(r"^\s*\d+\.\s+(test/.+?\.test)\s*$", re.MULTILINE)
_SUMMARY_RE     = re.compile(
    r"test cases:\s*(\d+)\s*\|\s*(\d+)\s*passed\s*\|\s*(\d+)\s*failed\s*\|\s*(\d+)\s*skipped",
    re.IGNORECASE
)

def _parse_failed_tests_from_stdout(stdout_text: str) -> List[str]:
    """
    从 DuckDB unittest 的 stdout 中解析失败的 .test 文件路径：
      - 主要匹配 'xxx.test:<line>: FAILED:' 行
      - 若未匹配到但 summary 显示失败>0，退而取枚举行作为参考
    """
    stdout_text = stdout_text or ""
    failed = set(m.group(1).strip() for m in _FAILED_LINE_RE.finditer(stdout_text))
    if failed:
        return sorted(failed)
    # 兜底
    enum_paths = [m.group(1).strip() for m in _ENUM_FILE_RE.finditer(stdout_text)]
    return sorted(set(enum_paths))

def _parse_summary(stdout_text: str) -> Dict:
    m = _SUMMARY_RE.search(stdout_text or "")
    if not m:
        return {}
    total, passed, failed, skipped = map(int, m.groups())
    return {"total": total, "passed": passed, "failed": failed, "skipped": skipped}

def run_full_tests(duckdb_root: str, timeout: Optional[int] = None) -> Tuple[bool, Dict]:
    """
    运行全量测试：./build/release/test/unittest
    """
    p = _paths(duckdb_root)
    bin_path = p["unittest_bin"]
    if not os.path.exists(bin_path):
        return False, {"error": f"unittest 不存在: {bin_path}"}
    start = time.time()
    ok, info = _run([bin_path], cwd=os.path.dirname(bin_path), timeout=timeout or CATCH_ALL_TIMEOUT)
    info["duration"] = time.time() - start
    info["failed_tests"] = _parse_failed_tests_from_stdout(info.get("stdout", ""))
    info["summary"] = _parse_summary(info.get("stdout", ""))
    return ok, info

def run_group_tests(group_name: str, duckdb_root: str, timeout: Optional[int] = None) -> Tuple[bool, Dict]:
    """
    运行组别测试：./build/release/test/unittest "[group_name]"
    示例：run_group_tests("projection", ...)
    """
    p = _paths(duckdb_root)
    bin_path = p["unittest_bin"]
    if not os.path.exists(bin_path):
        return False, {"error": f"unittest 不存在: {bin_path}"}
    start = time.time()
    ok, info = _run([bin_path, f"[{group_name}]"], cwd=os.path.dirname(bin_path), timeout=timeout or TEST_TIMEOUT)
    info["duration"] = time.time() - start
    info["failed_tests"] = _parse_failed_tests_from_stdout(info.get("stdout", ""))
    info["summary"] = _parse_summary(info.get("stdout", ""))
    return ok, info

def run_single_test_file(test_file_path: str, duckdb_root: str, timeout: Optional[int] = None) -> Tuple[bool, Dict]:
    """
    运行单个测试文件：./build/release/test/unittest <path-to-test-file>
    例：test/sql/projection/test_simple_projection.test
    """
    p = _paths(duckdb_root)
    bin_path = p["unittest_bin"]
    if not os.path.exists(bin_path):
        return False, {"error": f"unittest 不存在: {bin_path}"}
    start = time.time()
    ok, info = _run([bin_path, test_file_path], cwd=os.path.dirname(bin_path), timeout=timeout or TEST_TIMEOUT)
    info["duration"] = time.time() - start
    info["failed_tests"] = [] if ok else [test_file_path]
    info["summary"] = _parse_summary(info.get("stdout", ""))
    return ok, info

# ========================= DuckDB 测试运行（函数相关） =========================

def _load_function_mapping() -> Tuple[bool, List[dict], str]:
    if not os.path.exists(FUNCTION_MAPPING_JSON):
        return False, [], f"找不到映射 JSON: {FUNCTION_MAPPING_JSON}"
    try:
        with open(FUNCTION_MAPPING_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
        return True, data, "OK"
    except Exception as e:
        return False, [], f"解析映射 JSON 失败: {e}"

def _extract_tests_for_keyword(mapping: List[dict], keyword: str) -> List[str]:
    """
    从新版 JSON 中找到 keyword 对应的 testcases.test_files（保持原始相对路径）
    格式与 ClickHouse 一致：type=function, keyword, testcases:{test_files:[...]}
    """
    for item in mapping:
        if item.get("type") == "function" and str(item.get("keyword", "")).lower() == keyword.lower():
            tcs = item.get("testcases", {})
            files = tcs.get("test_files", []) if isinstance(tcs, dict) else []
            return list(files)
    return []

def _union_all_function_tests(mapping: List[dict]) -> List[str]:
    s = set()
    for item in mapping:
        if item.get("type") == "function":
            tcs = item.get("testcases", {})
            files = tcs.get("test_files", []) if isinstance(tcs, dict) else []
            for f in files:
                s.add(f)
    return sorted(s)

def run_function_tests(func_name: str, duckdb_root: str, timeout: Optional[int] = None) -> Tuple[bool, Dict]:
    """
    运行“单个函数”对应的所有测试（从映射 JSON 读取 test_files，一条一条跑）
    """
    ok_m, mapping, msg = _load_function_mapping()
    if not ok_m:
        return False, {"error": msg}

    test_files = _extract_tests_for_keyword(mapping, func_name)
    if not test_files:
        return False, {"error": f"未找到函数 '{func_name}' 的测试用例"}

    p = _paths(duckdb_root)
    bin_path = p["unittest_bin"]
    if not os.path.exists(bin_path):
        return False, {"error": f"unittest 不存在: {bin_path}"}

    results = []
    failed_tests = []
    start_all = time.time()

    for idx, test_path in enumerate(test_files, 1):
        test_path = test_path.replace("duckdb/", "")
        ok, info = _run([bin_path, test_path], cwd=os.path.dirname(bin_path), timeout=timeout or TEST_TIMEOUT)
        if not ok:
            failed_tests.append(test_path)
        results.append({
            "index": idx,
            "test_file": test_path,
            "passed": ok,
            "returncode": info.get("returncode"),
            "stdout": info.get("stdout", ""),
            "stderr": info.get("stderr", ""),
        })

    duration = time.time() - start_all
    summary = {
        "total": len(results),
        "passed": sum(1 for r in results if r["passed"]),
        "failed": sum(1 for r in results if not r["passed"]),
        "duration_seconds": duration,
    }
    return (summary["failed"] == 0), {
        "summary": summary,
        "failed_tests": failed_tests,
        "results": results,
        "tag": f"function:{func_name}",
    }

def run_functions_union_tests(duckdb_root: str, timeout: Optional[int] = None) -> Tuple[bool, Dict]:
    """
    运行“所有函数的测试并集”（从映射 JSON 取所有 test_files 并集，一条一条跑）
    """
    ok_m, mapping, msg = _load_function_mapping()
    if not ok_m:
        return False, {"error": msg}

    test_files = _union_all_function_tests(mapping)
    if not test_files:
        return True, {"summary": {"total": 0, "passed": 0, "failed": 0, "duration_seconds": 0}, "failed_tests": [], "results": [], "tag": "union:functions"}

    p = _paths(duckdb_root)
    bin_path = p["unittest_bin"]
    if not os.path.exists(bin_path):
        return False, {"error": f"unittest 不存在: {bin_path}"}

    results = []
    failed_tests = []
    start_all = time.time()

    for idx, test_path in enumerate(test_files, 1):
        ok, info = _run([bin_path, test_path], cwd=os.path.dirname(bin_path), timeout=timeout or TEST_TIMEOUT)
        if not ok:
            failed_tests.append(test_path)
        results.append({
            "index": idx,
            "test_file": test_path,
            "passed": ok,
            "returncode": info.get("returncode"),
            "stdout": info.get("stdout", ""),
            "stderr": info.get("stderr", ""),
        })

    duration = time.time() - start_all
    summary = {
        "total": len(results),
        "passed": sum(1 for r in results if r["passed"]),
        "failed": sum(1 for r in results if not r["passed"]),
        "duration_seconds": duration,
    }
    return (summary["failed"] == 0), {
        "summary": summary,
        "failed_tests": failed_tests,
        "results": results,
        "tag": "union:functions",
    }

def run_all_builtin_groups(duckdb_root: str, timeout_per_group: Optional[int] = None) -> Tuple[bool, Dict]:
    """
    依次运行 BUILTIN_GROUPS 列表中的所有组。
    """
    groups_out = []
    any_failed = False
    total = passed = failed = skipped = 0

    for group in BUILTIN_GROUPS:
        ok, info = run_group_tests(group, duckdb_root, timeout=timeout_per_group or TEST_TIMEOUT)
        groups_out.append({
            "group": group,
            "ok": ok,
            "summary": info.get("summary", {}),
            "failed_tests": info.get("failed_tests", []),
            "stdout": info.get("stdout", ""),
            "stderr": info.get("stderr", ""),
        })
        if not ok:
            any_failed = True
        s = info.get("summary", {})
        total += int(s.get("total", 0))
        passed += int(s.get("passed", 0))
        failed += int(s.get("failed", 0))
        skipped += int(s.get("skipped", 0))

    summary = {"total": total, "passed": passed, "failed": failed, "skipped": skipped}
    return (not any_failed), {"summary": summary, "groups": groups_out, "tag": "builtin-groups"}

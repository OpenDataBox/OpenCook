# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

import argparse
import os
import subprocess
import sys
import json

from code_agent.utils.config import get_database_config

cpu_num: int = get_database_config("sqlite").cpu_num or 8

# ================ 配置路径 ================
BUILD_DIR = "build"
SQLITE_DIR = "."  # 当前目录就是sqlite目录
SQLITE3_BIN = os.path.join(BUILD_DIR, "sqlite3")
TESTFIXTURE_BIN = os.path.join(BUILD_DIR, "testfixture")
TCL_LIBRARY_PATH = "/home/zhouwei/anaconda3/lib/"  # 根据实际TCL安装路径修改
# 测试配置
TEST_TXT_PATH = "sqlite_tests.txt"  # 要运行哪些测试的txt文件，每一行一个测试名
TEST_RESULTS_JSON = "sqlite_test_results.json"  # 测试结果保存路径
TEST_TIMEOUT = 300  # 5分钟

# 批量测试类型
BATCH_TEST_TYPES = {
    "devtest": "开发测试（srctree-check+源码检查）",
    "releasetest": "发行测试（不包含srctree-check和源码检查）",
    "quicktest": "快速测试（包含部分tcl测试，不包含异常、模糊和侵泡测试）",
    "tcltest": "tcl测试"
}


# ================ 功能函数 ================

def run_cmd(cmd, cwd=None, shell=True, env=None, timeout=600):
    print(f"[执行] {cmd}")
    proc = subprocess.run(cmd, cwd=cwd, shell=shell, env=env,
                          capture_output=True,  # 捕获 stdout 和 stderr
                          text=True,  # 以字符串形式返回（而不是字节）
                          timeout=timeout,  # 设置超时时间
                          )
    if proc.returncode != 0:
        print(f"[失败] {cmd}\n{proc.stderr}")
        raise Exception(proc.stderr)
        # sys.exit(proc.returncode)

    return proc.stdout


def compile_sqlite(compile_folder, timeout=600):
    """完整编译SQLite"""
    try:
        out = str()

        print("[2/6] 创建build文件夹...")
        os.makedirs(f"{compile_folder}/{BUILD_DIR}", exist_ok=True)

        print("[3/6] 配置SQLite...")
        # configure_path = [
        #     # f"../configure --prefix={install_folder} --with-tcl={install_folder}/lib",
        #     f"../configure --with-tcl={TCL_LIBRARY_PATH}",
        #     f"make sqlite3 -j{cpu_num}",
        #     f"make tclextension-install -j{cpu_num}"
        # ]
        configure_path = [
            f"../configure",
            f"make sqlite3 -j{cpu_num}",
            f"make tclextension -j{cpu_num}"
        ]
        out += run_cmd(" && ".join(configure_path), cwd=f"{compile_folder}/{BUILD_DIR}", timeout=timeout)

        # print("[4/6] 编译sqlite3...")
        # out += run_cmd(, cwd=f"{compile_folder}/{BUILD_DIR}")
        #
        # print("[5/6] 安装TCL extension...")
        # out += run_cmd(, cwd=f"{compile_folder}/{BUILD_DIR}")

        print("[6/6] 编译完成!")
        return True, out
    except Exception as e:
        print(f"Compile error occurs {e}")
        if "timed out" in str(e).lower():
            return True, ""
        return False, str(e)


def incremental_build():
    """增量编译"""
    print("[1/1] 增量编译sqlite3...")
    run_cmd("make sqlite3", cwd=BUILD_DIR)

    print("[1/1] 增量编译完成!")


def run_batch_test(compile_folder, test_type="quicktest"):
    """运行批量测试"""
    print(f"[批量测试] 运行 {test_type}")
    if not os.path.exists(f"{compile_folder}/{BUILD_DIR}"):
        print(f"错误：找不到构建目录 {compile_folder}/{BUILD_DIR}")
        # sys.exit(1)
        return False, f"错误：找不到构建目录 {compile_folder}/{BUILD_DIR}"

    # 运行指定的批量测试
    try:
        out = run_cmd(f"make {test_type} -j{cpu_num}", cwd=f"{compile_folder}/{BUILD_DIR}")
        print(f"[批量测试] {test_type} 完成")
        return True, out
    except Exception as e:
        print(f"Test error occurs {e}")
        return False, str(e)


def build_testfixture(compile_folder):
    """构建testfixture（TCL解释器）"""
    print("[构建] testfixture...")

    out = run_cmd(f"make testfixture -j{cpu_num}", cwd=f"{compile_folder}/{BUILD_DIR}")

    # if not os.path.exists(TESTFIXTURE_BIN):
    #     print(f"错误：testfixture构建失败 {TESTFIXTURE_BIN}")
    #     sys.exit(1)

    print("[构建] testfixture构建成功")
    return out


def run_single_tests(compile_folder, txt_path=None):
    """运行单条TCL测试"""
    if txt_path is None:
        txt_path = TEST_TXT_PATH

    if not os.path.isfile(txt_path):
        print(f"未找到文件: {txt_path}")
        return

    # 确保testfixture已构建
    testfixture_bin = f"{compile_folder}/{BUILD_DIR}/testfixture"
    if not os.path.exists(testfixture_bin):
        print("[准备] 构建testfixture...")
        build_testfixture(compile_folder)

    with open(txt_path, 'r', encoding='utf-8') as f:
        test_files = [line.strip() for line in f if line.strip() and not line.startswith('#')]

    results = []
    passed_count = 0
    failed_count = 0

    print(f"开始运行 {len(test_files)} 个单条测试...")
    for i, test_file in enumerate(test_files, 1):
        print(f"[{i}/{len(test_files)}] {test_file} ...", end=" ")

        # 检查测试文件是否存在
        if not os.path.exists(test_file):
            print("文件不存在")
            failed_count += 1
            results.append({
                "test_file": test_file,
                "passed": False,
                "reason": "测试文件不存在"
            })
            continue

        # 运行单条测试 - 使用相对于build目录的路径
        cmd = ["./testfixture", os.path.join("..", test_file)]
        try:
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    timeout=TEST_TIMEOUT, cwd=BUILD_DIR)
            passed = (result.returncode == 0)
            reason = "" if passed else result.stderr.decode(errors="ignore")
        except subprocess.TimeoutExpired:
            passed = False
            reason = f"超时（超过{TEST_TIMEOUT // 60}分钟）"
        except Exception as e:
            passed = False
            reason = str(e)

        if passed:
            print("通过")
            passed_count += 1
        else:
            print("未通过")
            failed_count += 1
            if reason:
                print(f"    原因: {reason[:100]}...")

        results.append({
            "test_file": test_file,
            "passed": passed,
            "reason": reason
        })

    # 保存测试结果
    with open(TEST_RESULTS_JSON, "w", encoding="utf-8") as jf:
        json.dump(results, jf, ensure_ascii=False, indent=2)

    print(f"测试完成：通过 {passed_count} 个，未通过 {failed_count} 个，总数 {len(test_files)}")
    print(f"详细结果已保存到 {TEST_RESULTS_JSON}")


def run_sqlite_function_tests(compile_folder, timeout=TEST_TIMEOUT):
    """运行函数相关的测试"""
    function_tests = [
        "changes2.test", "coalesce.test", "ctime.test", "dbdata.test", "e_totalchanges.test",
        "exprfault.test", "fts3rank.test", "func.test", "func2.test", "func3.test",
        "func4.test", "func5.test", "func6.test", "func7.test", "func8.test",
        "func9.test", "init.test", "main.test", "notnullfault.test", "percentile.test",
        "substr.test", "vtab1.test", "whereL.test", "window9.test", "windowD.test",
        "windowerr.test", "windowfault.test"
    ]

    # 确保testfixture已构建
    testfixture_bin = f"{compile_folder}/{BUILD_DIR}/testfixture"
    if not os.path.exists(testfixture_bin):
        print("[准备] 构建testfixture...")
        build_testfixture(compile_folder)

    # results = []
    # passed_count = 0
    # failed_count = 0

    out = str()
    print(f"开始运行 {len(function_tests)} 个函数测试...")
    for i, test_file in enumerate(function_tests):
        print(f"[{i}/{len(function_tests)}] {test_file} ...")

        # 构建完整的测试文件路径
        test_path = f"{compile_folder}/test/{test_file}"
        # 检查测试文件是否存在
        # if not os.path.exists(test_path):
        #     print("文件不存在")
        #     failed_count += 1
        #     results.append({
        #         "test_file": test_file,
        #         "passed": False,
        #         "reason": "测试文件不存在"
        #     })
        #     continue

        # 运行单条测试 - 使用相对于build目录的路径
        cmd = ["./testfixture", test_path]
        try:
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    timeout=timeout, cwd=f"{compile_folder}/{BUILD_DIR}")
            if result.returncode != 0:
                err = result.stderr.decode(errors="ignore")
                if len(err) == 0:
                    err = result.stdout.decode(errors="ignore")
                return False, err

            if "0 errors out of" not in result.stdout.decode(errors="ignore"):
                return False, result.stdout.decode(errors="ignore")

            out += result.stdout.decode(errors="ignore") + "\n"
        except subprocess.TimeoutExpired:
            # passed = False
            reason = f"超时（超过 {timeout // 60} 分钟）"
            return False, reason
        except Exception as e:
            # passed = False
            reason = str(e)
            return False, reason

        # if passed:
        #     print("通过")
        #     passed_count += 1
        # else:
        #     print("未通过")
        #     failed_count += 1
        #     if reason:
        #         print(f"    原因: {reason[:100]}...")

        # results.append({
        #     "test_file": test_file,
        #     "passed": passed,
        #     "reason": reason
        # })

    # 保存测试结果
    # function_results_json = "sqlite_function_test_results.json"
    # with open(function_results_json, "w", encoding="utf-8") as jf:
    #     json.dump(results, jf, ensure_ascii=False, indent=2)

    # print(f"函数测试完成：通过 {passed_count} 个，未通过 {failed_count} 个，总数 {len(function_tests)}")
    # print(f"详细结果已保存到 {function_results_json}")
    return True, out


def format_time(seconds):
    """格式化时间显示"""
    if seconds < 60:
        return f"{seconds:.2f}秒"
    elif seconds < 3600:
        minutes = int(seconds // 60)
        secs = seconds % 60
        return f"{minutes}分{secs:.2f}秒"
    else:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60
        return f"{hours}小时{minutes}分{secs:.2f}秒"


def load_function_tests_mapping(mapping_json):
    """加载函数到测试的映射，返回列表或抛出异常"""
    if not os.path.exists(mapping_json):
        raise FileNotFoundError(f"找不到映射文件 {mapping_json}，请先运行 sqlite_extract.py 生成映射文件")
    with open(mapping_json, 'r', encoding='utf-8') as f:
        return json.load(f)


def find_minimal_test_set(function_mappings):
    """用贪心算法找到覆盖所有函数的最小测试集合"""
    test_to_functions = {}
    all_functions = set()

    for item in function_mappings:
        function_name = item.get("functions", "")
        test_paths = item.get("tests_path", [])
        if function_name and test_paths:
            all_functions.add(function_name)
            for test_path in test_paths:
                if test_path not in test_to_functions:
                    test_to_functions[test_path] = set()
                test_to_functions[test_path].add(function_name)

    print(f"找到 {len(all_functions)} 个需要覆盖的函数")
    print(f"共有 {len(test_to_functions)} 个相关测试文件")

    uncovered_functions = all_functions.copy()
    selected_tests = []

    while uncovered_functions:
        best_test = None
        best_coverage = 0
        for test_path, covered_functions in test_to_functions.items():
            if test_path in selected_tests:
                continue
            new_coverage = len(covered_functions & uncovered_functions)
            if new_coverage > best_coverage:
                best_coverage = new_coverage
                best_test = test_path
        if best_test is None:
            break
        selected_tests.append(best_test)
        covered_by_best = test_to_functions[best_test]
        uncovered_functions -= covered_by_best
        print(f"选择测试: {best_test}，覆盖 {best_coverage} 个函数，剩余 {len(uncovered_functions)} 个")

    if uncovered_functions:
        print(f"警告：{len(uncovered_functions)} 个函数无法被任何测试覆盖: {sorted(uncovered_functions)}")

    if all_functions:
        coverage_pct = (len(all_functions) - len(uncovered_functions)) / len(all_functions) * 100
        print(f"最小测试集合: {len(selected_tests)} 个文件，覆盖率 {coverage_pct:.2f}%")

    return selected_tests


def run_test_files_with_results(compile_folder, test_files, test_type, timeout=TEST_TIMEOUT):
    """运行指定测试文件列表，返回 (success, output_str)"""
    if not test_files:
        return True, f"没有找到{test_type}相关的测试文件"

    testfixture_bin = f"{compile_folder}/{BUILD_DIR}/testfixture"
    if not os.path.exists(testfixture_bin):
        print("[准备] 构建testfixture...")
        build_testfixture(compile_folder)

    passed_count = 0
    out_lines = []

    print(f"开始运行 {len(test_files)} 个{test_type}...")
    for i, test_file in enumerate(test_files, 1):
        print(f"[{i}/{len(test_files)}] {test_file} ...", end=" ")
        test_path = os.path.join(compile_folder, test_file)
        cmd = ["./testfixture", test_path]
        try:
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    timeout=timeout, cwd=f"{compile_folder}/{BUILD_DIR}")
            stdout_str = result.stdout.decode(errors="ignore")
            stderr_str = result.stderr.decode(errors="ignore")
            if result.returncode != 0:
                passed = False
                reason = stderr_str or stdout_str
            elif "0 errors out of" not in stdout_str:
                passed = False
                reason = stdout_str
            else:
                passed = True
                reason = ""
        except subprocess.TimeoutExpired:
            passed = False
            stdout_str = ""
            reason = f"超时（超过 {timeout // 60} 分钟）"
        except Exception as e:
            passed = False
            stdout_str = ""
            reason = str(e)

        if passed:
            print("通过")
            passed_count += 1
            out_lines.append(stdout_str)
        else:
            print("未通过")
            return False, f"{test_type} 测试失败 [{test_file}]: {reason}"

    summary = f"{test_type}完成：通过 {passed_count} 个，未通过 0 个，总数 {len(test_files)}"
    print(summary)
    return True, "\n".join(out_lines) + "\n" + summary


def run_builtin_function_tests_from_mapping(compile_folder, mapping_json, timeout=TEST_TIMEOUT):
    """运行映射文件中所有内置函数相关的测试"""
    try:
        function_mappings = load_function_tests_mapping(mapping_json)
    except Exception as e:
        return False, str(e)

    all_test_files = set()
    for item in function_mappings:
        function_name = item.get("functions", "")
        test_paths = item.get("tests_path", [])
        if function_name and test_paths:
            for tp in test_paths:
                all_test_files.add(tp)

    test_files_list = sorted(all_test_files)
    print(f"找到 {len(test_files_list)} 个与内置函数相关的测试文件")
    return run_test_files_with_results(compile_folder, test_files_list, "内置函数相关测试", timeout)


def run_sub_builtin_function_tests(compile_folder, mapping_json, timeout=TEST_TIMEOUT):
    """运行覆盖所有内置函数的最小测试集合"""
    try:
        function_mappings = load_function_tests_mapping(mapping_json)
    except Exception as e:
        return False, str(e)

    minimal_test_files = find_minimal_test_set(function_mappings)
    if not minimal_test_files:
        return True, "没有找到合适的测试文件"

    print(f"将运行 {len(minimal_test_files)} 个最小覆盖测试")
    return run_test_files_with_results(compile_folder, minimal_test_files, "子内置函数相关测试", timeout)


def run_single_function_tests_by_name(func_name, compile_folder, mapping_json, timeout=TEST_TIMEOUT):
    """运行指定函数名的相关测试"""
    try:
        function_mappings = load_function_tests_mapping(mapping_json)
    except Exception as e:
        return False, str(e)

    test_files = []
    for item in function_mappings:
        if item.get("functions", "").lower() == func_name.lower():
            test_files.extend(item.get("tests_path", []) or [])
            break

    if not test_files:
        available = sorted(
            item.get("functions", "") for item in function_mappings
            if item.get("functions", "") and item.get("tests_path", [])
        )
        sample = available[:20]
        return False, f"未找到函数 '{func_name}' 的测试用例。可用函数示例: {sample}"

    print(f"找到 {len(test_files)} 个与函数 '{func_name}' 相关的测试文件")
    return run_test_files_with_results(compile_folder, test_files, f"函数'{func_name}'相关测试", timeout)


def main():
    parser = argparse.ArgumentParser(description='SQLite 编译和测试脚本')
    parser.add_argument('--mode', choices=['batch', 'builtin', 'sub-builtin', 'function', 'subtest'],
                        default='batch', help='测试模式')
    parser.add_argument('--batch-type', choices=list(BATCH_TEST_TYPES.keys()),
                        default='quicktest', help='批量测试类型（仅 --mode batch）')
    parser.add_argument('--function', type=str,
                        help='指定函数名（仅 --mode function）')
    parser.add_argument('--mapping', type=str, default="辅助脚本/sqlite_output/function_to_tests.json",
                        help='函数-测试映射 JSON 路径')
    parser.add_argument('--compile-folder', type=str, default="compile_folder",
                        help='编译目录（包含 build/ 子目录）')
    parser.add_argument('--action', choices=['both', 'build', 'test'],
                        default='both', help='执行模式')
    args = parser.parse_args()

    cf = args.compile_folder

    if args.action in ('both', 'build'):
        ok, msg = compile_sqlite(compile_folder=cf)
        if not ok:
            print(f"编译失败: {msg}")
            sys.exit(1)

    if args.action in ('both', 'test'):
        try:
            if args.mode == 'batch':
                ok, msg = run_batch_test(cf, args.batch_type)
            elif args.mode == 'builtin':
                ok, msg = run_builtin_function_tests_from_mapping(cf, args.mapping)
            elif args.mode == 'sub-builtin':
                ok, msg = run_sub_builtin_function_tests(cf, args.mapping)
            elif args.mode == 'function':
                if not args.function:
                    print("错误：--mode function 时必须指定 --function")
                    sys.exit(1)
                ok, msg = run_single_function_tests_by_name(args.function, cf, args.mapping)
            elif args.mode == 'subtest':
                ok, msg = run_sqlite_function_tests(cf)
            else:
                ok, msg = False, f"未知模式 {args.mode}"
        except Exception as e:
            print(f"测试过程异常: {e}")
            sys.exit(1)

        if not ok:
            print(f"测试失败: {msg}")
            sys.exit(1)
        print(msg)

    print("\n全部流程完成！")


if __name__ == "__main__":
    main()

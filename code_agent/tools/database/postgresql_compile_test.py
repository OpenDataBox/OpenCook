# Copyright (c) 2025-2026 weAIDB
# OpenCook: Start with a generic project. End with a perfectly tailored solution.
# SPDX-License-Identifier: MIT

import os
import shutil
import subprocess

from code_agent.utils.config import get_database_config

_db_cfg = get_database_config("postgresql")
port: int = _db_cfg.port or 5432
user: str = _db_cfg.user or "postgres"
cpu_num: int = _db_cfg.cpu_num or os.cpu_count() or 8
bash_path: str = _db_cfg.bash_path or "/bin/bash"

# https://github.com/postgres/postgres/tree/REL9_5_STABLE

TIMEOUT = 600


def compile_postgresql(compile_folder, install_folder, timeout=600):
    # 删除目标路径原先存在的文件夹
    if os.path.exists(install_folder):
        shutil.rmtree(install_folder)

    # # 复制文件夹
    # source_folder = r'D:\JXMYJ\PostgreSQL\dist\REL9_5_0'
    # shutil.copytree(source_folder, target_folder)

    # 构造 bash 要执行的命令, /d/JXMYJ/PostgreSQL/build/REL9_5_0
    commands = r"""
    cd {compile_folder}
    ./configure --prefix={install_folder}
    make clean
    make uninstall
    make -j{cpu_num} -s
    make install
    exit
    """.format(compile_folder=compile_folder, cpu_num=cpu_num, install_folder=install_folder)

    # 调用 bash 并运行命令，明确设置 encoding 为 'utf-8'
    proc = subprocess.Popen(
        [bash_path, '-l'],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,  # 或 universal_newlines=True
        encoding='utf-8'  # 明确设置 UTF-8 编码
    )

    # 发送命令并获取输出（out是标准输出，err是错误输出）
    try:
        out, warn_err = proc.communicate(commands, timeout=timeout)
        # print("-----------------------------")
        # print("out:", out)
        # print("-----------------------------")
        # print("warn_err:", warn_err)
        # print("-----------------------------")

        # if "错误" in warn_err:
        if "error" in warn_err.lower():
            return False, warn_err

        return True, out
    except subprocess.TimeoutExpired as e:
        partial_out = e.stdout  # 已输出的部分
        proc.kill()  # 别忘了杀掉子进程
        return True, partial_out


def init_postgresql(install_folder, data_folder):
    try:
        # 在 D:\JXMYJ\REL9_5_0\bin 目录下执行 initdb 命令初始化数据库
        # initdb_cmd = r'D:\JXMYJ\REL9_5_0\bin\initdb --pgdata=D:\pgsql\pgdata95 --username=JXMYJ --auth=trust'
        initdb_cmd = f"{install_folder}/bin/initdb --pgdata={data_folder} --username={user} --auth=trust"
        res = subprocess.run(initdb_cmd, shell=True, check=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=TIMEOUT)
        # print("init_postgresql:", res)
        return True, str(res)
    except Exception as e:
        print(f"初始化 PostgreSQL 失败: {e}")
        return False, str(e)


def start_postgresql(install_folder, data_folder):
    # 启动 PostgreSQL
    try:
        cmd_stop = f'{install_folder}/bin/pg_ctl start -D {data_folder} -o "-p {port}"'
        res = subprocess.run(cmd_stop, shell=True, check=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=TIMEOUT)
        # print("start_postgresql:", res)
        return True, str(res)
    except Exception as e:
        print(f"启动 PostgreSQL 失败: {e}")
        return False, str(e)


def stop_postgresql(install_folder, data_folder):
    # 停止 PostgreSQL
    try:
        cmd_stop = f'{install_folder}/bin/pg_ctl stop -D {data_folder} -o "-p {port}"'
        res = subprocess.run(cmd_stop, shell=True, check=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=TIMEOUT)
        # print("stop_postgresql:", type(res), res)
        return True, str(res)
    except Exception as e:
        print(f"停止 PostgreSQL 失败: {e}")
        return False, str(e)


def status_postgresql(install_folder, data_folder):
    # 探测 PostgreSQL
    try:
        cmd_stop = f'{install_folder}/bin/pg_ctl status -D {data_folder} -o "-p {port}"'
        res = subprocess.run(cmd_stop, shell=True, check=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=TIMEOUT)
        return True, str(res)
    except Exception as e:
        print(f"探测 PostgreSQL 失败: {e}")
        return False, str(e)


def installcheck_postgresql(compile_folder, timeout=TIMEOUT):
    """
    使用 MSYS2 bash 执行 make installcheck，并捕获输出。
    """
    commands = r"""
    cd {compile_folder}
    make installcheck PGUSER={user} PGPORT={port}
    exit
    """.format(compile_folder=compile_folder, user=user, port=port)
    try:
        proc = subprocess.Popen(
            [bash_path, '-l'],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8'
        )

        out, warn_err = proc.communicate(commands, timeout=timeout)
        # print("------ 标准输出 ------")
        # print(type(out))
        # print("out:", out)
        # print("------ 错误输出 ------")
        # print(warn_err)

        if "failed" in warn_err.lower():
            out = warn_err
        return True, out

    except subprocess.TimeoutExpired as e:
        partial_out = e.stdout  # 已输出的部分
        proc.kill()  # 别忘了杀掉子进程
        return True, partial_out

    except Exception as e:
        print(f"执行 installcheck 失败: {e}")
        return False, str(e)


if __name__ == "__main__":
    compile_folder, data_folder = "", ""
    init_postgresql(compile_folder, data_folder)
    installcheck_postgresql(compile_folder)

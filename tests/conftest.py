"""pytest 全局配置。

做两件事：
1. 把项目根目录与 tests 目录放进 ``sys.path``，让 ``import auth_helpers`` 在所有
   测试模块里都可用（tests/ 不是包，走的是同级模块导入）；
2. **在收集测试之前**把鉴权相关环境变量固定下来。

第 2 点很关键：``services.auth_context.load_auth_config()`` 每次调用都重新读
环境变量（刻意不做缓存），所以如果开发者本机设过 ``RAG_JWT_SECRET``，
测试签发的令牌就会和校验用的密钥不一致，出现「本机能过、CI 挂掉」这类
依赖环境的假失败。这里强制覆盖，保证测试是自洽且可复现的。
"""

from __future__ import annotations

import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent

for path in (PROJECT_ROOT, TESTS_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from auth_helpers import TEST_SECRET  # noqa: E402
from services.auth_context import (  # noqa: E402
    DEFAULT_ALGORITHM,
    DEFAULT_AUDIENCE,
    DEFAULT_ISSUER,
    DEFAULT_LEEWAY_SECONDS,
)

# 强制覆盖，避免测试结果依赖开发者本机环境
os.environ["RAG_JWT_SECRET"] = TEST_SECRET
os.environ["RAG_JWT_ALGORITHM"] = DEFAULT_ALGORITHM
os.environ["RAG_JWT_ISSUER"] = DEFAULT_ISSUER
os.environ["RAG_JWT_AUDIENCE"] = DEFAULT_AUDIENCE
os.environ["RAG_JWT_LEEWAY_SECONDS"] = str(DEFAULT_LEEWAY_SECONDS)
# 测试用真实密钥，因此这条「允许回退到内置测试密钥」的后门必须关掉，
# 否则「密钥缺失时应报错」的测试会被它意外放行。
os.environ.pop("RAG_ALLOW_TEST_JWT_SECRET", None)

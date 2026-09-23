"""接线守卫（B8）：用 AST 锁住几条「看起来能用、一改就静默失效」的架构约束。

为什么需要这个文件
------------------
B7 把检索链路从 A 轨（种子 FAQ，没有 tenant / ACL 概念）切到了 B 轨
（chunk 级索引 + 服务端构造的权限过滤）。这次切换**此前只有人工核对**：
谁把 `routers/retrieval.py` 里那两行改回去，或给 `search_chunk_index` 的 `access`
加个默认值，测试**仍然会全绿** —— 但要等到线上检索越权才会暴露。

这类「接线」是最容易被后续改动悄悄拆掉的东西：表达式还在、测试还绿、行为已经变了。
所以这里用 AST 把它钉死（踩坑 **D1**：AST 守卫 > 源码字符串匹配）。

守什么（三条，来自 B8 作业规程）
--------------------------------
1. `routers/retrieval.py` 必须**真的调用** chunk 检索与权限过滤构造函数；
2. `utils/vector_retriever.py::search_chunk_index` 的 `access` 参数
   **必须没有默认值、必须有非 Optional 的注解** ——
   默认值等于给「不带权限过滤的检索」开了后门；
3. `routers/*.py` 一律**不得 import `jwt`** —— 身份解析只允许在
   `services/auth_context.py`；在 router 里重建等于绕开统一鉴权
   （踩坑 C1：JWT 的 `sub` 不是 `users.id`，一个不报错的静默失效）。

为什么每个守卫都配一条「健全性检查」
------------------------------------
守卫最坏的失效方式是**静默变成空**：目标文件被挪走、函数被改名，
断言就再无对象可执行，测试照样绿。所以：
* 找不到函数就**报错**（`_function_def` 不返回空值）；
* 扫到的 router 文件数不足就**报错**（见末尾两条）。

用法说明
--------
本文件自带 AST 辅助函数，与 `tests/test_ingestion_pipeline.py::TestProductionCallPoints`
里的同名实现同源。**有意没有抽公共模块**：抽取需要改动那个既有测试文件，
而接线守卫的价值在于"随时能加一条"，不该让每次新增守卫都去动老文件。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROUTERS_DIR = PROJECT_ROOT / "routers"
ROUTER_FILES = sorted(ROUTERS_DIR.glob("*.py"))


# ---------------------------------------------------------------- AST 辅助


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _referenced_names(tree: ast.Module) -> set[str]:
    """被**引用**的名字（``Name`` 节点 + ``Attribute`` 的属性名）。

    查"引用"而不只是"调用"：默认实现常以 ``x or f`` 的形式注入
    （例如 ``self.chunker = chunker or chunk_document``），那也是生产调用点。
    """

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def _called_names(tree: ast.Module) -> set[str]:
    """真正出现在 ``Call`` 位置的名字 —— 与"只 import 进来没用"区分开。"""

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def _imported_modules(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _function_def(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    """按名字取函数定义；**取不到直接失败**（守卫不许静默失效）。"""

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"守卫目标 `{name}` 不存在——函数被改名或删掉了，这条守卫已经失效")


def _all_args(args: ast.arguments) -> list[ast.arg]:
    return [*args.posonlyargs, *args.args, *args.kwonlyargs]


# ---------------------------------------------------------------- 守卫自身的健全性


def test_guarded_files_exist() -> None:
    """目标文件被挪走时，守卫必须立刻失败，而不是变成空断言。"""

    for path in (
        ROUTERS_DIR / "retrieval.py",
        PROJECT_ROOT / "utils" / "vector_retriever.py",
    ):
        assert path.is_file(), f"守卫目标不存在：{path}"


def test_router_files_were_actually_found() -> None:
    """扫不到 router 时，下面那条 parametrize 会**跳过**并显示为绿 —— 那是假绿。"""

    assert len(ROUTER_FILES) >= 10, f"只扫到 {len(ROUTER_FILES)} 个 router，路径或 glob 有问题"


# ---------------------------------------------------------------- 守卫 1：检索接线


def test_retrieval_router_calls_the_chunk_path_and_the_access_filter() -> None:
    """B7 的接线：正式检索路径必须**真的调用** chunk 检索与权限过滤构造。

    这两行就是 A 轨 / B 轨的分界线。删掉它们，检索会退回种子 FAQ，
    而"越权零泄漏"那批用例可能**仍然全绿**（它们走的是另一条路）——
    这正是 F1「已建未启用」的复现方式，所以要在这里钉住。
    """

    tree = _parse(ROUTERS_DIR / "retrieval.py")

    referenced = _referenced_names(tree)
    assert "retrieve_chunk_items" in referenced, "retrieval.py 没有引用 retrieve_chunk_items"
    assert "build_chunk_access_filter" in referenced, "retrieval.py 没有引用 build_chunk_access_filter"

    # 必须真的**调用**：只 import 进来不用，检索同样已经退回 A 轨
    called = _called_names(tree)
    assert "retrieve_chunk_items" in called, "retrieve_chunk_items 只被 import，没有被调用"
    assert "build_chunk_access_filter" in called, "build_chunk_access_filter 只被 import，没有被调用"


# ---------------------------------------------------------------- 守卫 2：access 无默认值


def test_search_chunk_index_requires_access_without_default() -> None:
    """`access` 不能有默认值 —— 默认值 = 给「不带权限过滤的检索」开后门。

    守的是**签名**而不是行为：行为侧已有 `ChunkRetrievalError` 的负向测试锁定，
    但如果有人把签名改成 `access: ChunkAccessFilter | None = None`，
    新写的调用方就会**少传一个参数**而不自知，而老测试仍可能全绿。
    """

    tree = _parse(PROJECT_ROOT / "utils" / "vector_retriever.py")
    args = _function_def(tree, "search_chunk_index").args

    assert "access" in {a.arg for a in _all_args(args)}, "search_chunk_index 必须显式接收 access"

    # 位置参数：defaults 与 args 的**末尾**对齐（所以这里要 reversed）
    positional = [*args.posonlyargs, *args.args]
    positional_defaults = {a.arg for a, _ in zip(reversed(positional), reversed(args.defaults))}
    assert "access" not in positional_defaults, "access 不允许有位置默认值"

    # keyword-only：kw_defaults 与 kwonlyargs 一一对齐，None 表示"没有默认值"
    kwonly_with_default = {
        a.arg for a, default in zip(args.kwonlyargs, args.kw_defaults) if default is not None
    }
    assert "access" not in kwonly_with_default, "access 不允许有 keyword 默认值"


def test_access_annotation_is_neither_missing_nor_optional() -> None:
    """`access` 的注解必须存在，且**不能含 `None` / `Optional`**。

    这条专门堵「软化写法」：先给 `Optional[ChunkAccessFilter] = None`，
    再在函数体里写 `if access is None: 全库检索` —— 那样安全要求就变成了可选行为。
    """

    tree = _parse(PROJECT_ROOT / "utils" / "vector_retriever.py")
    args = _function_def(tree, "search_chunk_index").args

    annotation = {a.arg: a.annotation for a in _all_args(args)}.get("access")
    assert annotation is not None, "access 必须有类型注解"

    rendered = ast.unparse(annotation)
    assert "None" not in rendered, f"access 注解不允许含 None：{rendered}"
    assert "Optional" not in rendered, f"access 注解不允许用 Optional：{rendered}"


# ---------------------------------------------------------------- 守卫 3：router 不解析令牌


@pytest.mark.parametrize("path", ROUTER_FILES, ids=lambda item: item.name)
def test_routers_do_not_import_jwt(path: Path) -> None:
    """身份解析只允许在 `services/auth_context.py`。

    在 router 里 import `jwt`，等于绕开统一鉴权自己再解析一遍 ——
    这种代码"看起来能用"，但它会慢慢长出第二套身份规则（踩坑 C1）。
    """

    modules = _imported_modules(_parse(path))
    offending = {module for module in modules if module == "jwt" or module.startswith("jwt.")}

    assert not offending, f"{path.name} 不得 import jwt（身份解析只许在 auth_context）：{offending}"


# ---------------------------------------------------------------- 守卫自己的反向验证


def test_guards_actually_detect_violations(tmp_path: Path) -> None:
    """**反向验证**：用已知的违规样例喂给守卫逻辑，确认它真的会红。

    没有这条，前面所有断言都可能是"装饰"—— 比如判定写反了、
    `kw_defaults` 与 `kwonlyargs` 对错了位置，结果永远是绿。
    守卫本身也要有测试（同一件事：**测不到的分支等于没测**，见踩坑 D10）。
    """

    # ① 违规样例：access 带默认值（正是守卫 2 要拦的写法）
    violating = tmp_path / "violating_signature.py"
    violating.write_text(
        "def search_chunk_index(query, *, access=None):\n    return []\n",
        encoding="utf-8",
    )
    args = _function_def(_parse(violating), "search_chunk_index").args
    kwonly_with_default = {
        arg.arg for arg, default in zip(args.kwonlyargs, args.kw_defaults) if default is not None
    }
    assert "access" in kwonly_with_default, "守卫漏掉了「access 带 keyword 默认值」这种违规"

    # ② 违规样例：router 里 import jwt
    bad_router = tmp_path / "bad_router.py"
    bad_router.write_text("import jwt\n", encoding="utf-8")
    modules = _imported_modules(_parse(bad_router))
    offending = {module for module in modules if module == "jwt" or module.startswith("jwt.")}
    assert offending, "守卫漏掉了 `import jwt`"

    # ③ 违规样例：函数被改名 → 守卫必须**报错**而不是静默放过
    with pytest.raises(AssertionError, match="已经失效"):
        _function_def(_parse(violating), "search_chunk_index_renamed")

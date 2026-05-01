# Plan: SkillRegistry — 弥补 Hermes Agent 与 gbrain 的 12 个技能差距

## 1. 目标

在 Hermes Agent 中构建一个完整的 `SkillRegistry` 系统，类比现有的 `ToolRegistry`。
12个待弥补的差距：

| # | Gap | 现状 | 目标 |
|---|-----|------|------|
| 1 | 无 SkillRegistry | skills 散落在 filesystem scan | 中心化 SkillRegistry 单例 |
| 2 | 无 SkillEntry | 技能以原始 dict 管理 | SkillEntry 数据类 |
| 3 | 无统一技能发现 API | prompt_builder 内重复扫描 | registry.get_all_skills() |
| 4 | 无 dispatch() 分发机制 | skill_view 只能手动调用 | registry.dispatch(name, args) |
| 5 | 无 provenance 追踪 | bundled/hub/user/external 无区分 | skill.source 字段 |
| 6 | 无 check_fn 运行时检查 | 技能一直"可用" | registry.is_skill_available(name) |
| 7 | requires_tools 无中心记录 | 在 frontmatter 里无索引 | registry.get_skill_dependencies() |
| 8 | 无技能参数 schema | 技能无参数接口定义 | skill.schema |
| 9 | 无 Skillset 分组机制 | 所有技能平铺 | skill.category / skillset |
| 10 | 插件不支持技能 | MCP 只注册工具 | skill 注册钩子 |
| 11 | Skills/Tools 配置不一致 | 分别配置不统一 | 统一 config 结构 |
| 12 | 重复扫描文件系统 | 每次 prompt 都扫描 | 启动时注册，后续缓存 |

---

## 2. 当前代码架构（read-only 摘要）

### 工具端参考（ToolRegistry — `tools/registry.py`）

```python
class ToolEntry:
    __slots__ = ("name", "toolset", "schema", "handler", "check_fn",
                 "requires_env", "is_async", "description", "emoji",
                 "max_result_size_chars")

class ToolRegistry:
    def register(name, toolset, schema, handler, check_fn, ...)
    def deregister(name)
    def get_definitions(tool_names) -> List[dict]  # OpenAI format
    def dispatch(name, args, **kwargs) -> str
    def get_all_tool_names() -> List[str]
    def is_toolset_available(toolset) -> bool
    def get_available_toolsets() -> Dict
    # ... query helpers

registry = ToolRegistry()  # singleton
```

### 技能端现状（分散）

| 模块 | 职责 |
|------|------|
| `agent/skill_utils.py` | frontmatter 解析、platform 过滤、get_all_skills_dirs() |
| `agent/prompt_builder.py` | `_build_skills_prompt_section()` — 扫描 + 缓存 |
| `tools/skills_tool.py` | `skills_list()` / `skill_view()` — 工具注册在 registry |
| `tools/skills_hub.py` | Hub 仓库同步、索引 |
| `tools/skills_sync.py` | 同步 bundled skills |
| `tools/skill_manager_tool.py` | create/edit/delete skill 管理操作 |
| `hermes_cli/skills_config.py` | `hermes skills` CLI 配置 |

### 技能来源（4个）

```
~/.hermes/skills/            # local (user created + bundled synced here)
~/.hermes/skills/.hub/       # hub installed
~/.hermes/skills/.external/  # user config: skills.external_dirs
(bundled/ skills/ in repo)   # repo bundled — synced to ~/.hermes on install
```

---

## 3. 实施计划（分 4 步，顺序执行）

### Step 1 — 创建 `agent/skill_registry.py`（新增文件）

**目标**：创建 SkillEntry + SkillRegistry 类，镜像 ToolRegistry 架构。

**文件**：`agent/skill_registry.py`（注意放 agent/ 而非 tools/，因为技能是 agent 级概念）

**SkillEntry 字段**：

```python
class SkillEntry:
    __slots__ = (
        "name",           # str — 技能唯一名
        "source",         # str — "bundled" | "hub" | "user" | "external"
        "path",           # Path — SKILL.md 路径
        "category",       # str — 分类（从 frontmatter category 或目录）
        "description",    # str
        "frontmatter",    # dict — 原始 frontmatter
        "check_fn",       # Callable | None — 运行时可用性检查
        "requires_tools", # List[str] — 依赖的工具名
        "fallback_for_tools", # List[str] — 备用工具
        "schema",         # dict | None — 参数 schema（参考 tool schema）
        "readiness",      # SkillReadinessStatus
        "metadata",       # dict — 任意扩展数据
    )
```

**SkillRegistry 方法**：

```python
class SkillRegistry:
    # 内部
    _skills: Dict[str, SkillEntry]
    _skills_by_category: Dict[str, List[str]]  # category → [skill_names]

    # 注册（由各个来源模块调用）
    def register(entry: SkillEntry) -> None
    def deregister(name: str) -> None

    # 查询
    def get_all_skills() -> List[SkillEntry]
    def get_skill(name: str) -> SkillEntry | None
    def get_skill_names() -> List[str]
    def get_categories() -> List[str]
    def get_skills_by_category(category: str) -> List[SkillEntry]
    def get_skill_definitions(names: Set[str]) -> List[dict]  # OpenAI-compatible schema
    def get_skill_dependencies(name: str) -> dict  # {requires_tools, fallback_for_tools}
    def is_skill_available(name: str) -> bool  # check_fn() 或默认 True
    def get_source(name: str) -> str | None

    # 分发（类比 tool dispatch，但返回 skill content dict）
    def dispatch(name: str, task_id: str | None = None) -> dict

    # 批量（类比 toolset availability）
    def check_all_availability() -> Dict[str, SkillReadinessStatus]

    # Snapshot（生成 prompt_builder 所需的索引）
    def build_snapshot() -> dict
```

**关键设计决策**：
- `SkillRegistry` 是**内存只读缓存**，启动时由各个来源模块（bundled_sync、hub、user）向其注册。
- `dispatch()` 的返回值是 dict（不是 JSON string），与 tool dispatch 不同，因为技能内容需要后续处理。
- Snapshot 格式兼容现有 `prompt_builder._load_skills_snapshot()` 的 JSON 结构，确保不需要改 prompt_builder。

**注册点（启动时调用）**：

```python
# 三个注册入口，按顺序执行（后面的覆盖前面的同名技能）
1. bundled sync  → register_bundled_skills()   [source="bundled"]
2. hub install   → register_hub_skills()        [source="hub"]
3. user created  → register_user_skills()        [source="user"]
4. external dirs → register_external_skills()    [source="external"]
```

---

### Step 2 — 修改现有模块，注入注册调用（改造为主）

**2a. `tools/skills_sync.py` — bundled sync 时注册**

```python
# 在 sync_skills() 成功后，遍历 _discover_bundled_skills() 结果
# 对每个 skill_dir 调用 skill_registry.register(entry)
```

**2b. `tools/skills_hub.py` — hub install 时注册**

```python
# 在 install_skill() / _install_from_index() 成功后注册
# 在 uninstall_skill() 时 deregister()
```

**2c. `tools/skill_manager_tool.py` — user create/edit/delete 时注册/deregister**

```python
# _create_skill() 成功后 register(source="user")
# _delete_skill() 时 deregister()
```

**2d. `agent/skill_utils.py` — external dirs 注册**

```python
# 在 get_all_skills_dirs() 基础上，增加 register_external_skills()
# 在启动流程中调用（prompt_builder 首次导入时）
```

**2e. `hermes_cli/skills_config.py` — 禁用/启用技能时**

```python
# 禁用技能时：在 SkillRegistry 中标记 unavailable
# 启用技能时：重新检查 check_fn
```

---

### Step 3 — 新增 `check_fn` 运行时检查机制（Gap 6）

**问题**：技能目前总是显示为 available，无法在运行时检查依赖。

**解决方案**：

在 `SKILL.md` frontmatter 中扩展 `check_fn` 声明（未来兼容，可选）：

```yaml
---
name: whisper
description: Speech recognition with Whisper
check:
  env_vars: [OPENAI_API_KEY]        # check_fn 等价：验证 env var
  commands: [ffmpeg]                # check_fn 等价：验证命令存在
  python_imports: [openai]           # check_fn 等价：验证 import
---
```

`SkillRegistry.is_skill_available(name)` 实现：

```python
def is_skill_available(name: str) -> bool:
    entry = self._skills.get(name)
    if not entry:
        return False
    if entry.check_fn is None:
        return True
    try:
        return bool(entry.check_fn())
    except Exception:
        return False
```

同时在 `skills_tool.py` 的 `SkillReadinessStatus` 已有 `AVAILABLE / SETUP_NEEDED / UNSUPPORTED`，复用这个 enum。

---

### Step 4 — 扩展 frontmatter schema 支持（Gap 8 — 技能参数 schema）

**问题**：技能目前无参数 schema，无法让 LLM 知道如何调用技能。

**解决方案**：

在 `SKILL.md` frontmatter 中添加可选的 `parameters` 字段：

```yaml
---
name: axolotl
description: Fine-tune LLMs with Axolotl
parameters:
  - name: model_name
    type: string
    description: Base model to fine-tune
    required: true
  - name: num_epochs
    type: integer
    description: Number of training epochs
    default: 3
---
```

`SkillRegistry.get_skill_definitions()` 返回 OpenAI-compatible schema：

```python
{
    "type": "function",
    "function": {
        "name": "axolotl",
        "description": "Fine-tune LLMs with Axolotl",
        "parameters": {
            "type": "object",
            "properties": {...},
            "required": [...]
        }
    }
}
```

这样技能可以被当作"工具"调用，模型可以 `requires_tools` 声明依赖工具，技能可以 `dispatch()` 调用。

---

## 4. 文件变更清单

| 操作 | 文件 |
|------|------|
| **新增** | `agent/skill_registry.py` — SkillEntry + SkillRegistry |
| **新增** | `agent/skill_registry_snapshot.py` — snapshot 生成/加载 |
| **改造** | `tools/skills_sync.py` — 注册 bundled skills |
| **改造** | `tools/skills_hub.py` — 注册 hub skills |
| **改造** | `tools/skill_manager_tool.py` — 注册/deregister user skills |
| **改造** | `agent/skill_utils.py` — 注册 external skills，启动时注册流程 |
| **改造** | `agent/prompt_builder.py` — 改用 SkillRegistry.get_all_skills()（可选，若 registry 足够快则替换 filesystem scan） |
| **改造** | `hermes_cli/skills_config.py` — SkillRegistry 感知 |
| **改造** | `tools/registry.py` — 添加 `register_skill_hook()` 插件钩子（Gap 10） |
| **改造** | `AGENTS.md` — 文档更新 |
| **改造** | `hermes_cli/config.py` — skills 配置结构与 tools 对齐（Gap 11） |

---

## 5. 测试计划

| 测试文件 | 覆盖内容 |
|---------|---------|
| `tests/agent/test_skill_registry.py` | SkillEntry, SkillRegistry 所有方法 |
| `tests/agent/test_skill_registry_snapshot.py` | snapshot round-trip, 格式兼容 |
| `tests/tools/test_skills_sync.py` | bundled 注册逻辑 |
| `tests/tools/test_skills_hub.py` | hub install/uninstall 注册 |
| `tests/cli/test_skills_config.py` | CLI 禁用/启用与 registry 联动 |
| `tests/agent/test_prompt_builder.py` | SkillRegistry 缓存集成 |

**运行命令**：
```bash
source venv/bin/activate
python -m pytest tests/agent/test_skill_registry.py tests/agent/test_skill_registry_snapshot.py -q
```

---

## 6. 风险与权衡

| 风险 | 影响 | 缓解方案 |
|------|------|---------|
| SkillRegistry 启动注册慢 | 影响冷启动时间 | 使用异步注册 + LRU 缓存 |
| 破坏现有 prompt_builder 缓存 | prompt 构建变慢 | snapshot 格式完全兼容，复用现有缓存逻辑 |
| 多个注册点同时修改 registry | 竞争条件 | 注册流程在单线程主进程执行，使用 threading.Lock |
| Gap 10 (插件) 改动 registry.py | 可能破坏现有工具注册 | 插件钩子仅影响技能注册，工具注册路径不变 |

---

## 7. 实施顺序

```
Phase 1（基础层）:
  Step 1  → agent/skill_registry.py（SkillEntry + SkillRegistry）
  Step 3  → check_fn 机制

Phase 2（注册注入）:
  Step 2a → skills_sync.py 注册
  Step 2b → skills_hub.py 注册
  Step 2c → skill_manager_tool.py 注册/deregister
  Step 2d → skill_utils.py external + 启动注册

Phase 3（集成 + 增强）:
  Step 2e → skills_config.py 联动
  Step 4  → 参数 schema
  Step 2 改造 → prompt_builder 可选切换到 registry
  Step 10 → 插件钩子
  Step 11 → 配置对齐

Phase 4（测试 + 文档）:
  所有测试通过
  AGENTS.md 更新
```

---

## 8. 验证步骤

```bash
# 1. 单元测试
python -m pytest tests/agent/test_skill_registry.py -v

# 2. 验证注册流程
python -c "
from agent.skill_registry import registry
print('Skills registered:', registry.get_skill_names())
print('Categories:', registry.get_categories())
print('Sources:', {s: registry.get_source(s) for s in registry.get_skill_names()[:5]})
"

# 3. 验证 is_skill_available
python -c "
from agent.skill_registry import registry
for name in registry.get_skill_names()[:10]:
    print(f'{name}: {registry.is_skill_available(name)}')
"

# 4. 验证 snapshot 格式兼容
python -c "
from agent.prompt_builder import _load_skills_snapshot
snap = _load_skills_snapshot()
print('Snapshot keys:', list(snap.keys()) if snap else 'None')
"
```

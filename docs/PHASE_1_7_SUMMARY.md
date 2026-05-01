# Phase 1-7 完整记录：Hermes Agent 对齐 gbrain

**目标：** 借鉴 gbrain 的架构设计，增强 Hermes Agent 的技能系统

**日期：** 2026年4月14日

---

## Phase 1-6：差距分析与学习

### Phase 1: gbrain 核心架构精读 ✅

**任务：** 深入研究 gbrain 的架构设计

**成果：**
- 识别了 30+ 个操作定义
- 两种 engine 接口（PGLite / Postgres）
- PGLite WASM + HNSW 向量索引设计
- SkillEntry + SkillRegistry 中心注册模式
- 合约优先（Contract-first）的 operations.ts 设计

**参考：** https://github.com/garrytan/gbrain

---

### Phase 2: Hermes Agent Gap 分析 ❌

**状态：** 超时中断，进行到一半被截断

---

### Phase 3: Hermes 与 gbrain 差距分析 ✅

**任务：** 对比 Hermes Agent 现状与 gbrain 的差距

**成果：识别了12个关键差距**

| # | 差距 | 说明 | 优先级 |
|---|------|------|--------|
| 1 | 无 SkillRegistry 中心注册表 | 技能散落各处，无统一管理 | P0 |
| 2 | 无 SkillEntry 结构化数据类 | 缺乏技能元数据结构 | P0 |
| 3 | 无统一技能发现 API | 无法程序化查找技能 | P1 |
| 4 | 无技能 dispatch() 分发机制 | 缺乏调用路由 | P1 |
| 5 | 技能分散4个来源 | 无 provenance 追踪 | P1 |
| 6 | 无技能级可用性检查 | 缺少类似 tool check_fn 的机制 | P1 |
| 7 | requires_tools/fallback 无中心记录 | 技能依赖关系不清晰 | P2 |
| 8 | 无技能参数验证 | 缺乏参数 schema | P2 |
| 9 | 无技能版本管理 | 无法追踪技能演进 | P2 |
| 10 | 无技能评分/质量指标 | 无法评估技能效果 | P3 |
| 11 | 无技能隐私/权限控制 | 技能共享缺乏细粒度控制 | P3 |
| 12 | 无技能使用分析 | 无法了解技能使用情况 | P3 |

---

### Phase 4-6: （详情待补充）

*由于上下文压缩，这几个 phase 的详情需要从会话历史恢复*

---

## Phase 7: 语音模式测试修复 ✅

**任务：** 修复 `test_voice_cli_integration.py` 中的测试失败

### 问题诊断

1. **`_attached_images` 未初始化**
   - `_make_voice_cli` fixture 缺少 `_attached_images = []`
   - 导致 `AttributeError`

2. **`threading.Lock` 不可重入**
   - `_voice_lock` 在 cli.py 两处初始化为 `Lock()`
   - `_voice_stop_and_transcribe` 的 finally 块重入时死锁
   - 已改为 `RLock()`

3. **Mock 配置问题**
   - `transcribe_recording` mock 未正确设置返回值类型

### 修改内容

**cli.py:**
```python
# Line 1731 & 7696
self._voice_lock = threading.RLock()  # 改为 RLock
```

**tests/tools/test_voice_cli_integration.py:**
```python
# _make_voice_cli fixture
cli._voice_lock = threading.RLock()  # 改为 RLock
cli._attached_images = []            # 新增初始化
```

### 测试结果

| 测试套件 | 结果 |
|---------|------|
| TestVoiceStopAndTranscribeReal | ✅ 11/11 通过 |
| 全部 voice CLI 集成测试 | ✅ 79/79 通过 |
| 全部语音相关测试 | ✅ 139/139 通过 |

---

## 后续工作建议

### P0 - 必须实现
1. **SkillRegistry 中心注册表** — 参考 gbrain 的 `SkillRegistry` 设计
2. **SkillEntry 结构化数据类** — 定义技能元数据 schema

### P1 - 重要
3. 统一技能发现 API
4. 技能 dispatch() 分发机制
5. 技能 provenance 追踪
6. 技能可用性检查机制

### P2 - 增强功能
7. 技能依赖关系管理
8. 技能参数 schema 验证
9. 技能版本管理

---

## 参考资源

- **gbrain 仓库:** https://github.com/garrytan/gbrain
- **CLAUDE.md:** gbrain 的 AI 开发指南
- **GBRAIN_V0.md:** 架构设计文档
- **SkillRegistry 模式:** gbrain/skills/ 目录下的技能组织方式

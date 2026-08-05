# CLI — 命令行接口

> 覆盖: `src/cli.py`

## 功能

Typer CLI，提供命令行一键操作。也是 API 的客户端——CLI 内部调用 ChatEngine（不入 HTTP）。

## 命令

### `run` — 单个商品生图

```bash
python -m src.cli run <image_path> --platform taobao --category 保健品 --product-info "护肝胶囊"
```

流程：
1. 读取图片 → base64
2. 初始化 ProviderRegistry + AgentRegistry
3. 创建 Session → ChatEngine.run()
4. 打印结果摘要（分析/评分/合规 + 群聊记录）

### `batch` — 批量处理

```bash
python -m src.cli batch <directory> --platform taobao
```

扫描目录下所有 `.jpg/.jpeg/.png/.webp` 文件，逐个调用 `run`。

### `agents-list` — 列出 Agent

```bash
python -m src.cli agents-list
```

输出所有已注册 Agent 的元信息和可配置参数（含 type/options/default）。

### `config-validate` — 验证配置文件

```bash
python -m src.cli config-validate
```

检查所有配置文件的格式正确性：`default.yaml`, `models.yaml`, 所有 `agents/*.yaml`。

## 编码兼容

```python
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
```

Windows 终端强制 UTF-8 输出，避免 `UnicodeEncodeError`。

## 修改指南

- **新增命令** → 添加 `@app.command()` 装饰的函数
- **修改输出格式** → 编辑对应命令的 `typer.echo()` 语句
- **新增 CLI 参数** → 在函数签名中添加 `typer.Option()` 参数

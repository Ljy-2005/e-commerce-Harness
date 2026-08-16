"""Workflow 编排层 — 模板（Skill 库）加载 / 校验 / 实例化

模板 = config/workflows/*.yaml（见 docs/workflow-design.md §3.1）
"""

import yaml
from pathlib import Path
from typing import Any

from src.workflow.models import WorkflowJob

_NODE_TYPES = ("tool", "agent", "condition", "human", "group_chat", "end", "subworkflow")


def _workflows_dir() -> Path:
    return Path(__file__).parent.parent.parent / "config" / "workflows"


def load_template(name: str) -> dict:
    """加载模板 YAML（不存在返回空 dict）"""
    path = _workflows_dir() / f"{name}.yaml"
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def list_templates() -> list[dict]:
    """Skill 库列表（不含 nodes，供画廊展示）"""
    templates = []
    for path in sorted(_workflows_dir().glob("*.yaml")):
        if path.stem.startswith("_"):
            continue
        with open(path, "r", encoding="utf-8") as f:
            tpl = yaml.safe_load(f) or {}
        if not tpl.get("name"):
            continue
        templates.append({
            "template_name": path.stem,
            "name": tpl["name"],
            "version": tpl.get("version", "1.0.0"),
            "description": tpl.get("description", ""),
            "category": tpl.get("category", ""),
            "icon": tpl.get("icon", "🧩"),
            "inputs": tpl.get("inputs", []),
            "node_count": len(tpl.get("nodes") or {}),
        })
    return templates


def validate_template(tpl: dict, agent_names: set[str]) -> list[str]:
    """结构校验，返回错误列表（空 = 通过）"""
    errors = []
    if not isinstance(tpl, dict) or not tpl.get("name"):
        return ["模板缺少 name"]
    nodes = tpl.get("nodes")
    if not isinstance(nodes, dict) or not nodes:
        return ["模板缺少 nodes"]

    names = list(nodes.keys())
    for n, cfg in nodes.items():
        if not isinstance(cfg, dict):
            errors.append(f"节点 {n} 配置必须是对象")
            continue
        ntype = cfg.get("type")
        if ntype not in _NODE_TYPES:
            errors.append(f"节点 {n} 的 type '{ntype}' 非法（可选 {_NODE_TYPES}）")
            continue
        if ntype == "agent" and cfg.get("agent") not in agent_names:
            errors.append(f"节点 {n} 引用的 Agent '{cfg.get('agent')}' 未注册")
        if ntype == "condition":
            rules = cfg.get("rules")
            if not isinstance(rules, list) or not rules:
                errors.append(f"条件节点 {n} 缺少 rules")
            else:
                for r in rules:
                    goto = r.get("goto")
                    if goto and goto not in names:
                        errors.append(f"条件节点 {n} 的 goto '{goto}' 不存在")
        if ntype == "human":
            for action, target in (cfg.get("routes") or {}).items():
                if target not in names:
                    errors.append(f"人工节点 {n} 的 routes.{action} → '{target}' 不存在")
        if cfg.get("on_error") and cfg["on_error"] not in names:
            errors.append(f"节点 {n} 的 on_error '{cfg['on_error']}' 不存在")

    # edges 目标存在性
    for e in tpl.get("edges") or []:
        if not isinstance(e, (list, tuple)) or len(e) != 2:
            errors.append(f"边 {e} 非法（应为 [from, to]）")
            continue
        for endpoint in e:
            if endpoint not in names:
                errors.append(f"边 '{endpoint}' 指向不存在的节点")

    # 起始节点
    start = tpl.get("start")
    if start and start not in names:
        errors.append(f"start '{start}' 不存在")

    return errors


def build_graph(nodes: dict, edges: list, start: str) -> tuple[dict, str]:
    """构建 {node: next_node} 默认路由表"""
    graph = {}
    order = list(nodes.keys())
    if not start:
        start = order[0]
    # 默认顺序边
    for i, n in enumerate(order):
        if i + 1 < len(order):
            graph[n] = order[i + 1]
        else:
            graph[n] = None
    # 显式 edges 覆盖默认顺序
    for a, b in edges or []:
        graph[a] = b
    return graph, start
def instantiate(
    template_name: str,
    inputs: dict,
    tenant_id: str = "default",
    mode: str = "auto",
) -> WorkflowJob:
    """实例化模板 → WorkflowJob（含输入校验）"""
    tpl = load_template(template_name)
    if not tpl:
        raise ValueError(f"模板 '{template_name}' 不存在")

    # 必填输入校验
    for spec in tpl.get("inputs") or []:
        key = spec.get("key")
        if spec.get("required") and not inputs.get(key):
            raise ValueError(f"缺少必填输入: {spec.get('label') or key}")

    job = WorkflowJob(
        template_name=template_name,
        version=str(tpl.get("version", "1.0.0")),
        tenant_id=tenant_id,
        mode=mode if mode in ("auto", "manual") else "auto",
        inputs=dict(inputs),
    )
    # 模板快照挂到 context（重跑始终用实例化时的版本）
    job.context["_snapshot"] = tpl
    return job


def get_snapshot(job: WorkflowJob) -> dict:
    """从 job 取模板快照（保证重跑用旧版本）"""
    snap = job.context.get("_snapshot") or {}
    if not snap:
        snap = load_template(job.template_name)
    return snap

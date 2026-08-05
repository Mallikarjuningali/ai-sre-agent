"""Recursive resource topology tree (Investigation page)."""
from __future__ import annotations

import html

import streamlit as st

from .badges import status_dot

_TYPE_ICON = {
    "load balancer": "⚖️",
    "ec2 instance": "🖥️",
    "auto scaling group": "📈",
    "rds instance": "🗄️",
    "target group": "🎯",
    "vpc": "🌐",
}


def _icon_for(resource_type: str) -> str:
    return _TYPE_ICON.get(str(resource_type).lower(), "📦")


def _render_node(node: dict, depth: int) -> str:
    indent = depth * 22
    node_id = node.get("id", "—")
    node_type = node.get("type", "Resource")
    status = node.get("status", "unknown")
    children = node.get("children") or []

    html_out = f"""
    <div style="margin-left:{indent}px; display:flex; align-items:center; gap:8px; padding:6px 0;
                border-left: {'1px solid var(--border-subtle)' if depth > 0 else 'none'}; padding-left: {'12px' if depth > 0 else '0'};">
      <span style="font-size:14px;">{_icon_for(node_type)}</span>
      <span class="ao-mono" style="font-size:12.5px; color:var(--text-primary); font-weight:600;">{html.escape(str(node_id))}</span>
      <span style="font-size:11.5px; color:var(--text-muted);">{html.escape(str(node_type))}</span>
      {status_dot(status)}
      <span style="font-size:11px; color:var(--text-muted); text-transform:uppercase;">{html.escape(str(status))}</span>
    </div>
    """
    for child in children:
        html_out += _render_node(child, depth + 1)
    return html_out


def render_resource_tree(tree: dict | None) -> None:
    if not tree:
        from .cards import empty_state

        empty_state("No resource topology", "This report did not include a resource_tree.", "🌳")
        return
    st.markdown(_render_node(tree, 0), unsafe_allow_html=True)

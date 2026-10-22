"""Freeze a template launch as a plan in its own Quick Trial project."""

from quant_studio import QuantStudioError
from quant_studio.projects import ProjectStore
from quant_studio.recipes import RecipeStore
from quant_studio.templates import (
    apply_factor_selection,
    load_template,
    render_template,
)


def create_trial(root, template_id, knobs, *, factors=None, snapshot=None):
    template = load_template(template_id)
    if template_id == "etf-weekly-research":
        raise QuantStudioError("请从已登记研究卡的项目启动 ETF 研究")
    rendered = render_template(template, knobs)
    if factors is not None:
        apply_factor_selection(rendered.config, template, factors)
    recipe = RecipeStore(root).save(
        "快速试跑 · " + template.title,
        template_id,
        rendered.config,
        cli={
            knob["name"]: rendered.values[knob["name"]]
            for knob in template.knobs
            if knob.get("target") == "cli"
        },
        snapshot=snapshot,
    )
    project = ProjectStore(root).save(
        "快速试跑 · " + template.title,
        "探索模板的运行结果；后续可编辑研究问题、修改方案并保存结论。",
        recipes=[{"id": recipe["id"], "revision": recipe["revision"]}],
    )
    return recipe, project

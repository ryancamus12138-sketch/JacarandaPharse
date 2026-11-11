"""模板（只读）。模板是文件，没有数据库表，所以这个模块没有 models 和 repository 两层。"""

from __future__ import annotations

from jacara.core.owner import Owner
from jacara.core.permissions import require
from jacara.modules.templates.schemas import TemplateFieldOut, TemplateOut, TemplateRuleOut
from jacara.templates import Template, TemplateError, load_template, template_names


class TemplateNotFound(LookupError):
    pass


def to_out(t: Template) -> TemplateOut:
    return TemplateOut(
        name=t.name,
        version=t.version,
        title=t.title,
        description=t.description,
        fields=[
            TemplateFieldOut(
                name=f.name,
                label=f.label,
                type=f.type,
                required=f.required,
                description=f.description,
            )
            for f in t.fields
        ],
        rules=[
            TemplateRuleOut(id=r.id, title=r.title, needs=list(r.needs), severity=r.severity)
            for r in t.rules
        ],
    )


class TemplateService:
    def __init__(self, owner: Owner) -> None:
        self.owner = owner

    def list(self) -> list[TemplateOut]:
        require(self.owner, "document:read")
        return [to_out(load_template(name)) for name in template_names()]

    def get(self, name: str) -> TemplateOut:
        require(self.owner, "document:read")
        try:
            return to_out(load_template(name))
        except TemplateError as err:
            raise TemplateNotFound(str(err)) from err

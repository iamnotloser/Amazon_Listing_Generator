"""Pydantic Schema：强制 LLM 输出结构化字段，再由代码拼接成最终文案。"""
from typing import List
from pydantic import BaseModel, Field


class TitleFields(BaseModel):
    """商品名称 + 商品亮点字段。LLM 只抽字段，拼装与字符数由代码控制。

    字段顺序遵循 Amazon 官方信息顺序：
    品牌 → 款式/风味 → 商品类型 → 关键属性 → 颜色 → 尺寸/包装 → 型号
    """
    brand: str = Field(description="品牌名，来自输入，不要改写")
    style: str = Field(default="", description="款式/风味短语（英文），如 Extra Large、Probiotic")
    product_type: str = Field(description="商品类型核心名词（英文，单数），如 Sink Caddy")
    attribute_1: str = Field(description="关键属性 1（英文短语，2-4 词），如 with Drying Stone")
    attribute_2: str = Field(default="", description="关键属性 2（英文短语，2-4 词），如 Sponge and Brush Holder")
    color: str = Field(default="", description="颜色（英文），如 Black")
    size: str = Field(default="", description="尺寸或包装数量（英文，用阿拉伯数字），如 9.65 x 5.9 x 5.5 in")
    model: str = Field(default="", description="型号，没有则留空")
    highlights: List[str] = Field(
        default_factory=list,
        description='商品亮点：3-6 个逗号分隔的短语（英文，每个 2-6 词，不是完整句子），'
                    '总长不超过 125 字符。例如 ["No Drainage Needed", "2.6mm Stainless Steel", "6 Compartments"]')
    zh_summary: str = Field(description="一句话中文说明这版名称的侧重，供用户审核")


class BulletItem(BaseModel):
    benefit: str = Field(description="全大写利益点，2-4 个英文单词，不带冒号")
    feature: str = Field(description="功能说明，英文 15-25 个单词，必须包含具体参数")
    zh: str = Field(description="对应的中文说明，用于人工审核")


class BulletSet(BaseModel):
    direction: str = Field(description="这套五点的主打方向，如 参数导向 / 场景导向 / 竞品差异化")
    bullets: List[BulletItem] = Field(description="恰好 5 条")
    keywords_used: List[str] = Field(default_factory=list, description="这 5 条里实际用到的目标关键词")


class BulletPlan(BaseModel):
    """Step 3a：先出 3 套方向供用户选，不写完整文案。"""
    plans: List[dict] = Field(description='3 套方案，每套 {"direction": "...", "angles": ["第1条角度", ...共5条]}')


class CompetitorMatch(BaseModel):
    asin: str
    reason: str = Field(default="", description="一句中文说明")


class CompetitorFilter(BaseModel):
    """搜索结果里筛出与目标商品同品类的候选。"""
    relevant: List[CompetitorMatch] = Field(default_factory=list)
    rejected: List[CompetitorMatch] = Field(default_factory=list)


class ComplianceIssue(BaseModel):
    level: str = Field(description="high / medium / low")
    item: str = Field(description="出问题的位置，如 标题 / 五点第3条")
    problem: str = Field(description="问题描述")
    fix: str = Field(description="建议改法")

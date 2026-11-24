"""LLM 调用层：LCEL + Pydantic 结构化输出 + 解析失败自动重试。"""
import json
import os
import re

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_openai import ChatOpenAI

from listing_gen import prompts
from listing_gen.schemas import BulletPlan, BulletSet, TitleFields
from listing_gen.title_builder import (build_highlights, build_product_name,
                                       format_bullet)


def get_llm(temperature: float = 0.3) -> ChatOpenAI:
    """支持任何 OpenAI 兼容服务（OpenAI / DeepSeek / 豆包 / 通义）"""
    return ChatOpenAI(
        model=os.getenv("MODEL_NAME", "gpt-4o-mini"),
        api_key=os.getenv("LLM_API_KEY"),
        base_url=os.getenv("LLM_BASE_URL") or None,
        temperature=temperature,
        max_tokens=2048,
    )


def _extract_json(text: str) -> str:
    """从可能带 markdown 围栏的输出里抠出 JSON"""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        return text[start:end + 1]
    return text


def call_structured(llm, system: str, human: str, human_vars: dict,
                    schema, temperature: float = 0.3, max_retry: int = 2) -> dict:
    """调用 LLM 并按 Pydantic schema 校验；失败时把错误回喂重试。

    注意：不用 ChatPromptTemplate，避免 JSON Schema 里的花括号被当成模板变量。
    """
    parser = PydanticOutputParser(pydantic_object=schema)
    sys_text = system + "\n\n严格按以下 JSON Schema 输出：\n" + parser.get_format_instructions()

    try:
        human_text = human.format(**human_vars)
    except (KeyError, IndexError, ValueError):
        human_text = human

    last_err = None
    for attempt in range(max_retry + 1):
        try:
            msg = llm.invoke([SystemMessage(content=sys_text),
                              HumanMessage(content=human_text)])
            data = json.loads(_extract_json(msg.content))
            return schema(**data).model_dump()
        except Exception as e:
            last_err = e
            human_text += (f"\n\n上一次输出解析失败：{e}\n"
                           f"请只输出符合 Schema 的合法 JSON，不要任何解释或 markdown 围栏。")
    raise RuntimeError(f"LLM 结构化输出连续 {max_retry + 1} 次失败: {last_err}")


def generate_titles(site: dict, product_input: str, product_facts: str,
                    competitor_titles: list, keywords: list, differentiators: str = "",
                    brand: str = "") -> list:
    """生成商品名称 + 商品亮点，每版一个角度，共 3 版"""
    llm = get_llm(temperature=0.4)
    results = []
    angles = ["核心功能优先", "使用场景优先", "规格参数优先"]
    for angle in angles:
        sys_prompt = prompts.TITLE_SYSTEM.format(
            site_spelling=site["spelling"],
            keywords=", ".join(keywords[:15]) or "（无，请从竞品标题自行判断）",
            product_facts=product_facts,
            differentiators=differentiators or "（未提供，请从产品事实中自行判断）",
        ) + f"\n本次侧重：{angle}"
        fields = call_structured(
            llm, sys_prompt, prompts.TITLE_HUMAN,
            {
                "product_input": product_input,
                "competitor_titles": "\n".join(f"- {t}" for t in competitor_titles[:5]),
                "format_instructions": "",
            },
            TitleFields, temperature=0.4,
        )
        name = build_product_name(fields, site["title_max"])
        highlights = build_highlights(fields, site["highlight_max"])
        results.append({
            "angle": angle,
            "product_name": name,
            "highlights": highlights,
            "fields": fields,
        })
    return results


def generate_bullet_plans(site: dict, product_facts: str, keywords: list,
                          competitor_style: str = "") -> list:
    llm = get_llm(temperature=0.6)
    sys_prompt = prompts.BULLET_PLAN_SYSTEM.format(
        site_language=site["language"], product_facts=product_facts,
        keywords=", ".join(keywords[:20]),
        competitor_style=competitor_style or "（无竞品数据，按每条 200-260 字符把握）",
    )
    data = call_structured(llm, sys_prompt, prompts.BULLET_PLAN_HUMAN,
                           {"format_instructions": ""}, BulletPlan, temperature=0.6)
    return data["plans"]


def generate_bullets(site: dict, product_facts: str, keywords: list,
                     direction: str, angles: list, competitor_style: str = "") -> dict:
    llm = get_llm(temperature=0.4)
    sys_prompt = prompts.BULLET_SYSTEM.format(
        site_language=site["language"], site_spelling=site["spelling"],
        direction=direction, angles="\n".join(f"{i+1}. {a}" for i, a in enumerate(angles)),
        keywords=", ".join(keywords[:20]), product_facts=product_facts,
        competitor_style=competitor_style or "（无竞品数据，按每条 200-260 字符把握）",
    )
    data = call_structured(llm, sys_prompt, prompts.BULLET_HUMAN,
                           {"format_instructions": ""}, BulletSet, temperature=0.4)
    data["formatted"] = [format_bullet(b) for b in data["bullets"]]
    return data


def rewrite_bullet_feature(site: dict, benefit: str, feature: str,
                           target_min: int, target_max: int, product_facts: str = "",
                           feedback: str = "") -> dict:
    """把单条五点的功能说明改写进目标字符区间，返回 {feature, zh}。"""
    llm = get_llm(temperature=0.3)
    sys_text = (
        f"你是 Amazon Listing 文案编辑，负责把一条五点描述的功能说明改写到指定长度。\n"
        f"利益点（保持不变）：{benefit}\n当前功能说明：{feature}\n"
        f"要求：使用{site['language']}（{site['spelling']}拼写），改写后的功能说明必须在 "
        f"{target_min}-{target_max} 个字符之间（含空格）；保留原有事实，可补充已给出的具体参数或使用场景，"
        "不得新增未提供的信息、不得夸大、不得与其他品牌比较。只输出 JSON："
        '{"feature": "...", "zh": "对应的中文说明"}'
    )
    if feedback:
        sys_text += f"\n上一版不合格：{feedback}"
    human = f"产品事实：\n{product_facts}" if product_facts else "按当前说明改写长度即可。"
    msg = llm.invoke([SystemMessage(content=sys_text), HumanMessage(content=human)])
    data = json.loads(_extract_json(msg.content))
    return {"feature": (data.get("feature") or "").strip(),
            "zh": (data.get("zh") or "").strip()}


def translate_listing(name: str, highlights: str) -> dict:
    """把商品名称与亮点翻译成中文，供中英对照审核。"""
    llm = get_llm(temperature=0.2)
    sys_text = ("你是电商文案翻译。把用户给的英文商品名称和商品亮点翻译成简体中文，"
                "忠实原意、不增删信息，短语保持短语形式。只输出 JSON："
                '{"name_zh": "...", "highlights_zh": "..."}')
    msg = llm.invoke([SystemMessage(content=sys_text),
                      HumanMessage(content=f"商品名称：{name}\n商品亮点：{highlights}")])
    data = json.loads(_extract_json(msg.content))
    return {"name_zh": data.get("name_zh", ""), "highlights_zh": data.get("highlights_zh", "")}


def revise(site: dict, current: str, instruction: str, constraint: str) -> str:
    llm = get_llm(temperature=0.3)
    sys_text = prompts.REVISE_SYSTEM.format(
        site_language=site["language"], constraint=constraint,
        current=current, user_instruction=instruction,
    )
    msg = llm.invoke([SystemMessage(content=sys_text),
                      HumanMessage(content="请输出修改后的文案，只给文案本身。")])
    return msg.content.strip()

"""v3 第 3 批（ADR 0038）：自动判断用哪个模板、让模型看原图。"""

from __future__ import annotations

import json
import uuid

import numpy as np

from jacara.pipeline import run as run_module
from jacara.pipeline.classify import AutoChoice, Candidate, classify, keyword_score
from jacara.pipeline.process import process_document
from jacara.pipeline.run import Pipeline
from jacara.pipeline.vision import vision_reason, weak_ocr
from jacara.plugins.extractors import LlmExtractor
from jacara.plugins.model_backends import ChatMessage, ChatResult
from jacara.plugins.model_backends.llama_server import LlamaServerBackend
from jacara.templates import load_starter

from .conftest import make_line, make_page
from .test_batch2 import HOTEL, hotel_pages, upload_file
from .test_template_library import SchemaBackend, detail


def candidates(*names: str) -> AutoChoice:
    return AutoChoice(tuple(Candidate(load_starter(n), uuid.uuid4(), uuid.uuid4()) for n in names))


HOTEL_TEXT = "OYO Hotel\nFolio No. F-100\nGuest Name Ravi\nCheck In 30/12 Check Out 31/12\nRoom"


def test_keywords_pick_the_template_when_there_is_no_model():
    auto = candidates("cn_einvoice", "hotel_folio")
    hotel = auto.candidates[1].template
    assert keyword_score(HOTEL_TEXT, hotel) > keyword_score(HOTEL_TEXT, auto.candidates[0].template)
    picked = classify(HOTEL_TEXT, auto, None)
    assert picked.picked is auto.candidates[1] and picked.by == "keywords"
    nothing = classify("lorem ipsum dolor", auto, None)
    assert nothing.picked is None and "键值对" in nothing.reason
    empty = classify(HOTEL_TEXT, AutoChoice(()), None)
    assert empty.picked is None and empty.by == "none"


def test_model_choice_wins_and_zero_means_none():
    auto = candidates("cn_einvoice", "hotel_folio")
    asked = []

    def chooser(text, options):
        asked.append(options)
        return 0, "这是一张手写便条"

    result = classify(HOTEL_TEXT, auto, chooser)
    assert result.picked is None and result.reason == "这是一张手写便条" and result.by == "model"
    # 交给模型的是按关键字排好的：酒店账单排第一
    assert asked[0][0].startswith(load_starter("hotel_folio").title)
    out = classify(HOTEL_TEXT, auto, lambda text, options: (1, "酒店退房账单"))
    assert out.picked is not None and out.picked.template.name == "hotel_folio"
    assert out.to_json()["picked"]["title"] == out.picked.template.title
    # 模型答了不存在的编号：按关键字
    assert classify(HOTEL_TEXT, auto, lambda t, o: (9, "?")).by == "keywords"


class ChoosingBackend(SchemaBackend):
    """会选模板的假模型：选第 choice 个；能不能看图由 vision 决定。记下收到的消息。"""

    def __init__(self, values, choice: int, vision: bool = False) -> None:
        super().__init__(values)
        self.choice = choice
        self.vision = vision
        self.seen: list[list[ChatMessage]] = []

    def sees_images(self) -> bool:
        return self.vision

    def chat(self, messages, *, json_schema=None):
        self.seen.append(messages)
        assert json_schema is not None
        if "choice" in json_schema["properties"]:
            answer = {"choice": self.choice, "reason": "抬头写着 Folio，是酒店账单"}
            return ChatResult(json.dumps(answer), "fake-model", self.name, 0, 0, 1)
        return super().chat(messages, json_schema=json_schema)


def test_upload_with_auto_picks_a_template_per_document(client, factory, storage, monkeypatch):
    hotel = client.post(
        "/api/v1/templates", json={"source": "starter", "starter": "hotel_folio"}
    ).json()
    client.post("/api/v1/templates", json={"source": "starter", "starter": "cn_einvoice"})
    document = upload_file(client, template="auto", split="one")
    assert document["auto"] is True

    backend = ChoosingBackend(HOTEL, choice=1)
    pipeline = Pipeline(ocr=None, extractor=LlmExtractor(backend))  # type: ignore[arg-type]
    monkeypatch.setattr(pipeline, "read", lambda _path: hotel_pages()[:2])
    outcome = process_document(
        uuid.UUID(document["id"]), storage=storage, pipeline=pipeline, factory=factory
    )
    assert outcome == "done"
    [extraction] = detail(client, document["id"])["extractions"]
    assert extraction["template"] == hotel["id"]
    assert extraction["classified"]["picked"]["template_id"] == hotel["id"]
    assert extraction["classified"]["by"] == "model"
    assert extraction["template_title"] == hotel["title"]
    # 模板页：自动判断选中的也算"用过"
    listed = {t["id"]: t for t in client.get("/api/v1/templates").json()}
    assert listed[hotel["id"]]["used"] == 1

    # 重新处理不写模板：再自动判断一次；下一次上传默认也是自动判断
    redo = client.post(
        f"/api/v1/documents/{document['id']}/reprocess", json={"reason": "bad_result"}
    )
    assert redo.status_code == 202 and redo.json()["auto"] is True
    assert upload_file(client, data=b"other")["auto"] is True


def test_auto_without_a_fitting_template_extracts_all_pairs(client, factory, storage, monkeypatch):
    client.post("/api/v1/templates", json={"source": "starter", "starter": "cn_einvoice"})
    document = upload_file(client, template="auto", split="one")
    backend = ChoosingBackend(HOTEL, choice=0)
    pipeline = Pipeline(ocr=None, extractor=LlmExtractor(backend))  # type: ignore[arg-type]
    monkeypatch.setattr(pipeline, "read", lambda _path: hotel_pages()[:1])
    process_document(uuid.UUID(document["id"]), storage=storage, pipeline=pipeline, factory=factory)
    [extraction] = detail(client, document["id"])["extractions"]
    assert extraction["template"] == "discover"
    assert extraction["classified"]["picked"] is None


def blurry_page():
    return make_page(
        make_line("OYO Hotel Nanganallur", 10, 10, 300, 30, conf=0.62),
        make_line("Folio No. F-1OO", 10, 40, 300, 60, conf=0.6),
        make_line("Grand Total 1939", 10, 70, 300, 90, conf=0.7),
        make_line("Guest", 10, 100, 300, 120, conf=0.65),
        make_line("Room", 10, 130, 300, 150, conf=0.9),
    )


def test_when_to_look_at_the_picture():
    clear = hotel_pages()[0]
    assert weak_ocr(clear) is not None  # 只有 3 块字
    good = make_page(*(make_line(f"line {n}", 0, n * 20, 100, n * 20 + 10) for n in range(8)))
    assert weak_ocr(good) is None
    assert "平均置信度" in (weak_ocr(blurry_page()) or "")
    pdf = make_page(*blurry_page().lines)
    pdf = type(pdf)(pdf.width, pdf.height, pdf.lines, "pdf-text", {}, 0)
    assert weak_ocr(pdf) is None
    assert vision_reason([good], "auto") is None
    assert vision_reason([good], "always") is not None
    assert vision_reason([blurry_page()], "off") is None


def test_model_looks_at_the_picture_when_ocr_is_weak(client, factory, storage, monkeypatch):
    template_id = client.post(
        "/api/v1/templates", json={"source": "starter", "starter": "hotel_folio"}
    ).json()["id"]
    document = upload_file(client, template=template_id, split="one")
    # 模型在图上看清了账单号是 F-100（OCR 认成了 F-1OO）
    backend = ChoosingBackend(HOTEL, choice=1, vision=True)
    pipeline = Pipeline(ocr=None, extractor=LlmExtractor(backend), vision="auto")  # type: ignore[arg-type]
    monkeypatch.setattr(pipeline, "read", lambda _path: [blurry_page()])
    picture = np.zeros((40, 60, 3), dtype=np.uint8)
    monkeypatch.setattr(run_module, "page_images", lambda _path: [picture])
    process_document(uuid.UUID(document["id"]), storage=storage, pipeline=pipeline, factory=factory)

    [message] = [m for m in backend.seen[-1] if m.images]
    assert message.images[0].startswith(b"\x89PNG") and "附上了这张单据的原图" in message.content
    [extraction] = detail(client, document["id"])["extractions"]
    assert "平均置信度" in extraction["vision"]
    assert extraction["prompt_version"].endswith("+vision")
    folio = next(v for v in extraction["values"] if v["name"] == "folio_no")
    assert folio["status"] == "seen" and folio["current_value"] == "F-100"
    assert folio["needs_review"] and "看原图读的" in folio["note"]

    # 模型服务没有视觉模型：不给图
    backend = ChoosingBackend(HOTEL, choice=1, vision=False)
    pipeline = Pipeline(ocr=None, extractor=LlmExtractor(backend), vision="auto")  # type: ignore[arg-type]
    monkeypatch.setattr(pipeline, "read", lambda _path: [blurry_page()])
    redo = client.post(
        f"/api/v1/documents/{document['id']}/reprocess",
        json={"reason": "bad_result", "vision": "always"},
    )
    assert redo.json()["vision"] == "always"
    process_document(uuid.UUID(document["id"]), storage=storage, pipeline=pipeline, factory=factory)
    assert not any(m.images for msgs in backend.seen for m in msgs)
    [extraction] = detail(client, document["id"])["extractions"]
    assert extraction["vision"] is None


def test_images_are_sent_in_the_openai_format():
    backend = LlamaServerBackend()
    payload = backend._payload([ChatMessage("user", "看图", (b"\x89PNG",))], None)
    [message] = payload["messages"]  # type: ignore[misc]
    text, image = message["content"]
    assert text == {"type": "text", "text": "看图"}
    assert image["image_url"]["url"].startswith("data:image/png;base64,")


def test_mmproj_is_not_taken_for_the_model(tmp_path, monkeypatch):
    from jacara.core import config

    models = tmp_path / "models"
    models.mkdir()
    (models / "Qwen3.5-9B-Q4_K_M.gguf").write_bytes(b"")
    (models / "mmproj-F16.gguf").write_bytes(b"")
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    settings = config.Settings(jacara_model=None, jacara_mmproj=None)
    assert settings.find_model() == models / "Qwen3.5-9B-Q4_K_M.gguf"
    assert settings.find_mmproj() == models / "mmproj-F16.gguf"


# ---------- 核对出处：24 小时制的时间、少认了小数点的数字 ----------

from jacara.plugins.aligners import BlockAligner  # noqa: E402
from jacara.plugins.aligners.text_match import time_pattern  # noqa: E402
from jacara.plugins.extractors.base import ExtractedField, ExtractionResult  # noqa: E402
from jacara.structure.analyze import analyze_page  # noqa: E402


def test_time_in_12_hour_clock_matches_24_hour_value():
    pattern = time_pattern("20:13:39")
    assert pattern is not None
    assert pattern.search("25/12/2018 8:13:39 PM")
    assert pattern.search("20:13:39")
    assert not pattern.search("8:13:39 AM") and not pattern.search("8:13:39")
    short = time_pattern("14:05")
    assert short is not None and short.search("2:05:33 pm") and short.search("14:05")
    morning = time_pattern("08:13")
    assert morning is not None and morning.search("8:13 AM") and not morning.search("8:13 PM")
    assert time_pattern("Cashier") is None and time_pattern("25:00") is None


def scanned_receipt():
    """照着一张扫描的小票写的：找零 1.00 被手写的圈盖住了小数点，OCR 读成 100；
    圈里手写的 9.00 和它在同一高度。"""
    return make_page(
        make_line("Date:", 10, 100, 80, 120),
        make_line("25/12/2018 8:13:39 PM", 150, 100, 400, 120),
        make_line("Cash", 100, 300, 160, 320),
        make_line("10.00", 380, 300, 450, 320),
        make_line("CHANGE", 100, 330, 190, 350),
        make_line("100", 400, 330, 450, 352),
        make_line("9.00", 300, 335, 390, 400, conf=0.9),
    )


def number_of(structure, text):
    return next(n for n in range(1, len(structure.words) + 1) if structure.ocr_line(n).text == text)


def test_time_and_dotless_change_are_found():
    page = scanned_receipt()
    structure = analyze_page(page)
    fields = (
        ExtractedField(
            "time", "string", "20:13:39", blocks=(number_of(structure, "25/12/2018 8:13:39 PM"),)
        ),
        # 模型说找零在手写的 9.00 那块（说错了块），值是 1
        ExtractedField("change", "number", 1.0, blocks=(number_of(structure, "9.00"),)),
    )
    result = ExtractionResult(fields, "{}", "m", "b", "v", 0)
    time, change = BlockAligner().align(page, result, structure)
    assert time.status == "matched" and time.evidence is not None
    assert change.status == "fuzzy" and change.needs_review
    assert change.evidence is not None and change.evidence.lines[0].text == "100"
    assert "小数点" in change.note


# ---------- 审核时"让模型看这块" ----------


class LookingBackend:
    name = "fake"

    def __init__(self, sees: bool) -> None:
        self.sees = sees
        self.asked: list[ChatMessage] = []

    def sees_images(self) -> bool:
        return self.sees

    def chat(self, messages, *, json_schema=None):
        self.asked = messages
        answer = {"text": "CHANGE 1.00", "value": 1}
        return ChatResult(json.dumps(answer), "qwen-vl", self.name, 0, 0, 1)


def test_look_at_a_region(client):
    import io

    from PIL import Image

    from jacara.core.deps import get_model

    picture = io.BytesIO()
    Image.new("RGB", (400, 300), "white").save(picture, format="PNG")
    response = client.post(
        "/api/v1/documents", files={"file": ("r.png", picture.getvalue(), "image/png")}
    )
    document_id = response.json()["id"]
    app = client.app
    backend = LookingBackend(sees=False)
    app.dependency_overrides[get_model] = lambda: backend
    url = f"/api/v1/documents/{document_id}/pages/1/look-region"
    body = {"box": [100, 100, 200, 140], "label": "找零", "type": "number"}
    assert client.post(url, json=body).status_code == 503

    backend.sees = True
    out = client.post(url, json=body).json()
    assert out == {"text": "CHANGE 1.00", "value": "1", "model": "qwen-vl"}
    question = backend.asked[-1]
    assert question.images and question.images[0].startswith(b"\x89PNG")
    assert "找零（数字）" in question.content

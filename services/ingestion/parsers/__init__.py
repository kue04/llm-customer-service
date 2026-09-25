"""文档解析器（阶段 2）。

模块划分：
- ``base``      解析契约：``DocumentParser`` 协议、``ParsedDocument`` / ``Block``、
                结构化错误 ``ParserError``、模板基类 ``TemplateDocumentParser``
- ``plain_text`` / ``markdown`` / ``html`` / ``pdf`` / ``docx``  各格式实现
- ``ocr``       扫描件 OCR 接口（默认不装 ``rapidocr-onnxruntime``）

注册表在上一层：``services.ingestion.parser_registry``。
"""

from __future__ import annotations

__all__ = ["base", "docx", "html", "markdown", "ocr", "pdf", "plain_text"]

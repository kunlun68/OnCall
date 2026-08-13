"""向量重排服务模块 - 基于阿里云百炼 Rerank 模型

对向量检索召回的候选文档做二次精排（recall -> rerank -> top_k）。
使用 dashscope SDK 的 TextReRank 调用百炼 gte-rerank-v2 模型。

设计原则：**不降级**。重排失败时抛出 RerankError，绝不静默回退到
「仅向量检索」的未重排结果。
"""

from typing import List, Tuple

from dashscope import TextReRank
from langchain_core.documents import Document
from loguru import logger

from app.config import config

# 百炼 rerank 单次请求的文档数量上限（官方建议 100 以内，硬上限 200）
MAX_RERANK_DOCS = 100


class RerankError(Exception):
    """重排失败专用异常，用于与检索失败区分，实现"不降级"语义"""


class VectorRerankService:
    """向量重排服务 - 对召回文档按与 query 的相关性重新排序"""

    def __init__(self):
        """初始化重排服务"""
        self.model = config.dashscope_rerank_model
        self.api_key = config.dashscope_api_key
        logger.info(f"向量重排服务初始化完成, 模型: {self.model}")

    def rerank(
        self,
        query: str,
        documents: List[Document],
        top_n: int,
    ) -> List[Tuple[Document, float]]:
        """对文档列表执行重排，返回按相关性降序的 (文档, 相关分) 列表

        Args:
            query: 用户查询文本
            documents: 向量检索召回的候选文档列表
            top_n: 返回前 N 个最相关文档

        Returns:
            List[Tuple[Document, float]]: 按相关性分数降序排列的 (文档, 分数) 列表

        Raises:
            RerankError: 重排服务调用失败、返回异常状态或结果为空时抛出，
                绝不降级返回未重排的结果
        """
        # 空结果或单条结果无需重排（单条跳过调用是优化，不是降级）
        if not documents:
            return []
        if len(documents) == 1:
            return [(documents[0], 1.0)]

        # 防御：超过百炼上限时截断（正常配置 RAG_RECALL_K=20 不会触发）
        if len(documents) > MAX_RERANK_DOCS:
            logger.warning(
                f"候选文档数 {len(documents)} 超过上限 {MAX_RERANK_DOCS}, 已截断"
            )
            documents = documents[:MAX_RERANK_DOCS]

        texts = [doc.page_content for doc in documents]

        try:
            logger.info(
                f"调用重排模型 {self.model}, 候选文档数: {len(texts)}, top_n: {top_n}"
            )
            # 必须显式传 api_key（config 由 pydantic-settings 从 .env 读取，
            # 不写入 os.environ，SDK 默认读环境变量会取不到）
            resp = TextReRank.call(
                model=self.model,
                query=query,
                documents=texts,
                top_n=min(top_n, len(documents)),
                api_key=self.api_key,
            )
        except Exception as e:
            logger.error(f"重排服务调用异常: {e}")
            raise RerankError(f"重排服务调用失败: {e}") from e

        # SDK 在 HTTP 失败时不抛异常，只返回 status_code!=200 的响应。
        # 这里显式校验是「不降级」的唯一强制点：漏掉它会静默得到空结果。
        if resp.status_code != 200:
            logger.error(
                f"重排服务返回异常状态: status={resp.status_code}, "
                f"code={resp.code}, message={resp.message}, request_id={resp.request_id}"
            )
            raise RerankError(
                f"重排服务返回异常状态: {resp.status_code} - {resp.message}"
            )

        # results 已按相关性降序排列，按 index 映射回原始 Document
        ranked: List[Tuple[Document, float]] = []
        for result in resp.output.results:
            idx = result.index
            if not (0 <= idx < len(documents)):
                logger.error(f"重排结果索引越界: index={idx}, 文档数={len(documents)}")
                raise RerankError(f"重排结果索引越界: index={idx}")
            ranked.append((documents[idx], result.relevance_score))
            logger.debug(
                f"重排结果: index={idx}, score={result.relevance_score:.4f}"
            )

        if not ranked:
            logger.error("重排服务未返回任何有效结果")
            raise RerankError("重排服务未返回任何有效结果")

        logger.info(
            f"重排完成, request_id={resp.request_id}, 返回 {len(ranked)} 个文档"
        )
        return ranked


# 全局单例
vector_rerank_service = VectorRerankService()

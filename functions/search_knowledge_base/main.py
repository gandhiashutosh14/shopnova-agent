import functions_framework
import json

# In production: replace with Vertex AI Search / RAG pipeline
KNOWLEDGE_BASE = {
    "return policy": "ShopNova accepts returns within 30 days of delivery. "
                     "Items must be in original condition. Digital downloads are non-refundable.",
    "refund":        "Refunds are processed within 5-7 business days after receiving "
                     "the returned item. Refunds over $200 require manager approval.",
    "shipping":      "Standard shipping takes 5-7 business days. Express (2-3 days) "
                     "available for a fee. Free shipping on orders over $50.",
    "cancel order":  "Orders can be cancelled within 1 hour of placement. "
                     "After that, wait for delivery and initiate a return.",
    "damaged item":  "If you received a damaged item, contact support within "
                     "48 hours with photos. We will arrange a replacement or full refund.",
    "warranty":      "Most products carry a 1-year manufacturer warranty. "
                     "Extended warranties are available at checkout.",
}

@functions_framework.http
def search_knowledge_base(request):
    data = request.get_json(silent=True) or {}
    query = data.get("query", "").lower()

    if not query:
        return json.dumps({"error": "query required"}), 400, \
            {"Content-Type": "application/json"}

    results = []
    # First pass: match on topic keywords (high relevance)
    for topic, content in KNOWLEDGE_BASE.items():
        if any(w in query for w in topic.split()) or \
           any(w in topic for w in query.split()):
            results.append({"topic": topic, "content": content, "relevance": "high"})

    # Second pass: search inside content body (medium relevance)
    if not results:
        for topic, content in KNOWLEDGE_BASE.items():
            if any(w in content.lower() for w in query.split() if len(w) > 3):
                results.append({"topic": topic, "content": content, "relevance": "medium"})

    return json.dumps({
        "query": query,
        "results": results[:3],
        "found": bool(results)
    }), 200, {"Content-Type": "application/json"}
